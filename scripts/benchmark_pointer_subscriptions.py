"""Real F103 fixture: depth 0..4, shared/independent pointer chains and live retargeting.

Writes only reserved fixture scratch RAM 0x20001000..0x20001fff. The existing
fixture program and Flash image are retained. Do not use on an arbitrary app.
"""
import argparse
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


def require(condition,message):
    if not condition:raise AssertionError(message)


def layout(depth,shared):
    base=0x20001000;image=bytearray(4096);fields=[]
    def put(address,value):struct.pack_into('<I',image,address-base,value)
    for i in range(10):
        root=base+(0 if shared else i*64)
        for d in range(depth):
            put(root+d*4,root+(d+1)*4 if d+1<depth else 0x20000100+(0 if shared else i*64))
        fields.append({'address':root if depth else 0x20000100+i*64,
                       'offsets':([0]*(depth-1)+[i*64 if shared else 0]) if depth else []})
    return image,fields


def collect(client,sub,seconds,expected=None):
    after=sub['stream_cursor'];first=last=None;count=0;gaps=consumer_gap=invalid=0;previous=None
    durations=deque(maxlen=8192);intervals=deque(maxlen=8192)
    deadline=time.perf_counter()+seconds
    while time.perf_counter()<deadline:
        batch=client.events(after,256,500);after=batch['cursor'];consumer_gap+=batch['consumer_gap']
        require(not batch['error'],f'Stream failed: {batch["error"]}')
        for row in batch['records']:
            sample=row['sample']
            if sample['capture_id']!=sub['capture_id']:continue
            if previous is not None:gaps+=(sample['sequence']-previous-1)&0xffffffff
            previous=sample['sequence'];count+=1
            stamp=sample['timestamp_us']
            if last is not None:intervals.append(stamp-last)
            first=stamp if first is None else first;last=stamp
            durations.append(sample['duration_us'])
            invalid+=int(sample['valid_bitmap']!=1023 or bool(sample['flags']&1))
            for i,value in enumerate(sample['values']):
                require(value>>24==(expected if expected is not None else i),f'Wrong field {i}: {value:#x}')
        status=client.subscription(sub['subscription_id'])
        require(status['state']==2,f'Capture stopped: {status}')
    def percentiles(values):
        ordered=sorted(values)
        return {name:ordered[int((len(ordered)-1)*p)] if ordered else None
                for name,p in [('p50',.5),('p95',.95),('p99',.99),('max',1)]}
    return {'samples':count,'effectiveHz':(count-1)*1e6/(last-first) if count>1 else None,
            'sequenceGaps':gaps,'consumerGap':consumer_gap,'invalidFrames':invalid,'status':status,
            'timingWindowFrames':len(durations),'captureSpanUs':percentiles(durations),
            'sampleIntervalUs':percentiles(intervals)}


def capture(client,depth,shared,seconds,period,coexist):
    image,fields=layout(depth,shared)
    client.target('write_memory',address=0x20001000,data=image.hex())
    sub=client.subscribe('memory_series',fields=fields,period_us=period);ids=[sub['subscription_id']]
    try:
        if coexist:
            ids.append(client.subscribe('memory_snapshot',fields=fields[:4],period_us=100000)['subscription_id'])
            ids.append(client.subscribe('rtt_stream',control_block_address=0x20000400,period_us=5000)['subscription_id'])
        result=collect(client,sub,seconds)
        result.update(depth=depth,shared=shared,coexist=coexist,periodUs=period)
        require(result['samples']>0 and result['invalidFrames']==0 and result['consumerGap']==0,'Invalid capture')
        require(all(client.subscription(s)['failures']==0 for s in ids),'SWD failure')
        return result
    finally:
        for sid in reversed(ids):client.unsubscribe(sid)


