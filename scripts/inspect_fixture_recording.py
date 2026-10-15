"""Re-read a completed F103 soak and audit dynamic fields and RTT sequence gaps.

Runs offline: no connector, USB, SWD or target access. Distinguishes producer
publication skips from a discontinuity in the probe's committed byte stream.
Repeated memory values are allowed; all ten dynamic fixture leaves must change.
"""
import argparse
import json
from pathlib import Path
import struct

from verify_subscription_soak import Measurements, inspect_recording, require


class Audit(Measurements):
    def __init__(self):
        super().__init__()
        self.first_rtt_timestamp = None
        self.producer_gaps = []
        self.producer_gap_events = 0
        self.field_first = {}
        self.field_last = {}
        self.field_changes = {}

    def require_dynamic_timeline(self):
        require(len(self.field_changes.get('1', [])) == 10 and
                all(count > 0 for count in self.field_changes['1']),
                'A dynamic Timeline fixture field never changed')

    def accept(self, cursor, sample):
        if sample['kind'] in (1, 2):
            kind = str(sample['kind'])
            values = sample['values']
            if kind not in self.field_last:
                self.field_first[kind] = list(values)
                self.field_changes[kind] = [0] * len(values)
            else:
                require(len(values) == len(self.field_last[kind]), 'Field plan changed within soak')
                for index, value in enumerate(values):
                    self.field_changes[kind][index] += value != self.field_last[kind][index]
            self.field_last[kind] = list(values)
        if sample['kind'] == 3:
            if self.first_rtt_timestamp is None:
                self.first_rtt_timestamp = sample['timestamp_us']
            ready = bytes(self.rtt_pending) + sample['data']
            previous = self.rtt_last_sequence
            for at in range(0, len(ready) - 15, 16):
                _, sequence, _, _ = struct.unpack_from('<IIII', ready, at)
                if previous is not None:
                    delta = (sequence - previous) & 0xffffffff
                    if 1 < delta < 0x80000000:
                        self.producer_gap_events += 1
                        if len(self.producer_gaps) < 128:
                            self.producer_gaps.append({
                                'previous': previous, 'next': sequence, 'skipped': delta - 1,
                                'atUsFromFirstRttFrame': sample['timestamp_us'] - self.first_rtt_timestamp})
                previous = sequence
        super().accept(cursor, sample)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True, help='Completed verify_subscription_soak JSON')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'complete': False, 'source': str(args.report), 'offline': True}
    try:
        source = json.loads(args.report.read_text(encoding='utf-8-sig'))
        require(source['complete'], 'Source soak did not complete')
        recording = source['recording']
        require(recording['phase'] == 'complete' and recording['finalized'], 'Recording is not finalized')
        audit = Audit()
        parsed = inspect_recording(recording['path'], audit.accept)
        require(parsed['sha256'] == recording['sha256'], 'Recording hash changed')
        require(not audit.rtt_pending, 'Recording ended within a fixture RTT record')
        require(audit.summary() == source['disk'], 'Offline measurements differ from completed soak')
        audit.require_dynamic_timeline()
        result.update(recordingSha256=parsed['sha256'], frames=recording['frames'],
                      validated=audit.summary(), producerGaps=audit.producer_gaps,
                      producerGapEvents=audit.producer_gap_events,
                      producerGapListTruncated=audit.producer_gap_events > len(audit.producer_gaps),
                      fieldChangeCounts=audit.field_changes, firstFieldValues=audit.field_first,
                      lastFieldValues=audit.field_last, allTimelineFieldsChanged=True,
                      note='Producer sequence gaps, committed byte continuity and missed poll deadlines are separate.',
                      complete=True)
    except BaseException as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'complete': result['complete'], 'output': str(args.output),
                          'failure': result.get('failure')}), flush=True)


if __name__ == '__main__':
    main()
