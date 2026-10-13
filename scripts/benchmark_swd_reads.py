"""Read-only Flash transfer comparison on the same physical target and image.

Records a local 64 KiB Flash backup and compares 4 KiB reads at requested clocks.
No target halt, reset, SRAM write, erase or programming is performed.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from rmlink_swd import SwdSession
from rmlink_tool import TcpClient
from rmlink_usb import UsbClient


def read_bytes(swd, address, size):
    words = swd.read_words(address, size // 4)
    return struct.pack('<' + 'I' * len(words), *words)


def run(host, port, output, backup):
    report = dict(capturedAt=datetime.now().astimezone().isoformat(),
                  target='user reports STM32F103C8T8; DEV_ID must be 0x410',
                  dataAddress='0x08000000', dataBytes=4096,
                  readPath='DAP_TransferBlock, up to 15 words per response; TAR updated per block')
    with UsbClient() as client, SwdSession(client, 1000000) as swd:
        if swd.read32(0xe0042000) & 0xfff != 0x410 or swd.read32(0x1ffff7e0) & 0xffff != 64:
            raise AssertionError('Backup must be restricted to this medium-density 64 KiB target')
        begin = time.perf_counter()
        data = read_bytes(swd, 0x08000000, 64 * 1024)
        backup_ms = (time.perf_counter() - begin) * 1000
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists() and backup.read_bytes() != data:
            raise RuntimeError('Existing backup differs; refusing to overwrite it')
        backup.write_bytes(data)
        report['flashBackup'] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                    elapsedMs=round(backup_ms, 2), path=str(backup))
    expected = data[:4096]
    report['sampleSha256'] = hashlib.sha256(expected).hexdigest()
    rows = []
    for transport in ('USB', 'TCP'):
        for clock in (100000, 500000, 1000000, 2000000):
            try:
                client = UsbClient() if transport == 'USB' else TcpClient(host, port)
                with client, SwdSession(client, clock) as swd:
                    times = []
                    for _ in range(3):
                        begin = time.perf_counter()
                        actual = read_bytes(swd, 0x08000000, 4096)
                        elapsed = time.perf_counter() - begin
                        if actual != expected:
                            raise AssertionError('Target Flash content differs from baseline')
                        times.append(elapsed)
                median = sorted(times)[1]
                row = dict(transport=transport, clockRequestedHz=clock, passed=True,
                           repetitions=3, elapsedMs=[round(value * 1000, 2) for value in times],
                           medianKiBPerSecond=round(4 / median, 2))
            except (RuntimeError, OSError, AssertionError) as error:
                row = dict(transport=transport, clockRequestedHz=clock, passed=False, error=str(error))
                rows.append(row)
                print(json.dumps(row), flush=True)
                break  # Don't keep increasing clock after a physical access failure.
            rows.append(row)
            print(json.dumps(row), flush=True)
    report['reads'] = rows
    report['evidence'] = 'Same-target Flash reads and host timing only; configured clocks not measured on SWCLK; not programming throughput or variable sampling rate; target application not intentionally halted'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if len(rows) != 8 or not all(row['passed'] for row in rows):
        raise RuntimeError('At least one read benchmark failed; inspect the recorded report')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    run(args.host, args.port, args.output, args.backup)


if __name__ == '__main__':
    main()
