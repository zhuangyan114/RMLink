"""Compare host polling and probe subscriptions at 4 MHz; keep the target fixture."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


def quantile(data,p):
    if not data:return None
    return sorted(data)[min(len(data)-1,int((len(data)-1)*p))]

def capture(client,addresses,period,seconds,coexist):
    sub=client.subscribe('memory_series',addresses=addresses,period_us=period)
    ids=[sub['subscription_id']]
    if coexist:
        ids.append(client.subscribe('memory_snapshot',addresses=addresses[:4],period_us=100000)['subscription_id'])
        ids.append(client.subscribe('rtt_stream',control_block_address=0x20000400,period_us=5000)['subscription_id'])
    after=sub['stream_cursor'];rows=[];begin=time.perf_counter();errors=[];consumer_gap=0
    try:
        while time.perf_counter()-begin<seconds:
            batch=client.events(after,256,1000);after=batch['cursor']
            consumer_gap+=batch['consumer_gap']
            if batch['error']:raise RuntimeError(batch['error'])
            for entry in batch['records']:
                sample=entry['sample']
                if sample['kind']==1 and sample['capture_id']==sub['capture_id']:rows.append(sample)
            state=client.subscription(1)
            if state['state']==3:
                errors.append({'reason':'probe_paused','status':state});break
        status={i:client.subscription(i) for i in ids}
    finally:
        for i in reversed(ids):client.unsubscribe(i)
    # Processing batches on the PC does not define sample times.
    good=[r for r in rows if r['valid_bitmap']==(1<<len(addresses))-1 and not r['flags']&1]
    times=[r['timestamp_us'] for r in good];intervals=[b-a for a,b in zip(times,times[1:])]
    duration=times[-1]-times[0] if len(times)>1 else 0
    for r in good:
        for i,v in enumerate(r['values']):
            if v>>24!=i:raise AssertionError('Fixture field identity incorrect')
    return {'period_us':period,'coexist':coexist,'received':len(rows),'valid_complete':len(good),
        'effective_hz':(len(times)-1)*1e6/duration if duration else None,
        'interval_p50_us':quantile(intervals,.5),'interval_p95_us':quantile(intervals,.95),
        'interval_p99_us':quantile(intervals,.99),'duration_p99_us':quantile([r['duration_us'] for r in rows],.99),
        'statuses':status,'errors':errors,'consumer_gap':consumer_gap,'first_sequence':rows[0]['sequence'] if rows else None,
        'last_sequence':rows[-1]['sequence'] if rows else None}

def poll_baseline(client,addresses,seconds):
    start=time.perf_counter();completed=0;intervals=[];previous=start
    while time.perf_counter()-start<seconds:
        reply=client.target('read_batch',reads=[{'address':a,'byte_length':4} for a in addresses])
        now=time.perf_counter();intervals.append((now-previous)*1e6);previous=now
        if len(reply['reads'])!=len(addresses):raise AssertionError('Host read count')
        for i,r in enumerate(reply['reads']):
            if int.from_bytes(bytes.fromhex(r['data']),'little')>>24!=i:raise AssertionError('Host fixture field identity')
        completed+=1
    return {'scope':'local API host batch polling; not actual Orbit baseline','completed':completed,
        'elapsed_seconds':time.perf_counter()-start,'effective_hz':completed/(time.perf_counter()-start),
        'interval_p50_us':quantile(intervals,.5),'interval_p95_us':quantile(intervals,.95)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--repetitions',type=int,default=5);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--periods-us',nargs='+',type=int,default=[10000,5000,2000,1000,500,200])
    args=p.parse_args()
    if not 1<=args.seconds<=3600 or not 1<=args.repetitions<=20:p.error('Invalid duration/repetitions')
    addresses=[0x20000100+i*64 for i in range(10)]
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'requestedClockHz':4000000,
        'actualClockMeasured':False,'fixtureRetained':True,'addresses':addresses,'rows':[]}
    try:
        discovery=ApiClient();ready_deadline=time.perf_counter()+30
        while time.perf_counter()<ready_deadline:
            devices=discovery.request('GET','/v1/status')['devices']
            if any(d['device_id']==args.device_id and d['connected'] for d in devices):break
            time.sleep(.5)
        else:raise ConnectionError('Probe did not become ready within 30 seconds')
        with ApiClient().open(args.device_id) as client:
            report['target']=client.target('connect')
            # Verify fixture identity before interpreting dynamic data.
            assert client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52'
            report['localContinuous']=client.target('benchmark_local',address=0x20000100,stride=4,read_count=10000,field_count=10)
            report['localDiscrete']=client.target('benchmark_local',address=0x20000100,stride=64,read_count=10000,field_count=10)
            report['hostApiPolling']=[poll_baseline(client,addresses,args.seconds) for _ in range(args.repetitions)]
            for period in args.periods_us:
                for repetition in range(args.repetitions):
                    for coexist in ((False,True) if repetition%2==0 else (True,False)):
                        row=capture(client,addresses,period,args.seconds,coexist)
                        row['repetition']=repetition+1;report['rows'].append(row);print(json.dumps(row),flush=True)
            report['complete']=True
    except BaseException as error:
        report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
