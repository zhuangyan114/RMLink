"""Verify all 64 KiB of the saved target Flash through both physical transports."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = args.baseline.read_bytes()
    if len(expected) != 65536:
        raise ValueError('Expected exactly 64 KiB of target Flash')
    rows = []
    for transport in ('USB', 'TCP'):
        client = UsbClient() if transport == 'USB' else TcpClient(args.host, 3241)
        with client, SwdSession(client, 2000000) as swd:
            begin = time.perf_counter()
            words = swd.read_words(0x08000000, len(expected) // 4, pipelined=True)
            elapsed = time.perf_counter() - begin
            actual = struct.pack('<' + 'I' * len(words), *words)
            if actual != expected:
                raise AssertionError(f'{transport} full Flash read differs from backup')
            row = dict(transport=transport, clockRequestedHz=2000000, bytes=len(actual),
                       elapsedMs=round(elapsed * 1000, 2), sha256=hashlib.sha256(actual).hexdigest(),
                       contentMatchesBackup=True)
            rows.append(row)
            print(json.dumps(row), flush=True)
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), results=rows,
                  evidence='Full main Flash backup read consistency; no erase/program/target halt/reset or backup restoration tested')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
