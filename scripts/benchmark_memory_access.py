"""Interleaved read-only legacy/optimized MEM-AP access comparison."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from rmlink_swd import SwdSession
from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient


class CountedClient:
    def __init__(self, client):
        self.client = client
        self.reset()

    def reset(self):
        self.packets = self.batches = self.request_bytes = self.response_bytes = 0

    @property
    def packet_size(self): return getattr(self.client, 'packet_size', 64)

    def exchange(self, packet):
        return self.exchange_many([packet])[0]

    def exchange_many(self, packets):
        self.packets += len(packets)
        self.batches += 1
        self.request_bytes += sum(map(len, packets))
        responses = self.client.exchange_many(packets)
        self.response_bytes += sum(map(len, responses))
        return responses


def legacy_read(client, address, count, pipelined):
    """457f1e2 algorithm: reconfigure TAR before every <=15-word read."""
    words = []
    while count:
        requests, sizes = [], []
        for _ in range(4 if pipelined else 1):
            if not count: break
            size = min(count, 15, (0x400 - (address & 0x3ff)) // 4)
            requests.extend([b'\x05\x00\x01\x05' + struct.pack('<I', address),
                             b'\x06\x00' + struct.pack('<H', size) + b'\x0f'])
            sizes.append(size)
            address += size * 4
            count -= size
        replies = client.exchange_many(requests) if pipelined else [client.exchange(r) for r in requests]
        if len(replies) != len(requests): raise RuntimeError('Legacy batch response count')
        for i, size in enumerate(sizes):
            tar, reply = replies[2*i:2*i+2]
            if tar != b'\x05\x01\x01' or reply[:4] != bytes([6, size, 0, 1]) or len(reply) != 4 + size*4:
                raise RuntimeError('Legacy block read response')
            words.extend(struct.unpack('<' + 'I'*size, reply[4:]))
    return words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=['USB', 'TCP', 'VirtualUSB'], required=True)
    parser.add_argument('--host', help='Required for direct TCP; connector must have released its TCP connection')
    parser.add_argument('--bytes', type=int, default=4096)
    parser.add_argument('--offset', type=lambda value: int(value, 0), default=0)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--clock', type=int, default=4000000)
    parser.add_argument('--pipeline-only', action='store_true')
    parser.add_argument('--optimized-only', action='store_true', help='Capacity sweep with the same safe addressed algorithm')
    parser.add_argument('--compare-capacity', type=int, choices=[64,128,256],
                        help='Interleave a smaller memory packet budget on the same negotiated transport')
    parser.add_argument('--expected-image', type=Path, default=ROOT / 'artifacts/build/stm32-fixture/fixture-64k.bin')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.optimized_only and args.compare_capacity: parser.error('Choose one comparison mode')
    algorithms = (('limited', 'optimized') if args.compare_capacity else
                  ('optimized',) if args.optimized_only else ('legacy', 'optimized'))
    if args.bytes <= 0 or args.bytes % 4 or args.offset < 0 or args.offset % 4 or args.offset + args.bytes > 65536:
        parser.error('Aligned Flash slice must fit the confirmed 64 KiB target')
    if args.samples < 3: parser.error('At least 3 interleaved repetitions required')
    if args.transport == 'TCP' and not args.host: parser.error('--host required for TCP')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  transport=args.transport, bytes=args.bytes, address=hex(0x08000000 + args.offset),
                  clockRequestedHz=args.clock, repetitions=args.samples, rows=[],
                  scope='Flash read-only; host SWD setup, no halt/reset/erase/program; no write replay')
    try:
        backup = args.expected_image.read_bytes()
        expected = backup[args.offset:args.offset + args.bytes]
        if len(expected) != args.bytes: raise ValueError('Backup slice missing')
        report['expectedSha256'] = hashlib.sha256(expected).hexdigest()
        if args.transport != 'TCP':
            snapshot = ApiClient().request('GET', '/v1/status')
            dev = next(d for d in snapshot['devices'] if d['device_id'] == '50787d15a924')
            if dev['owner'] is not None: raise RuntimeError('Connector target is occupied')
            report['connectorVersion'] = snapshot['version']
        if args.transport == 'TCP': client = TcpClient(args.host, 3241)
        else: client = UsbClient(serial='50787D15A924' if args.transport == 'USB' else 'RM50787D15A924',
                                 bcd_device=2 if args.transport == 'USB' else 0x100)
        with client as raw:
            report['device'] = status(raw)
            report['packetSize'] = raw.packet_size
            if report['device']['ownerTransport']: raise RuntimeError('Board target is occupied')
            counted = CountedClient(raw)
            if args.compare_capacity and args.compare_capacity >= raw.packet_size:
                raise ValueError('Comparison budget must be smaller than negotiated capacity')
            with SwdSession(counted, args.clock) as target:
                if target.read32(0xe000ed00) != 0x411fc231: raise RuntimeError('Expected Cortex-M3')
                report['coreHaltedBefore'] = bool(target.read32(0xe000edf0) & 0x20000)
                def read(name, pipelined):
                    if name == 'legacy': return legacy_read(counted, 0x08000000 + args.offset, args.bytes//4, pipelined)
                    target.packet_size = args.compare_capacity if name == 'limited' else raw.packet_size
                    return target.read_words(0x08000000 + args.offset, args.bytes//4, pipelined)
                for pipelined in ([True] if args.pipeline_only else [False, True]):
                    # Warm up outside timed samples, then alternate the execution order.
                    for name in algorithms:
                        values = read(name, pipelined)
                        if struct.pack('<' + 'I'*len(values), *values) != expected: raise AssertionError('Warm-up data mismatch')
                    rows = {name:dict(algorithm=name, memoryPacketBudget=args.compare_capacity if name == 'limited' else raw.packet_size,
                                      mode='pipeline' if pipelined else 'sequential',
                                      elapsedMs=[], dapPackets=[], batches=[], requestBytes=[], responseBytes=[]) for name in algorithms}
                    for iteration in range(args.samples):
                        for name in (algorithms if iteration % 2 == 0 else reversed(algorithms)):
                            counted.reset()
                            begin = time.perf_counter()
                            values = read(name, pipelined)
                            data = struct.pack('<' + 'I'*len(values), *values)
                            elapsed = (time.perf_counter() - begin)*1000
                            if data != expected: raise AssertionError('Measured read mismatch')
                            row = rows[name]
                            row['elapsedMs'].append(round(elapsed, 3))
                            for key, value in (('dapPackets', counted.packets), ('batches', counted.batches),
                                               ('requestBytes', counted.request_bytes), ('responseBytes', counted.response_bytes)):
                                row[key].append(value)
                    for row in rows.values():
                        row['medianKiBPerSecond'] = round(args.bytes/1024*1000/statistics.median(row['elapsedMs']), 3)
                        row['contentMatchesBackup'] = True
                        report['rows'].append(row)
                        print(json.dumps(row), flush=True)
                report['coreHaltedAfter'] = bool(target.read32(0xe000edf0) & 0x20000)
            report['ownerAfter'] = status(raw)['ownerTransport']
            if report['ownerAfter'] or report['coreHaltedAfter'] != report['coreHaltedBefore']:
                raise RuntimeError('Target state/owner changed')
        report['complete'] = True
    except BaseException as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'complete':report['complete'], 'output':str(args.output)}), flush=True)


if __name__ == '__main__': main()
