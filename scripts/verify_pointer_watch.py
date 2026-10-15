"""Validate Watch/Timeline pointer reuse and freshness on the running F103 fixture.

Writes reserved fixture scratch RAM only. Retains the fixture Flash image.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from benchmark_pointer_subscriptions import layout, require


def collect(client, timeline, watch, after, seconds, expected, minimum_sequence=0):
    deadline = time.perf_counter() + seconds
    result = {'watchFrames': 0, 'reusedFrames': 0, 'ownReadFrames': 0,
              'consumerGap': 0, 'sequenceGaps': 0, 'examples': {}}
    previous = None
    while time.perf_counter() < deadline:
        batch = client.events(after, 256, 100)
        after = batch['cursor']
        result['consumerGap'] += batch['consumer_gap']
        require(not batch['error'], f'Stream error: {batch["error"]}')
        for row in batch['records']:
            sample = row['sample']
            if sample['capture_id'] != watch['capture_id'] or sample['sequence'] < minimum_sequence:
                continue
            require(sample['valid_bitmap'] == 15 and not sample['flags'] & 1,
                    f'Invalid Watch frame: {sample}')
            require([v >> 24 for v in sample['values']] == expected,
                    f'Stale or incorrect pointer values: {sample}')
            if previous is not None:
                result['sequenceGaps'] += (sample['sequence'] - previous - 1) & 0xffffffff
            previous = sample['sequence']
            result['watchFrames'] += 1
            kind = 'reused' if sample['flags'] & 4 else 'ownRead'
            result[kind + 'Frames'] += 1
            result['examples'].setdefault(kind, sample)
    result['timelineStatus'] = client.subscription(timeline['subscription_id'])
    result['watchStatus'] = client.subscription(watch['subscription_id'])
    require(result['watchFrames'] >= 5, 'Insufficient Watch frames')
    require(result['consumerGap'] == 0 and result['sequenceGaps'] == 0, 'Watch stream gap')
    require(all(not result[k]['failures'] and not result[k]['ring_dropped']
                for k in ('timelineStatus', 'watchStatus')), 'SWD failure or ring loss')
    return after, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-id', default='50787d15a924')
    parser.add_argument('--seconds', type=float, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error('seconds must be 1..30')
    report = {'capturedAt': datetime.now().astimezone().isoformat(), 'complete': False,
              'requestedClockHz': 4000000, 'actualClockMeasured': False,
              'fixtureFlashRetained': True, 'pointerDepth': 3, 'leafStrideBytes': 64,
              'secondsPerPhase': args.seconds, 'scratchRam': [0x20001000, 0x20002000]}
    receipt = ROOT / 'artifacts/hardware/rmlink-last-flash.json'
    if receipt.exists():
        report['flashReceipt'] = json.loads(receipt.read_text(encoding='utf-8-sig'))
    ids = []
    try:
        with ApiClient().open(args.device_id) as client:
            report['target'] = client.target('connect')
            require(client.target('read_memory', address=0x20000000, byte_length=4)['data'] == '4b4c4d52',
                    'The RMLink F103 fixture must already be running')
            image, fields = layout(3, True)
            fields = fields[:4]
            try:
                client.target('write_memory', address=0x20001000, data=image.hex())
                timeline = client.subscribe('memory_series', fields=fields, period_us=2000)
                ids.append(timeline['subscription_id'])
                watch = client.subscribe('memory_snapshot', fields=fields, period_us=100000)
                ids.append(watch['subscription_id'])
                after, phase = collect(client, timeline, watch, timeline['stream_cursor'],
                                       args.seconds, [0, 1, 2, 3])
                require(phase['reusedFrames'] > 0, 'Pointer Watch never reused Timeline')
                report['activeTimeline'] = phase

                client.subscription(timeline['subscription_id'], 'pause')
                client.target('write_memory', address=0x20001008,
                              data=struct.pack('<I', 0x20000140).hex())
                cutoff = client.subscription(watch['subscription_id'])['last_sequence']
                after, phase = collect(client, timeline, watch, after, args.seconds, [1, 2, 3, 4], cutoff)
                require(not phase['reusedFrames'], 'Watch reused a paused Timeline')
                report['pausedAndRetargeted'] = phase

                client.subscription(timeline['subscription_id'], 'resume')
                after, phase = collect(client, timeline, watch, after, args.seconds, [1, 2, 3, 4])
                require(phase['reusedFrames'] > 0, 'Watch did not resume Timeline reuse')
                report['resumedTimeline'] = phase

                for sid in reversed(ids):
                    client.unsubscribe(sid)
                ids.clear()
                client.target('write_memory', address=0x20001000, data=image.hex())
                timeline = client.subscribe('memory_series', fields=fields, period_us=100000)
                ids.append(timeline['subscription_id'])
                watch = client.subscribe('memory_snapshot', fields=fields, period_us=10000)
                ids.append(watch['subscription_id'])
                _, phase = collect(client, timeline, watch, timeline['stream_cursor'],
                                   args.seconds, [0, 1, 2, 3])
                require(phase['reusedFrames'] > 0 and phase['ownReadFrames'] > 0,
                        'Watch did not fall back to fresh reads between slow Timeline frames')
                report['slowTimelineFreshness'] = phase
                report['finalCoreState'] = client.target('state')
                require(report['finalCoreState']['state'] == 'Running', 'Fixture stopped')
                report['complete'] = True
            finally:
                for sid in reversed(ids):
                    try:
                        client.unsubscribe(sid)
                    except Exception as error:
                        report.setdefault('cleanupErrors', []).append(str(error))
    except BaseException as error:
        report['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'complete': report['complete'], 'output': str(args.output),
                          'failure': report.get('failure')}), flush=True)


if __name__ == '__main__':
    main()
