"""Inject a read-only H723 bus error, clear sticky state, and verify recovery.

Only for the standalone SWD fixture. Uses raw DAP; the invalid memory access is
a read, and no application-memory write is retried after an uncertain result.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from rmlink_swd import SwdSession


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='b81f3fc30470')
    p.add_argument('--clocks', type=int, nargs='+', default=[4000000, 8000000, 10000000])
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Choose a new evidence path')
    report = dict(started_at=datetime.now().astimezone().isoformat(), complete=False, rows=[],
                  scope='H723 fixture; read unmapped address 0xfffffffc; no memory write replay')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for clock in args.clocks:
            row = dict(clock_requested_hz=clock, passed=False)
            report['rows'].append(row)
            with ApiClient().open(args.device) as client, SwdSession(client, clock) as swd:
                if swd.read32(0x24000000) != 0x58525357:
                    raise RuntimeError('Standalone H723 SWD fixture required')
                swd.write32(0xe000edf0, 0xa05f0001)
                row['running_before'] = not bool(swd.read32(0xe000edf0) & 0x20000)
                try:
                    row['unmapped_value'] = swd.read32(0xfffffffc)
                except RuntimeError as error:
                    row['injected_error'] = str(error)
                    reply = bytes.fromhex(str(error).split(': ', 1)[1].split(' ', 1)[0])
                    row['ack'] = reply[2] & 7
                row['ctrl_stat_before_clear'] = hex(swd.dp_read(4))
                # Cancel any pending read and clear sticky flags; never replay an application write.
                swd.dp_write(0, 0x1f)
                swd.transfer([(8, 0), (1, 0x23000052)])
                row['idcode_after'] = hex(swd.dp_read(0))
                row['magic_after'] = hex(swd.read32(0x24000000))
                row['ctrl_stat_after_clear'] = hex(swd.dp_read(4))
                row['running_after'] = not bool(swd.read32(0xe000edf0) & 0x20000)
                if row.get('ack') != 4 or not int(row['ctrl_stat_before_clear'], 16) & 0x20:
                    raise AssertionError('Expected an actual FAULT with STICKYERR')
                if int(row['ctrl_stat_after_clear'], 16) & 0x20 or row['magic_after'] != '0x58525357' or not row['running_before'] or not row['running_after']:
                    raise AssertionError('Fault injection/recovery failed')
                row['passed'] = True
            print(json.dumps(row), flush=True)
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        report['complete'] = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['finished_at'] = datetime.now().astimezone().isoformat()
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
