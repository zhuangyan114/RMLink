"""RMLink development protocol tool. Passwords are prompted locally, never printed."""
import argparse
import getpass
import json
import socket
import struct
import time


class TcpClient:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    def read(self, count):
        data = bytearray()
        while len(data) < count:
            piece = self.sock.recv(count - len(data))
            if not piece: raise ConnectionError('TCP disconnected; request completion may be unknown')
            data.extend(piece)
        return bytes(data)
    def exchange(self, request):
        if bytes(request)[:1] == b'\x07':
            raise ValueError('TransferAbort has no response; use abort_transfer()')
        return self.exchange_many([request])[0]
    def send(self, request):
        request = bytes(request)
        if not 0 < len(request) <= 64: raise ValueError('DAP packet must be 1..64 bytes')
        self.sock.sendall(struct.pack('<4sHBB', b'DAP\0', len(request), 1, 0) + request)
    def receive(self): return self._response()
    def abort_transfer(self): self.send(b'\x07')
    def exchange_many(self, requests):
        requests = [bytes(request) for request in requests]
        if not 1 <= len(requests) <= 8: raise ValueError('DAP pipeline must contain 1..8 packets')
        if any(not 0 < len(request) <= 64 for request in requests):
            raise ValueError('DAP packet must be 1..64 bytes')
        if any(request[0] == 7 for request in requests):
            raise ValueError('TransferAbort has no response; use abort_transfer()')
        # Validate the whole batch before sending; never replay after an uncertain failure.
        frames = b''.join(struct.pack('<4sHBB', b'DAP\0', len(request), 1, 0) + request for request in requests)
        self.sock.sendall(frames)
        return [self._response() for _ in requests]
    def _response(self):
        magic, size, kind, reserved = struct.unpack('<4sHBB', self.read(8))
        if magic != b'DAP\0' or kind != 2 or reserved or not 0 < size <= 64:
            raise ValueError('Malformed CMSIS-DAP TCP response')
        return self.read(size)
    def close(self): self.sock.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def status(client):
    r = client.exchange(b'\x80\x00')
    if len(r) != 17 or r[:4] != b'\x80\x00\x00\x01': raise ValueError('Not a supported RMLink device')
    return dict(version=f'{r[4]}.{r[5]}.{r[6]}', capabilities=int.from_bytes(r[7:9], 'little'),
                targetPinsConfirmed=bool(r[9]), ownerTransport=r[10], ownerSession=int.from_bytes(r[11:15], 'little'),
                packetSize=int.from_bytes(r[15:17], 'little'))


def verify(client):
    result = status(client)
    expected = {0xff: b'\x00\x02\x40\x00', 0xfe: b'\x00\x01\x08'}
    for key, value in expected.items():
        actual = client.exchange(bytes([0, key]))
        if actual != value: raise AssertionError(f'DAP_Info {key:#x}: {actual.hex()}')
    caps = client.exchange(b'\x00\xf0')
    if len(caps) != 4 or caps[0:2] != b'\x00\x02' or caps[2] & 0x10:
        raise AssertionError('Invalid or falsely advertised atomic capability')
    if client.exchange(b'\x06\x00\xff\xff\x02') != b'\x06\x00\x00\x08':
        raise AssertionError('Oversized TransferBlock not rejected')
    if not result['targetPinsConfirmed'] and client.exchange(b'\x02\x01') != b'\x02\x00':
        raise AssertionError('Unconfigured board unexpectedly enabled target pins')
    begin = time.perf_counter()
    for _ in range(100):
        if client.exchange(b'\x00\xff') != expected[0xff]: raise AssertionError('Repeated query failed')
    result.update(queries=100, elapsedMs=round((time.perf_counter() - begin) * 1000, 2),
                  evidence='device protocol only; not target SWD or throughput acceptance')
    return result


def provision_credentials(client, ssid, password):
    ssid = ssid.encode('utf-8')
    password = password.encode('utf-8')
    if not 1 <= len(ssid) <= 32 or not 8 <= len(password) <= 63: raise ValueError('SSID: 1..32 bytes; password: 8..63 bytes')
    for field, data in enumerate((ssid, password)):
        for offset in range(0, len(data), 48):
            chunk = data[offset:offset + 48]
            command = bytes([0x80, 2, field, offset, len(data), len(chunk)]) + chunk
            if client.exchange(command) != b'\x80\x02\x00': raise RuntimeError('Credential fragment rejected')
    if client.exchange(b'\x80\x03') != b'\x80\x03\x00': raise RuntimeError('Credential save failed')
    return {'saved': True, 'credentialsPrinted': False}


def provision(client):
    return provision_credentials(client, input('2.4GHz hotspot SSID: '), getpass.getpass('Hotspot password: '))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('info', 'verify', 'provision', 'network'))
    parser.add_argument('--host', help='Development TCP endpoint; omit to use USB')
    parser.add_argument('--port', type=int, default=3241)
    args = parser.parse_args()
    if args.command == 'provision' and args.host: parser.error('Provisioning is USB only')
    if args.host: client = TcpClient(args.host, args.port)
    else:
        from rmlink_usb import UsbClient
        client = UsbClient()
    with client:
        if args.command == 'info': result = status(client)
        elif args.command == 'verify': result = verify(client)
        elif args.command == 'provision': result = provision(client)
        else:
            r = client.exchange(b'\x80\x04')
            if len(r) != 10 or r[:3] != b'\x80\x04\x00': raise ValueError('Network query failed')
            result = dict(configured=bool(r[3]), connected=bool(r[4]), rssi=struct.unpack('b', r[5:6])[0], ip=socket.inet_ntoa(r[6:10]))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
