"""Validate new transport diagnostic controls without claiming or accessing SWD."""
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import time

from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient


def expect(client, request, reply):
    actual = client.exchange(request)
    if actual != reply:
        raise AssertionError(f'{request.hex()}: {actual.hex()} != {reply.hex()}')


def main():
    host = 'rmlink-15a924.local'
    report = {'capturedAt': datetime.now().astimezone().isoformat()}
    with UsbClient() as usb:
        before = status(usb)
        if before['ownerTransport']:
            raise AssertionError('Stop target sessions before transport-only diagnostics')
        network = usb.exchange(b'\x80\x04')
        if len(network) != 10 or network[:3] != b'\x80\x04\x00' or not network[4]:
            raise AssertionError('Wi-Fi is not connected')
        device_ip = socket.inet_ntoa(network[6:10])
        expect(usb, b'\x80\x08\x2d\x01', b'\x80\x08\x01')  # 301 seconds is out of range.
        with TcpClient(host, 3241) as tcp:
            expect(tcp, b'\x80\x08\x01\x00', b'\x80\x08\x02')
            expect(tcp, b'\x80\x07', b'\x80\x07\x01')
        expect(usb, b'\x80\x08\x02\x00', b'\x80\x08\x00')
        time.sleep(0.3)
        bad_headers = [struct.pack('<4sIII', b'BAD1', 1, 1, 0),
                       struct.pack('<4sIII', b'RMB1', 1, 16 * 1024 * 1024 + 1, 0),
                       struct.pack('<4sIII', b'RMB1', 2, 1025, 0)]
        for header in bad_headers:
            with socket.create_connection((host, 3242), timeout=3) as sock:
                sock.sendall(header)
                try:
                    data = sock.recv(64)
                except ConnectionResetError:
                    data = b''
                if data:
                    raise AssertionError('Invalid raw request was processed')
        time.sleep(2.2)
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex((device_ip, 3242)) == 0:
                raise AssertionError('Raw benchmark remained open after its expiry')
        expect(usb, b'\x80\x08\x00\x00', b'\x80\x08\x00')
        after = status(usb)
        if after['ownerTransport']:
            raise AssertionError('Transport diagnostic claimed a target session')
    report.update(device=after, invalidDurationRejected=True, tcpArmingRejected=True,
                  shortDiagnosticRejected=True, rawInvalidHeadersRejected=len(bad_headers),
                  automaticExpiryVerified=True, targetOwnerZero=True,
                  evidence='ESP32 diagnostic bounds and automatic port closure only; no STM32 access')
    output = Path(__file__).resolve().parents[1] / 'artifacts/logs/verify-transport-bench.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
