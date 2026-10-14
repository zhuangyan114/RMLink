"""Real WinUSB/API ownership and one-device STA disconnect/reconnect test."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))

from rmlink_connector.client import ApiClient
from rmlink_swd import SwdSession
from rmlink_usb import UsbClient

def virtual():
    deadline = time.perf_counter() + 10
    while True:
        try: return UsbClient(bcd_device=0x100)
        except RuntimeError:
            if time.perf_counter() >= deadline: raise
            time.sleep(.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/logs/verify-connector-reconnect.json')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  method='USB vendor 80/05 disconnects only RMLink STA; PC hotspot stays on')
    api = None
    try:
        api = ApiClient()
        devices = api.request('GET', '/v1/devices')
        online = [d for d in devices if d['paired'] and d['connected']]
        assert len(online) == 1, 'Select exactly one connected development probe'
        key = report['deviceId'] = online[0]['device_id']
        with api.open(key), SwdSession(api, 2000000) as swd:
            assert swd.read32(0xe000ed00) == 0x411fc231
            with virtual() as c:
                assert c.exchange(b'\x02\x01') == b'\x02\x00'
                assert c.exchange(b'\x03') == b'\x03\xff'
            assert swd.read32(0xe000ed00) == 0x411fc231
        report['realVirtualExcludesApiOwner'] = True
        with virtual() as c, SwdSession(c, 2000000) as swd:
            with api.open(key):
                assert api.exchange(b'\x02\x01') == b'\x02\x00'
                assert api.exchange(b'\x03') == b'\x03\xff'
            assert swd.read32(0xe000ed00) == 0x411fc231
        report['realApiExcludesVirtualOwner'] = True
        api.open(key)
        swd = SwdSession(api, 2000000).start()
        assert swd.read32(0xe000ed00) == 0x411fc231
        begin = time.perf_counter()
        with UsbClient(bcd_device=0x0002, serial='50787D15A924') as physical:
            assert physical.exchange(b'\x80\x05') == b'\x80\x05\x00'
        deadline = begin + 30
        while True:
            try: api.exchange(b'\x00\xff')
            except HTTPError as exc:
                assert exc.code == 503
                report['oldSessionFailedMs'] = round((time.perf_counter()-begin)*1000, 2)
                break
            if time.perf_counter() > deadline: raise TimeoutError('Expected old TCP session to fail')
            time.sleep(.1)
        # The disconnected lease must never migrate silently onto the fresh broker.
        swd.connected = False
        while True:
            snapshot = api.request('GET', '/v1/status')
            ready = any(d['device_id'] == key and d['connected'] for d in snapshot['devices'])
            if ready and key in snapshot['driver']['managed_ports'] and not snapshot['mount_errors']:
                try:
                    with virtual() as c: assert c.exchange(b'\x00\xff') == b'\x00\x02' + c.packet_size.to_bytes(2, 'little')
                    break
                except (RuntimeError, OSError): pass
            if time.perf_counter() > deadline: raise TimeoutError('Automatic connection/virtual mount did not recover')
            time.sleep(.2)
        report['automaticVirtualRecoveryMs'] = round((time.perf_counter()-begin)*1000, 2)
        try:
            api.exchange(b'\x02\x01')
            raise AssertionError('Lost session was silently revived')
        except HTTPError as exc: assert exc.code == 503
        report['oldLeaseRemainsLost'] = True
        api.session = None
        with api.open(key), SwdSession(api, 2000000) as swd:
            report['newSessionCpuid'] = hex(swd.read32(0xe000ed00))
            assert report['newSessionCpuid'] == '0x411fc231'
        with virtual() as c, SwdSession(c, 2000000) as swd:
            report['remountedVirtualCpuid'] = hex(swd.read32(0xe000ed00))
            assert report['remountedVirtualCpuid'] == '0x411fc231'
        report['ownerAtEnd'] = api.request('GET', '/v1/status')['devices'][0]['owner']
        assert report['ownerAtEnd'] is None
        report['complete'] = True
    except BaseException as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        cleanup_error = None
        if api is not None:
            try:
                api.close()
            except BaseException as exc:
                cleanup_error = exc
                report['cleanupFailure'] = f'{type(exc).__name__}: {exc}'
                report['complete'] = False
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)
        if cleanup_error is not None and 'failure' not in report:
            raise RuntimeError('API session cleanup failed; inspect the saved report') from cleanup_error


if __name__ == '__main__': main()
