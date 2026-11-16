"""Program the standalone H723 fixture repeatedly and verify both copies."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write', action='store_true', required=True)
    p.add_argument('--device', default='b81f3fc30470')
    p.add_argument('--clock', type=int, default=10000000)
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--image', type=Path, default=ROOT / 'artifacts/build/h723-swd-fixture/fixture-128k.bin')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or not 1 <= args.rounds <= 20:
        p.error('Choose a new evidence path; rounds 1..20')
    data = args.image.read_bytes()
    if len(data) != 131072:
        p.error('A single 128 KiB fixture sector is required')
    report = dict(started_at=datetime.now().astimezone().isoformat(), complete=False, rows=[],
                  clock_requested_hz=args.clock, image_sha256=hashlib.sha256(data).hexdigest(),
                  bytes=len(data), original_program_restored=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        api = ApiClient()
        report['firmware'] = api.request('GET', f'/v1/devices/{args.device}/firmware')
        for iteration in range(args.rounds):
            row = dict(iteration=iteration + 1, passed=False)
            report['rows'].append(row)
            with api.open(args.device) as c:
                row['identity'] = c.target('connect', device='STM32H723VG', clock_hz=args.clock)
                if c.target('read_memory', address=0x24000000, byte_length=4)['data'] != '57535258':
                    raise RuntimeError('Standalone H723 fixture required')
                result = c.flash_image(data, verify_on_host=True, reset_after=True)
                row['flash'] = result
                if not result['probe_verified'] or not result['host_verified'] or result['sha256'] != report['image_sha256']:
                    raise AssertionError('Full image verification failed')
                if result['probe_recovery']['downgraded'] or result['probe_recovery']['active_clock_hz'] != args.clock:
                    raise AssertionError('Requested clock was not retained')
                row['state'] = c.target('state')
                row['magic'] = c.target('read_memory', address=0x24000000, byte_length=4)
                if row['state']['state'] != 'Running' or row['magic']['data'] != '57535258':
                    raise AssertionError('Fixture did not start after reset')
                row['passed'] = True
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(row), flush=True)
        report['complete'] = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['finished_at'] = datetime.now().astimezone().isoformat()
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
