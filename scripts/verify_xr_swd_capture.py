"""Verify real H723 fixture sampling, coexistence and long Wi-Fi captures.

Requires tests/stm32h723/swd_fixture.c already programmed. No target image is
read back for preservation. The fixture is left running after this test.
"""
import argparse
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient


def percentiles(values):
    data = sorted(values)
    return {name: data[int((len(data) - 1) * q)] if data else None
            for name, q in (('p50', .5), ('p95', .95), ('p99', .99), ('max', 1))}


def capture(args, clock, progress):
    row = dict(clock_requested_hz=clock, seconds=args.seconds, period_us=args.period_us,
               field_count=args.field_count, coexist=args.coexist, passed=False)
    with ApiClient().open(args.device) as c:
        row['identity'] = c.target('connect', device='STM32H723VG', clock_hz=clock)
        if c.target('read_memory', address=0x24000000, byte_length=4)['data'] != '57535258':
            raise RuntimeError('Standalone H723 SWD fixture required')
        c.target('run')
        fields = [dict(address=0x24000008 + i * 4) for i in range(args.field_count)]
        sub = c.subscribe('memory_series', fields=fields, period_us=args.period_us)
        ids = [sub['subscription_id']]
        try:
            if args.coexist:
                ids.append(c.subscribe('memory_snapshot', fields=fields[:2], period_us=20000)['subscription_id'])
            after = sub['stream_cursor']
            samples = invalid = gaps = changed = consumer_gap = 0
            previous_sequence = first_stamp = last_stamp = previous_value = None
            durations, intervals = deque(maxlen=65536), deque(maxlen=65536)
            started = time.monotonic()
            next_progress = started + 10
            deadline = started + args.seconds
            row['examples'] = []
            while time.monotonic() < deadline:
                batch = c.events(after, 256, 200)
                after = batch['cursor']
                consumer_gap += batch['consumer_gap']
                if batch['error']:
                    raise RuntimeError('Capture stream failed: ' + str(batch['error']))
                for record in batch['records']:
                    sample = record['sample']
                    if sample['capture_id'] != sub['capture_id']:
                        continue
                    samples += 1
                    bad = sample['valid_bitmap'] != (1 << args.field_count) - 1 or bool(sample['flags'] & 1)
                    invalid += bad
                    if previous_sequence is not None:
                        gaps += (sample['sequence'] - previous_sequence - 1) & 0xffffffff
                        intervals.append(sample['timestamp_us'] - last_stamp)
                    previous_sequence = sample['sequence']
                    last_stamp = sample['timestamp_us']
                    if first_stamp is None:
                        first_stamp = last_stamp
                    durations.append(sample['duration_us'])
                    if not bad:
                        for i, value in enumerate(sample['values']):
                            if value >> 24 != i + 1:
                                raise AssertionError(f'Field {i} corrupted: {value:#x}')
                        value = sample['values'][0] & 0xffffff
                        if previous_value is not None and value != previous_value:
                            changed += 1
                        previous_value = value
                    if len(row['examples']) < 8:
                        row['examples'].append(sample)
                if time.monotonic() >= next_progress:
                    state = c.subscription(sub['subscription_id'])
                    item = dict(at=datetime.now().astimezone().isoformat(), clock=clock,
                                elapsed_s=time.monotonic() - started, samples=samples,
                                invalid=invalid, delivered_sequence_gaps=gaps, status=state)
                    progress.write(json.dumps(item) + '\n')
                    progress.flush()
                    print(json.dumps({k: v for k, v in item.items() if k != 'status'}), flush=True)
                    if state['state'] != 2 or state['failures']:
                        raise AssertionError('Sampling stopped or SWD failed: ' + str(state))
                    next_progress += 10
            row.update(samples=samples, invalid_frames=invalid, changed_frames=changed,
                       delivered_sequence_gaps=gaps, consumer_gap=consumer_gap,
                       effective_hz=(samples - 1) * 1e6 / (last_stamp - first_stamp) if samples > 1 else 0,
                       span_us=percentiles(durations), interval_us=percentiles(intervals),
                       percentile_window_frames=len(durations),
                       statuses=[c.subscription(sid) for sid in ids],
                       health=c.device_health(args.device), target_state=c.target('state'))
            if samples < 10 or invalid or changed < 10 or any(s['failures'] for s in row['statuses']):
                raise AssertionError('Sampling correctness check failed')
            row['passed'] = True
            return row
        finally:
            for sid in reversed(ids):
                c.unsubscribe(sid)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='b81f3fc30470')
    p.add_argument('--clocks', type=int, nargs='+', default=[4000000, 8000000, 10000000])
    p.add_argument('--seconds', type=float, default=30)
    p.add_argument('--period-us', type=int, default=500)
    p.add_argument('--field-count', type=int, default=10)
    p.add_argument('--coexist', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.output.with_suffix('.progress.jsonl').exists():
        p.error('Choose a new evidence path')
    if not 1 <= args.field_count <= 10 or not 1 <= args.seconds <= 3600 or not 200 <= args.period_us <= 100000:
        p.error('Invalid capture bounds')
    report = dict(started_at=datetime.now().astimezone().isoformat(), complete=False, rows=[],
                  scope='physical H723 fixture; Wi-Fi via service; digital values and probe timestamps; no VS Code UI acceptance')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report['firmware'] = ApiClient().request('GET', f'/v1/devices/{args.device}/firmware')
        with args.output.with_suffix('.progress.jsonl').open('x', encoding='utf-8') as progress:
            for clock in args.clocks:
                report['rows'].append(capture(args, clock, progress))
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
