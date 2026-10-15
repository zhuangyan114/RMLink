"""Five depth-3 pointer fields + five direct fields, at 1/5 kHz requested.

All ten dynamic F103 fixture leaves are separated by 64 bytes. Compare shared
and independent pointer chains with identical leaf addresses; no Flash writes.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import time

from benchmark_pointer_subscriptions import ApiClient,ROOT,layout,collect,require


def mixed_layout(shared):
    image,fields=layout(3,shared)
    return image,fields[:5]+[{'address':0x20000100+i*64,'offsets':[]} for i in range(5,10)]


def capture(client,shared,period,coexist,seconds):
    image,fields=mixed_layout(shared)
    client.target('write_memory',address=0x20001000,data=image.hex())
    sub=client.subscribe('memory_series',fields=fields,period_us=period);ids=[sub['subscription_id']]
    try:
        if coexist:
            ids.append(client.subscribe('memory_snapshot',fields=fields[:4],period_us=100000)['subscription_id'])
            ids.append(client.subscribe('rtt_stream',control_block_address=0x20000400,period_us=5000)['subscription_id'])
        result=collect(client,sub,seconds)
        result.update(shared=shared,periodUs=period,requestedHz=1e6/period,coexist=coexist,fields=fields)
        result['allStatuses']={str(i):client.subscription(i) for i in ids}
        require(result['samples']>0 and not result['invalidFrames'] and not result['consumerGap'],'Invalid mixed capture')
        require(all(not s['failures'] and not s['ring_dropped'] for s in result['allStatuses'].values()),'Mixed SWD/cache failure')
        return result
    finally:
        for sid in reversed(ids):client.unsubscribe(sid)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=5);parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--device-id',default='50787d15a924');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 2<=args.seconds<=300 or not 1<=args.repetitions<=10:parser.error('Invalid duration/repetitions')
    if args.output.exists():parser.error('Preserve existing evidence')
    report=dict(capturedAt=datetime.now().astimezone().isoformat(),complete=False,rows=[],
                pointerFields=5,pointerDepth=3,directFields=5,leafStrideBytes=64,requestedClockHz=4000000,
                actualClockMeasuredInThisRun=False,secondsPerRun=args.seconds,repetitions=args.repetitions,
                scope='public service + real F103 fixture; target remains running; no Flash write')
    try:
        with ApiClient().open(args.device_id) as client:
            report['device']=next(d for d in client.request('GET','/v1/status')['devices'] if d['device_id']==args.device_id)
            report['target']=client.target('connect');report['healthBefore']=client.device_health(args.device_id)
            require(client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52','Fixture missing')
            for repetition in range(args.repetitions):
                cases=[(shared,period,coexist) for shared in (False,True) for period in (1000,200) for coexist in (False,True)]
                if repetition%2:cases.reverse()
                for shared,period,coexist in cases:
                    row=capture(client,shared,period,coexist,args.seconds);row['repetition']=repetition+1
                    report['rows'].append(row)
                    print(json.dumps({k:row[k] for k in ('repetition','shared','requestedHz','coexist','effectiveHz','captureSpanUs','sampleIntervalUs','sequenceGaps','invalidFrames','consumerGap')}),flush=True)
            report['medians']=[]
            for shared in (False,True):
                for period in (1000,200):
                    for coexist in (False,True):
                        rows=[r for r in report['rows'] if (r['shared'],r['periodUs'],r['coexist'])==(shared,period,coexist)]
                        report['medians'].append(dict(shared=shared,requestedHz=1e6/period,coexist=coexist,
                            effectiveHz=statistics.median(r['effectiveHz'] for r in rows),
                            captureSpanP50Us=statistics.median(r['captureSpanUs']['p50'] for r in rows),
                            sampleIntervalP50Us=statistics.median(r['sampleIntervalUs']['p50'] for r in rows),
                            sampleIntervalP99Us=statistics.median(r['sampleIntervalUs']['p99'] for r in rows)))
            report['healthAfter']=client.device_health(args.device_id)
            report['stateAtEnd']=client.target('state');require(report['stateAtEnd']['state']=='Running','Target stopped')
            report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'failure':report.get('failure'),'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
