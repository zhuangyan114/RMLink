"""Read-only requested-clock sweep; actual SWCLK requires a waveform measurement."""
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
from rmlink_usb import UsbClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=['USB','VirtualUSB'], required=True)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--expected-image', type=Path, default=ROOT / 'artifacts/build/stm32-fixture/fixture-64k.bin')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.samples <= 20: parser.error('Use 3..20 repetitions')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  transport=args.transport, actualClockMeasured=False, clockRestoredHz=4000000, rows=[])
    try:
        snapshot = ApiClient().request('GET','/v1/status')
        assert all(d['owner'] is None for d in snapshot['devices'])
        backup = args.expected_image.read_bytes()
        assert len(backup) == 65536
        serial = '50787D15A924' if args.transport == 'USB' else 'RM50787D15A924'
        with UsbClient(serial=serial) as client:
            report['packetSize'] = client.packet_size
            for clock in (1000000, 2000000, 4000000, 6000000, 8000000, 10000000):
                row = dict(clockRequestedHz=clock, complete=False, elapsedMs=[])
                try:
                    with SwdSession(client, clock) as swd:
                        assert swd.read32(0xe000ed00) == 0x411fc231
                        halted = bool(swd.read32(0xe000edf0) & 0x20000)
                        for _ in range(args.samples):
                            start = time.perf_counter()
                            words = swd.read_words(0x08000000, 1024, True)
                            elapsed = (time.perf_counter() - start)*1000
                            assert struct.pack('<1024I', *words) == backup[:4096]
                            row['elapsedMs'].append(round(elapsed, 3))
                        full = struct.pack('<16384I', *swd.read_words(0x08000000, 16384, True))
                        assert full == backup
                        assert bool(swd.read32(0xe000edf0) & 0x20000) == halted
                        row.update(complete=True, fullFlashMatchesBackup=True,
                                   medianKiBPerSecond=round(4000/statistics.median(row['elapsedMs']),3))
                except (RuntimeError, OSError, AssertionError) as error:
                    # A failed clock is recorded, then reinitialize at the known setting.
                    row['failure'] = f'{type(error).__name__}: {error}'
                    with SwdSession(client, 4000000) as swd: assert swd.read32(0xe000ed00) == 0x411fc231
                report['rows'].append(row); print(json.dumps(row), flush=True)
            with SwdSession(client, 4000000) as swd: assert swd.read32(0xe000ed00) == 0x411fc231
        report['complete'] = True
        report['defaultChanged'] = False
    except BaseException as error:
        report['failure'] = f'{type(error).__name__}: {error}'; raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'complete':report['complete'], 'output':str(args.output)}), flush=True)


if __name__ == '__main__': main()
