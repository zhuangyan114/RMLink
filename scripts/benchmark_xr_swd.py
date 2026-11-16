"""Repeatable H723 SWD comparison through the connected Wi-Fi probe.

Requires explicit target-write authorization. Halts the target and overwrites
AXI SRAM scratch; it does not back up or restore the target application.
RMT observes SWCLK digitally (12.5 ns ticks), not analog signal integrity.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.target_pack import PACK_TAG, resolve_target
from rmlink_swd import SwdSession

SCRATCH = 0x24010000


def vendor(client, sub, payload=b''):
    reply = client.exchange(bytes([0x80, sub]) + payload)
    if reply[:3] != bytes([0x80, sub, 0]):
        raise RuntimeError(f'Diagnostic {sub}: {reply.hex()}')
    return reply[3:]


def metrics(client):
    data = vendor(client, 11)
    transfers, waits, errors = struct.unpack_from('<III', data, 1)
    cycles, wire = struct.unpack_from('<QQ', data, 13)
    cpu = struct.unpack_from('<I', data, 53)[0]
    return dict(transfers=transfers, waits=waits, errors=errors,
                elapsed_cycles=cycles, wire_clocks=wire, cpu_hz=cpu,
                mean_transfer_us=cycles * 1e6 / cpu / transfers if transfers else 0)


def pattern(round_index, count):
    fixed = (0, 0xffffffff, 0xaaaaaaaa, 0x55555555)
    if round_index < len(fixed):
        return [fixed[round_index]] * count
    rng = random.Random(0x58524441 + round_index)
    return [rng.getrandbits(32) for _ in range(count)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write', action='store_true', required=True)
    p.add_argument('--device', default='b81f3fc30470')
    p.add_argument('--clocks', type=int, nargs='+', default=[1000000, 4000000, 8000000, 10000000])
    p.add_argument('--rounds', type=int, default=8)
    p.add_argument('--words', type=int, default=1024)
    p.add_argument('--label', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Choose a new evidence path')
    if not 1 <= args.rounds <= 1000 or not 8 <= args.words <= 4096:
        p.error('rounds 1..1000, words 8..4096')
    report = dict(started_at=datetime.now().astimezone().isoformat(), label=args.label,
                  complete=False, rows=[], scratch=hex(SCRATCH), bytes_per_round=args.words * 4,
                  rounds=args.rounds, source_commit=subprocess.check_output(
                      ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  scope='H723 halted; AXI SRAM overwritten; no application backup/restore; Wi-Fi via local API')
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    api = ApiClient()
    try:
        report['device'] = next(d for d in api.request('GET', '/v1/status')['devices']
                                if d['device_id'] == args.device)
        if report['device']['transport'] != 'wifi' or report['device']['owner']:
            raise RuntimeError('An unoccupied Wi-Fi device is required')
        report['firmware'] = api.request('GET', f'/v1/devices/{args.device}/firmware')
        for clock in args.clocks:
            row = dict(clock_requested_hz=clock, passed=False, write_ms=[], read_ms=[], local=[], traces=[])
            report['rows'].append(row)
            try:
                with api.open(args.device) as client:
                    row['identity'] = client.target('connect', device='STM32H723VG', clock_hz=clock)
                    row['halt'] = client.target('halt')
                    for _ in range(3):
                        row['traces'].append(client.target('measure_swd_clock', mode='dp_read'))
                # The service deliberately separates enhanced and raw DAP sessions.
                with api.open(args.device) as client:
                    with SwdSession(client, clock) as swd:
                        if (swd.read32(0xe000ed00) >> 4) & 0xfff != 0xc27:
                            raise AssertionError('Expected H723 Cortex-M7')
                        profile = resolve_target('STM32H723VG')
                        selected = struct.pack('<BI', profile['profileId'], PACK_TAG)
                        if client.exchange(b'\x81\x15' + selected) != b'\x81\x15\0' + selected:
                            raise RuntimeError('H723 target package selection failed')
                        vendor(client, 14, b'\0')
                        vendor(client, 14, b'\1')
                        try:
                            for iteration in range(args.rounds):
                                words = pattern(iteration, args.words)
                                start = time.perf_counter()
                                swd.write_words(SCRATCH, words, pipelined=True)
                                row['write_ms'].append((time.perf_counter() - start) * 1000)
                                start = time.perf_counter()
                                actual = swd.read_words(SCRATCH, len(words), pipelined=True)
                                row['read_ms'].append((time.perf_counter() - start) * 1000)
                                if actual != words:
                                    mismatch = next(i for i, (a, b) in enumerate(zip(actual, words)) if a != b)
                                    raise AssertionError(f'SRAM mismatch at word {mismatch}')
                            row['transfer_metrics'] = metrics(client)
                        finally:
                            vendor(client, 14, b'\0')
                        # Time on the probe, excluding Wi-Fi/HTTP round trips.
                        for stride in (4, 64):
                            for _ in range(3):
                                reply = client.exchange(b'\x81\x0a' + struct.pack('<IIIB', SCRATCH, stride, 1024, 8))
                                if reply[:3] != b'\x81\x0a\0':
                                    raise RuntimeError('Local benchmark rejected: ' + reply.hex())
                                reads, elapsed, checksum = struct.unpack('<IQI', reply[3:])
                                result = dict(reads=reads, elapsed_us=elapsed, checksum=checksum)
                                if result['reads'] != 1024 or result['checksum'] != 0:
                                    raise AssertionError(f'Local benchmark incomplete: {result}')
                                row['local'].append(dict(stride=stride, **result))
                        row['last_pattern_sha256'] = hashlib.sha256(struct.pack('<' + 'I' * len(words), *words)).hexdigest()
                    row['passed'] = True
                    for direction in ('read', 'write'):
                        row[direction + '_kib_s'] = args.words * 4 / 1024 * 1000 / statistics.median(row[direction + '_ms'])
            except Exception as error:
                row['error'] = f'{type(error).__name__}: {error}'
            save()
            print(json.dumps({k: v for k, v in row.items() if k not in ('traces', 'identity', 'halt')}), flush=True)
            if not row['passed']:
                # A new connect re-enters SWD at the conservative clock; never replay a write.
                with api.open(args.device) as client:
                    row['recovered_at_1mhz'] = client.target('connect', device='STM32H723VG', clock_hz=1000000)
                save()
                break
        report['complete'] = len(report['rows']) == len(args.clocks) and all(r['passed'] for r in report['rows'])
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['finished_at'] = datetime.now().astimezone().isoformat()
        save()
    if not report['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
