"""Physical target session/error tests, without target RAM/Flash writes."""
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from rmlink_usb import UsbClient
from rmlink_tool import TcpClient, status
from rmlink_swd import SwdSession

ROOT = Path(__file__).resolve().parents[1]
HOST = 'rmlink-15a924.local'
MATCH = b'\x05\x00\x01\x12' + struct.pack('<I', 0)


def prepare_match(s):
    s.command(b'\x04\x02\x64\x00\xff\xff', b'\x04\x00')
    s.transfer([(0x20, 0xffffffff)])


def main():
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False)
    try:
        with UsbClient() as usb, SwdSession(usb, 100000) as s:
            report['device'] = status(usb)
            prepare_match(s)
            with TcpClient(HOST, 3241) as foreign:
                begin = time.perf_counter(); usb.send(MATCH); time.sleep(.08)
                foreign.abort_transfer()
                reply = usb.receive(); ms = (time.perf_counter() - begin) * 1000
                assert reply == b'\x05\x00\x11' and 900 <= ms < 1800, (reply.hex(), ms)
                assert status(usb)['ownerTransport'] == 1
                report['foreignAbortIgnored'] = dict(elapsedMs=round(ms, 2), response=reply.hex())
            s.command(b'\x04\x02\x64\x00\x00\x00', b'\x04\x00')
            # Intentionally invalid wire sequence, expecting physical NO_ACK.
            assert usb.exchange(b'\x1d\x02\x08\x00\x88')[:2] == b'\x1d\x00'
            reply = usb.exchange(b'\x05\x00\x01\x02')
            assert reply == b'\x05\x00\x07', reply.hex()
            for bits, data in ((56, b'\xff' * 7), (16, b'\x9e\xe7'), (56, b'\xff' * 7), (8, b'\x00')):
                s.command(bytes([0x12, bits]) + data, b'\x12\x00')
            assert s.dp_read(0) == s.idcode  # DPIDR is the first access after line reset.
            s.dp_write(0, 0x1e); s.dp_write(8, 0)
            assert s.read32(0xe000ed00) == 0x411fc231
            report['noAckRecovery'] = dict(response=reply.hex(), recoveryCpuid='0x411FC231')
        with TcpClient(HOST, 3241) as tcp:
            s = SwdSession(tcp, 100000).start()
            prepare_match(s)
            tcp.send(MATCH)
            # Queue several real DAP requests behind the long transfer, then drop.
            for request in (b'\x02\x01', b'\x08\x00\x1e\x00\x00\x00', b'\x00\xff'):
                tcp.send(request)
            time.sleep(.08)
            begin = time.perf_counter(); tcp.close()
            with UsbClient() as usb:
                deadline = begin + 2
                while status(usb)['ownerTransport']:
                    assert time.perf_counter() < deadline, 'TCP owner did not release on close'
                    time.sleep(.01)
                elapsed = (time.perf_counter() - begin) * 1000
                assert elapsed < 700, elapsed
                with SwdSession(usb, 2000000) as recovered:
                    assert recovered.read32(0xe000ed00) == 0x411fc231
                report['closeCancelsAndDrains'] = dict(queuedRequests=3, ownerReleaseMs=round(elapsed, 2),
                                                        usbReclaimed=True)
            s.connected = False  # Closed transport; never send to an uncertain old socket.
        bad = []
        for magic, length, kind, reserved in ((b'BAD\0', 2, 1, 0), (b'DAP\0', 65, 1, 0),
                                            (b'DAP\0', 0, 1, 0), (b'DAP\0', 2, 2, 0),
                                            (b'DAP\0', 2, 1, 1)):
            with TcpClient(HOST, 3241) as c:
                c.sock.sendall(struct.pack('<4sHBB', magic, length, kind, reserved))
                try:
                    result = c.sock.recv(1)
                    assert not result, result
                except ConnectionResetError: pass
            bad.append([magic.decode('ascii').replace('\0', ''), length, kind, reserved])
        report['invalidHeadersClosed'] = bad
        with TcpClient(HOST, 3241) as c:
            c.sock.sendall(b'DAP')
            begin = time.perf_counter()
            try: assert not c.sock.recv(1)
            except ConnectionResetError: pass
            report['partialHeaderTimeoutMs'] = round((time.perf_counter() - begin) * 1000, 2)
        # Order/content regression on both asynchronous transports.
        for label in ('USB', 'WiFi'):
            with (UsbClient() if label == 'USB' else TcpClient(HOST, 3241)) as c:
                for _ in range(20):
                    assert c.exchange_many([b'\x00\xff', b'\x00\xfe'] * 4) == [b'\x00\x02\x40\x00', b'\x00\x01\x08'] * 4
                assert not status(c)['ownerTransport']
        report['pipelineResponsesCheckedPerTransport'] = 160
        report['complete'] = True
    finally:
        (ROOT / 'artifacts/logs/verify-target-exceptions.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
