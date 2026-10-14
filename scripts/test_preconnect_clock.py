"""Real USB/TCP clock-before-Connect ownership acceptance, without Flash writes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from rmlink_swd import SwdSession
from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient


def run(host, port, serial):
    report = {'capturedAt': datetime.now().astimezone().isoformat(), 'complete': False}
    clock = b'\x11' + struct.pack('<I', 2000000)
    with UsbClient(bcd_device=2, serial=serial) as usb, TcpClient(host, port) as tcp:
        report['device'] = status(usb)
        assert report['device']['ownerTransport'] == 0, 'Target already owned; do not interrupt it'
        try:
            for invalid in (b'\x11', b'\x11' + bytes(4), b'\x11' + struct.pack('<I', 10000001)):
                assert tcp.exchange(invalid) == b'\x11\xff'
                assert status(usb)['ownerTransport'] == 0
            report['invalidTcpClockDoesNotClaim'] = True
            for owner, foreign, transport in ((usb, tcp, 1), (tcp, usb, 2)):
                assert owner.exchange(clock) == b'\x11\x00'
                reserved = status(usb)
                assert reserved['ownerTransport'] == transport and reserved['ownerSession']
                packets = [clock, b'\x02\x01', b'\x05\x00\x00', b'\x03']
                assert foreign.exchange_many(packets) == [b'\x11\xff', b'\x02\x00', b'\x05\x00\x08', b'\x03\xff']
                assert status(usb) == reserved
                with SwdSession(owner, clock=2000000) as target:
                    assert target.read32(0xe000ed00) == 0x411fc231
                assert status(usb)['ownerTransport'] == 0
                report['usb' if transport == 1 else 'tcp'] = {
                    'clockBeforeConnect': True, 'foreignClockConnectTransferDisconnectDenied': True,
                    'cpuid': '0x411fc231', 'releasedAfterDisconnect': True}
            assert tcp.exchange(clock) == b'\x11\x00'
            assert tcp.exchange(b'\x03') == b'\x03\x00'
            assert status(usb)['ownerTransport'] == 0
            report['disconnectBeforeConnectReleases'] = True
        finally:
            for client in (usb, tcp):
                client.exchange(b'\x03')  # Foreign disconnect is denied; the owner releases.
        report['ownerAfterTests'] = status(usb)
    # A transport close must also release a reservation made before Connect.
    with TcpClient(host, port) as tcp:
        assert tcp.exchange(clock) == b'\x11\x00'
    with UsbClient(bcd_device=2, serial=serial) as usb:
        deadline = time.monotonic() + 3
        while status(usb)['ownerTransport']:
            assert time.monotonic() < deadline, 'TCP close did not release target reservation'
            time.sleep(.02)
    report.update(complete=True, tcpCloseBeforeConnectReleases=True,
                  evidence='Physical STM32 CPUID reads and USB/TCP session arbitration; no target Flash/RAM writes or reset')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--serial', default='50787D15A924')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.host, args.port, args.serial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
