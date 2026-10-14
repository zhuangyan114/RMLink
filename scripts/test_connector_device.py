"""Real RMLink/STM32 through local API and an independent USB/IP wire client.

This tests USB/IP data without claiming Windows virtual enumeration. That is a
separate test after the reviewed virtual USB driver has been installed.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))

from rmlink_connector.client import ApiClient
from rmlink_connector.usbip import OP, URB
from rmlink_swd import SwdSession
from rmlink_protocol import packet_size_from_info


class WireUsbClient:
    def __init__(self, device_id, port=3240):
        self.packet_size = 64
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.sock.sendall(OP.pack(0x111, 0x8003, 0) + ('rmlink-' + device_id).encode().ljust(32, b'\0'))
        if self.read(8) != OP.pack(0x111, 3, 0): raise ConnectionError('USB/IP import denied')
        self.read(312)
        self.seq, self.pending, self.ins, self.responses = 0, {}, [], {}
        self.packet_size = packet_size_from_info(self.exchange(b'\x00\xff'))
    def read(self, count):
        result = bytearray()
        while len(result) < count:
            data = self.sock.recv(count - len(result))
            if not data: raise ConnectionError('USB/IP stream closed')
            result.extend(data)
        return bytes(result)
    def submit(self, direction, data=b''):
        self.seq += 1; seq = self.seq
        self.pending[seq] = direction
        if direction: self.ins.append(seq)
        size = self.packet_size
        self.sock.sendall(URB.pack(1, seq, 0x10001, direction, 3, 0, size, 0, 0, 0, bytes(8)) +
                          (data.ljust(size, b'\0') if not direction else b''))
    def exchange_many(self, requests):
        if not 1 <= len(requests) <= 8 or any(not 1 <= len(p) <= self.packet_size for p in requests):
            raise ValueError('Invalid negotiated DAP batch')
        for request in requests: self.submit(0, request); self.submit(1)
        return [self.receive() for _ in requests]
    def exchange(self, request): return self.exchange_many([request])[0]
    def receive(self):
        wanted = self.ins[0]
        while wanted not in self.responses:
            h = URB.unpack(self.read(48))
            if h[0] != 3 or h[1] not in self.pending: raise ValueError('Unexpected URB response')
            direction = self.pending.pop(h[1])
            if h[5] or not 0 <= h[6] <= self.packet_size: raise ConnectionError(f'URB status {h[5]}')
            if direction: self.responses[h[1]] = self.read(h[6])
        self.ins.pop(0); return self.responses.pop(wanted)
    def abort_transfer(self): self.submit(0, b'\x07')
    def close(self): self.sock.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    api = ApiClient()
    deadline = time.perf_counter() + 15
    devices = []
    while not devices and time.perf_counter() < deadline:
        devices = api.request('GET', '/v1/devices')
        if not devices: time.sleep(.2)
    if len(devices) != 1: raise RuntimeError('Expected one discovered development probe')
    key = devices[0]['device_id']
    api.request('POST', '/v1/pair', {'device_id': key})
    report = {'capturedAt': datetime.now().astimezone().isoformat(), 'complete': False, 'device_id': key,
              'evidence': 'Physical WiFi target via API and USB/IP wire; Windows virtual USB enumeration tested separately'}
    try:
        with api.open(key), SwdSession(api, 2000000) as s:
            report['apiCpuid'] = hex(s.read32(0xe000ed00))
            if report['apiCpuid'] != '0x411fc231': raise AssertionError('API target mismatch')
            with WireUsbClient(key) as virtual:
                if virtual.exchange(b'\x02\x01') != b'\x02\x00': raise AssertionError('Virtual USB stole API owner')
                if virtual.exchange(b'\x03') != b'\x03\xff': raise AssertionError('Foreign disconnect accepted')
                if s.read32(0xe000ed00) != 0x411fc231: raise AssertionError('API owner changed')
            report['apiExcludesVirtual'] = True
        # Import cleanup is asynchronous; wait until the previous import closes.
        time.sleep(.2)
        with WireUsbClient(key) as virtual, SwdSession(virtual, 2000000) as s:
            report['usbipWireCpuid'] = hex(s.read32(0xe000ed00))
            if report['usbipWireCpuid'] != '0x411fc231': raise AssertionError('USB/IP target mismatch')
            other = ApiClient().open(key)
            try:
                if other.exchange(b'\x02\x01') != b'\x02\x00': raise AssertionError('API stole virtual owner')
                if other.exchange(b'\x03') != b'\x03\xff': raise AssertionError('Foreign disconnect accepted')
            finally: other.close()
            for _ in range(25):
                if virtual.exchange_many([b'\x00\xff', b'\x00\xfe'] * 4) != [b'\x00\x02' + virtual.packet_size.to_bytes(2, 'little'), b'\x00\x01\x08'] * 4:
                    raise AssertionError('Virtual USB pipeline ordering/content')
            report['virtualExcludesApi'] = True
            report['pipelineResponsesChecked'] = 200
        report['ownerAtEnd'] = api.request('GET', '/v1/status')['devices'][0]['owner']
        if report['ownerAtEnd'] is not None: raise AssertionError('Owner leaked')
        report['complete'] = True
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
