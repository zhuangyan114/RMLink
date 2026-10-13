"""Compare standard DAP sequential/pipelined reads against the saved target Flash."""
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


def run(host, port, baseline):
    expected = baseline.read_bytes()[:4096]
    if len(expected) != 4096:
        raise ValueError('Baseline must include the first 4096 Flash bytes')
    rows = []
    for transport in ('USB', 'TCP'):
        for clock in (1000000, 2000000):
            for pipelined in (False, True):
                client = UsbClient() if transport == 'USB' else TcpClient(host, port)
                with client, SwdSession(client, clock) as swd:
                    times = []
                    for _ in range(3):
                        begin = time.perf_counter()
                        words = swd.read_words(0x08000000, 1024, pipelined=pipelined)
                        elapsed = time.perf_counter() - begin
                        actual = struct.pack('<' + 'I' * len(words), *words)
                        if actual != expected:
                            raise AssertionError('Flash content/order differs from saved baseline')
                        times.append(elapsed)
                median = sorted(times)[1]
                row = dict(transport=transport, clockRequestedHz=clock,
                           mode='pipelined' if pipelined else 'sequential',
                           maxPendingDapPackets=8 if pipelined else 1,
                           repetitions=3, elapsedMs=[round(value * 1000, 2) for value in times],
                           medianKiBPerSecond=round(4 / median, 2), contentMatchesBackup=True)
                rows.append(row)
                print(json.dumps(row), flush=True)
    return dict(capturedAt=datetime.now().astimezone().isoformat(),
                flashAddress='0x08000000', bytesPerRead=4096,
                baselineSha256=hashlib.sha256(expected).hexdigest(), rows=rows,
                evidence='Physical Flash reads with standard DAP host pipelining, same firmware and target; clocks requested, not measured; no target halt/reset/Flash write or board sampling')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.host, args.port, args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