def retarget(client):
    image,fields=layout(3,True)
    # Ten aliases of one leaf let us verify every valid value after a change.
    for f in fields:f['offsets'][-1]=0
    client.target('write_memory',address=0x20001000,data=image.hex())
    sub=client.subscribe('memory_series',fields=fields,period_us=5000);steps=[]
    try:
        for label,pointer,expected in [('initial',0x20000100,0),('retarget',0x20000140,1),
                ('null',0,None),('unaligned',0x20000101,None),('outside_ram',0x20005000,None),
                ('recovered',0x20000340,9)]:
            # Pause sampling only (the target keeps running). The retained
            # sequence separates queued old/partial frames from the next frame.
            client.subscription(1,'pause')
            client.target('write_memory',address=0x20001008,data=struct.pack('<I',pointer).hex())
            next_sequence=client.subscription(1)['last_sequence']
            client.subscription(1,'resume')
            after=sub['stream_cursor'];seen=0;deadline=time.perf_counter()+2
            while time.perf_counter()<deadline and seen<10:
                batch=client.events(after,128,200);after=batch['cursor']
                require(not batch['error'],'Retarget stream error')
                for row in batch['records']:
                    sample=row['sample']
                    if sample['capture_id']!=sub['capture_id']:continue
                    if sample['sequence']<next_sequence:continue
                    if expected is None:
                        require(sample['valid_bitmap']==0 and sample['flags']&1,'Bad pointer became valid')
                    else:
                        require(sample['valid_bitmap']==1023 and all(v>>24==expected for v in sample['values']),
                                f'Pointer cache survived across frames: {label}: {sample}')
                    seen+=1
            require(seen>=10,f'Not enough retarget samples: {label}')
            steps.append({'step':label,'checked':seen,'status':client.subscription(1)})
        return steps
    finally:client.unsubscribe(1)


def signed_offsets_and_widths(client):
    image=bytearray(4096)
    for address,value in [(0x20001000,0x200011fc),(0x20001200,0x20001304),
            (0x20001300,0x200013fc),(0x20001400,0x20000104),
            (0x20001404,0xffffffff),(0x20001408,0),(0x2000140c,0x20000101),
            (0x20001410,0x20005000)]:
        struct.pack_into('<I',image,address-0x20001000,value)
    client.target('write_memory',address=0x20001000,data=image.hex())
    fields=[{'address':0x20001000,'offsets':[4,-4,4,-4],'byte_length':w} for w in (4,2,1)]
    fields.extend([{'address':address,'offsets':[offset]} for address,offset in
        [(0x20001404,4),(0x20001408,-4),(0x2000140c,0),(0x20001410,0)]])
    sub=client.subscribe('memory_series',fields=fields,period_us=10000,sample_limit=20)
    after=sub['stream_cursor'];seen=0;deadline=time.perf_counter()+5
    try:
        while seen<20 and time.perf_counter()<deadline:
            batch=client.events(after,128,200);after=batch['cursor'];require(not batch['error'],'Edge-case stream')
            for row in batch['records']:
                s=row['sample']
                if s['capture_id']!=sub['capture_id']:continue
                require(s['valid_bitmap']==7 and s['flags']&1,'Invalid pointer branch isolation')
                full,half,byte,*bad=s['values']
                require(half==full&0xffff and byte==full&0xff and bad==[0]*4,'Width/cache mismatch')
                seen+=1
        status=client.subscription(1)
        require(seen==20 and status['stop_reason']=='sample_limit','Edge-case capture completion')
        return {'samples':seen,'fourSignedOffsets':[4,-4,4,-4],'widths':[4,2,1],
                'invalidCases':['uint32_overflow','uint32_underflow','unaligned','outside_ram'],'status':status}
    finally:client.unsubscribe(1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--seconds',type=float,default=3)
    p.add_argument('--repetitions',type=int,default=3);p.add_argument('--period-us',type=int,default=1000)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not 1<=args.seconds<=300 or not 1<=args.repetitions<=20:p.error('Invalid duration/repetitions')
    if not 200<=args.period_us<=10000000:p.error('Invalid period')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'rows':[],
            'requestedClockHz':4000000,'actualClockMeasured':False,'fixtureFlashRetained':True,
            'scratchRam':[0x20001000,0x20002000],'leafStrideBytes':64,
            'requestedSecondsPerRun':args.seconds,'repetitions':args.repetitions,
            'scope':'public service subscriptions; no Orbit adapter or installed VS Code UI',
            'timingScope':'probe frame span includes SWD plus executor scheduling; last 8192 frames, not pure SWCLK time'}
    try:
        with ApiClient().open(args.device_id) as client:
            report['device']=next(d for d in client.request('GET','/v1/status')['devices'] if d['device_id']==args.device_id)
            report['healthBefore']=client.device_health(args.device_id)
            report['target']=client.target('connect')
            require(client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52','Fixture missing')
            for repetition in range(args.repetitions):
                for depth in range(5):
                    for shared in ([False] if not depth else [False,True]):
                        for coexist in [False,True]:
                            row=capture(client,depth,shared,args.seconds,args.period_us,coexist)
                            row['repetition']=repetition+1;report['rows'].append(row);print(json.dumps(row),flush=True)
            report['retarget']=retarget(client)
            report['signedOffsetsAndWidths']=signed_offsets_and_widths(client)
            report['healthAfter']=client.device_health(args.device_id);report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
