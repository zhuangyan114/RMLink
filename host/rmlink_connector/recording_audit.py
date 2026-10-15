"""Bounded offline diagnostics; bad samples never relax container/CRC validation."""
from collections import Counter,deque
from .recording_reader import inspect_recording


def audit_recording(path,max_anomalies=16):
    if type(max_anomalies) is not int or not 1<=max_anomalies<=64:raise ValueError('Anomaly limit must be 1..64')
    flags=Counter();kinds=Counter();previous={};recent=deque(maxlen=4);anomalies=[]
    totals=dict(bad_data_samples=0,budget_overrun_samples=0,sequence_gap_events=0,
                missed_sequence_slots=0,out_of_order_samples=0,cursor_order_errors=0,anomaly_samples=0)
    last_cursor=None
    def accept(cursor,sample):
        nonlocal last_cursor
        kind=sample['kind'];flags[sample['flags']]+=1;kinds[kind]+=1;reasons=[]
        if sample['flags']&2:totals['budget_overrun_samples']+=1
        bad=bool(sample['flags']&1)
        if kind!=3:bad=bad or sample['valid_bitmap']!=(1<<len(sample['values']))-1
        if bad:totals['bad_data_samples']+=1;reasons.append('invalid_data')
        if sample['flags']&~7:reasons.append('unknown_sample_flags')
        if last_cursor is not None and cursor<=last_cursor:
            totals['cursor_order_errors']+=1;reasons.append('cursor_not_increasing')
        last_cursor=cursor
        # The wire bounds subscription IDs to 1..4. Keep only each slot's
        # latest generation, so an arbitrarily long file cannot grow this map.
        key=sample['subscription_id'];old=previous.get(key)
        if old is not None and old[0]==sample['capture_id']:
            delta=(sample['sequence']-old[1])&0xffffffff
            if delta!=1:
                totals['sequence_gap_events']+=1
                if 1<delta<0x80000000:totals['missed_sequence_slots']+=delta-1
                else:totals['out_of_order_samples']+=1;reasons.append('sequence_not_increasing')
        previous[key]=(sample['capture_id'],sample['sequence'])
        row={k:sample[k] for k in ('subscription_id','capture_id','kind','sequence','timestamp_us','duration_us','flags','valid_bitmap')}
        row['cursor']=cursor
        if kind!=3:row['values']=sample['values']
        else:row.update(byte_sequence=sample['byte_sequence'],byte_length=len(sample['data']))
        if reasons:
            totals['anomaly_samples']+=1
            if len(anomalies)<max_anomalies:anomalies.append(dict(reasons=reasons,sample=row,preceding=list(recent)))
        recent.append(row)
    parsed=inspect_recording(path,accept)
    return dict(parsed,analysis={**totals,'frames_by_kind':dict(kinds),'frames_by_flags':dict(flags),
        'anomalies':anomalies,'anomalies_truncated':totals['anomaly_samples']>len(anomalies),
        'scope':'offline; sequence gaps include skipped requested deadlines; budget flags do not invalidate data'})
