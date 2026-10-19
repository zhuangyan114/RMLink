"""Explicitly authorized real RMLink OTA tests; evidence stays in artifacts/."""
import argparse
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'host'), str(ROOT/'scripts')]
from rmlink_connector.client import ApiClient, RMLinkError
from rmlink_connector.firmware import inspect_image
from rmlink_swd import SwdSession


class Checks:
    def __init__(self, args):
        self.args = args
        self.api = ApiClient()
        self.path = f'/v1/devices/{args.device}/firmware'
        self.report = dict(started_at=datetime.now().astimezone().isoformat(), case=args.case, passed=False, checks=[])

    def save(self):
        self.args.output.write_text(json.dumps(self.report, indent=2)+'\n', encoding='utf-8')

    def check(self, name, data):
        self.report['checks'].append(dict(name=name, result=data))
        self.save()
        print(json.dumps(dict(check=name, passed=True)), flush=True)

    def info(self):
        return self.api.request('GET', self.path)

    def wait(self, elf, timeout=140):
        deadline = time.monotonic()+timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.info()
                if last.get('connected') and last.get('elf_sha256') == elf and last.get('boot_state') == 2 and last.get('state') == 'idle':
                    return last
            except (OSError, RMLinkError):
                pass
            time.sleep(1)
        raise AssertionError('Expected image not confirmed: '+str(last))

    def begin(self, image, **changes):
        body = dict(action='begin', size=image['bytes'], sha256=image['sha256'],
                    profile=image['profile'], elf_sha256=image['elf_sha256'])
        body.update(changes)
        return self.api.request('POST', self.path, body)['upload_id']

    def post(self, action, upload, **values):
        return self.api.request('POST', self.path, dict(action=action, upload_id=upload, **values))

    def write(self, upload, data, offset=0):
        for start in range(0, len(data), 4096):
            chunk = data[start:start+4096]
            result = self.post('write', upload, offset=offset+start, data=base64.b64encode(chunk).decode())
            assert result['received'] == offset+start+len(chunk)

    def rejected(self, name, call):
        try:
            call()
        except (RMLinkError, ConnectionError) as error:
            self.check(name, dict(rejected=True, message=str(error)))
            return
        raise AssertionError(name+' was accepted')

    def target_digest(self):
        with self.api.open(self.args.device):
            with SwdSession(self.api) as swd:
                raw = struct.pack('<16384I', *swd.read_words(0x08000000, 16384, pipelined=True))
                return dict(flash_sha256=hashlib.sha256(raw).hexdigest(), cpuid=hex(swd.read32(0xe000ed00)),
                            halted=bool(swd.read32(0xe000edf0) & (1<<17)))

    def update(self, image_path, transport, **options):
        assert self.api.set_transport(self.args.device, transport)['connected']
        before = self.info()
        command = [str(self.args.exe), 'firmware', 'update', str(image_path), '--device', self.args.device, '--yes', '--json']
        if options.get('no_wait'):
            command.append('--no-wait')
        started = time.monotonic()
        process = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=240)
        result = json.loads(process.stdout)
        assert process.returncode == 0, result
        after = self.info() if options.get('no_wait') else self.wait(inspect_image(image_path)['elf_sha256'])
        self.check('update-'+transport, dict(before=before, after=after, image=inspect_image(image_path),
                    result=result, elapsed_seconds=round(time.monotonic()-started, 3)))
        return result

    def cycle(self):
        before = self.target_digest()
        self.check('target-before', before)
        for path, transport in ((self.args.old, 'usb'), (self.args.image, 'wifi'),
                                (self.args.previous, 'wifi'), (self.args.image, 'usb')):
            self.update(path, transport)
        after = self.target_digest()
        assert after['flash_sha256'] == before['flash_sha256'] and not after['halted']
        self.check('target-unchanged', after)

    def negative(self):
        before = self.info()
        image = inspect_image(self.args.image)
        data = Path(self.args.image).read_bytes()
        with self.api.open(self.args.device):
            self.rejected('lease-blocks-begin', lambda: self.begin(image))
        self.rejected('wrong-profile', lambda: self.begin(image, profile='0'*16))
        self.rejected('oversized', lambda: self.begin(image, size=4*1024*1024))
        for size in (128, 4096, 32768):
            upload = self.begin(image)
            try:
                self.write(upload, data[:size])
                self.rejected('offset-rejected-'+str(size), lambda: self.post('write', upload, offset=0, data='AA=='))
                self.rejected('transport-change-blocked-'+str(size), lambda: self.api.set_transport(self.args.device, 'wifi'))
                self.rejected('shutdown-blocked-'+str(size), lambda: self.api.request('POST','/v1/shutdown'))
            finally:
                self.post('abort', upload)
            assert self.info()['elf_sha256'] == before['elf_sha256'] and self.info()['state'] == 'idle'
            self.check('partial-abort-'+str(size), dict(bytes=size, running_elf_unchanged=True))
        upload = self.begin(image, size=352, sha256='0'*64)
        self.write(upload, data[:352])
        self.rejected('bad-digest', lambda: self.post('finish', upload))
        assert self.info()['state'] == 'idle'
        bad = bytearray(data)
        bad[-1] ^= 1
        upload = self.begin(image, sha256=hashlib.sha256(bad).hexdigest())
        self.write(upload, bad)
        self.rejected('invalid-esp-image-with-correct-upload-sha', lambda: self.post('finish', upload))
        assert self.info()['state'] == 'idle'
        prefix = bytearray(data[:352])
        prefix[80] ^= 1  # Wrong app project, even though chip/size/digest are plausible.
        upload = self.begin(image, size=352, sha256=hashlib.sha256(prefix).hexdigest())
        self.rejected('device-rejects-wrong-project', lambda: self.write(upload, prefix))
        assert self.info()['state'] == 'idle'
        self.check('negative-final', self.wait(before['elf_sha256']))

    def serial_port(self):
        import serial
        port = serial.Serial()
        port.port, port.baudrate, port.timeout = self.args.port, 115200, .15
        port.dtr, port.rts = False, False
        port.open()
        return port

    def reset_upload(self):
        before = self.info()
        image = inspect_image(self.args.image)
        upload = self.begin(image)
        self.write(upload, Path(self.args.image).read_bytes()[:32768])
        with self.serial_port() as port:
            port.rts = True
            time.sleep(.12)
            port.rts = False
        self.check('reset-during-upload', dict(uploaded_bytes=32768, reset='CH343 RTS CPU reset; not a power cut'))
        after = self.wait(before['elf_sha256'])
        assert after['slot'] == before['slot']
        self.check('old-image-survives-reset', after)
        # The connector expires the abandoned maintenance transaction without replay.
        deadline = time.monotonic()+130
        while True:
            try:
                recovery = self.begin(image)
                self.post('abort', recovery)
                break
            except RMLinkError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        self.check('maintenance-expiry-recovers', dict(new_upload_possible=True))

    def rollback(self):
        before = self.info()
        data = bytearray()
        port = self.serial_port()
        stop = threading.Event()
        def read():
            while not stop.is_set():
                try:
                    data.extend(port.read(4096))
                except OSError:
                    break
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            result = self.update(self.args.failure_image, 'usb', no_wait=True)
            assert result['state'] == 'rebooting'
            time.sleep(8)
            after = self.wait(before['elf_sha256'])
            assert after['slot'] == before['slot']
        finally:
            stop.set();thread.join(timeout=1);port.close()
            self.args.output.with_suffix('.serial.log').write_bytes(data)
        assert b'TEST_ONLY_FORCED_RESTART_BEFORE_OTA_CONFIRM' in data
        self.check('first-boot-reset-rolls-back', dict(before=before, after=after,
                    injected_reset_observed=True, valid_running_image_restored=True,
                    serial_log=str(self.args.output.with_suffix('.serial.log'))))



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-device-write', action='store_true', required=True)
    parser.add_argument('--case', choices=('cycle','negative','reset-upload','rollback'), required=True)
    parser.add_argument('--device', default='50787d15a924')
    parser.add_argument('--port', default='COM13')
    parser.add_argument('--exe', type=Path, default=Path.home()/'.rmlink/app/bin/rmlink.exe')
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--old', type=Path)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--failure-image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve the old evidence; choose a new output file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checks = Checks(args)
    try:
        getattr(checks, args.case.replace('-','_'))()
        checks.report['passed'] = True
    except BaseException as error:
        checks.report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        checks.report['finished_at'] = datetime.now().astimezone().isoformat()
        checks.save()


if __name__ == '__main__':
    main()
