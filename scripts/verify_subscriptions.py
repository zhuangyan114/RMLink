"""Real F103C8 subscription/recording acceptance; leave the fixture running."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.recording_reader import inspect_recording


def require(condition,message):
    if not condition:raise AssertionError(message)


def strict_recording(client,seconds):
    ids=[]
    try:
        for kind,options in (
            ('memory_series',{'addresses':[0x20000100+i*64 for i in range(10)],'period_us':10000}),
            ('memory_snapshot',{'addresses':[0x20000100+i*64 for i in range(4)],'period_us':100000}),
            ('rtt_stream',{'control_block_address':0x20000400,'period_us':5000})):
            ids.append(client.subscribe(kind,**options)['subscription_id'])
        recording=client.start_recording(strict=True)
        deadline=time.perf_counter()+seconds
        while time.perf_counter()<deadline:
            state=client.recording(recording['recording_id'])
            if state['error']:break
            time.sleep(.2)
        result=client.recording(recording['recording_id'],stop=True)
        counts={1:0,2:0,3:0};reused=0;rtt=bytearray();last_byte=None
        def verify(cursor,sample):
            nonlocal reused,last_byte
            counts[sample['kind']]+=1
            if sample['kind']!=3:
                for i,value in enumerate(sample['values']):require(value>>24==i,'Fixture field identity')
                reused+=int(sample['kind']==2 and bool(sample['flags']&4))
            else:
                require(last_byte is None or last_byte==sample['byte_sequence'],'RTT byte sequence')
                last_byte=sample['byte_sequence']+len(sample['data']);rtt.extend(sample['data'])
        parsed=inspect_recording(result['path'],verify)
        require(result['phase']=='complete' and result['finalized'],f'Strict recording failed: {result}')
        require(parsed['sha256']==result['sha256'],'Independent file hash')
        require(all(counts.values()),'All three subscriptions must produce records')
        require(reused>0,'Watch reuse not observed')
        require(len(rtt)%16==0,'RTT fixture record alignment')
        for i in range(0,len(rtt),16):
            magic,seq,counter,check=struct.unpack_from('<IIII',rtt,i)
            require(magic==0x524d5254 and check==magic^seq^counter,'RTT fixture checksum')
        return {'recording':result,'counts':counts,'watchReusedFrames':reused,'rttBytesVerified':len(rtt),
            'withoutUiEventPolling':True,'independentHashAndCrcVerified':True}
    finally:
        for sid in reversed(ids):client.unsubscribe(sid)


def pointer_fields(client):
    sub=client.subscribe('memory_series',period_us=10000,sample_limit=8,fields=[
        {'address':0x20000380,'offsets':[0,0,0]},
        {'address':0x20000388,'offsets':[0]},
        {'address':0x20000104,'byte_length':1},
        {'address':0x20000108,'byte_length':2},
        {'address':0x2000038c,'offsets':[0]}])
    after=sub['stream_cursor'];seen=0;deadline=time.perf_counter()+5
    try:
        while time.perf_counter()<deadline and seen<8:
            batch=client.events(after,256,500);after=batch['cursor']
            for entry in batch['records']:
                sample=entry['sample']
                if sample['capture_id']!=sub['capture_id']:continue
                require(sample['valid_bitmap']==15 and sample['flags']&1,'Invalid pointer validity isolation')
                a,b,byte,half,invalid=sample['values']
                require(a>>24==b>>24==9 and byte==1 and half==2 and invalid==0,'Pointer/width values')
                seen+=1
        status=client.subscription(1)
        require(seen==8 and status['stop_reason']=='sample_limit','Pointer capture completion')
        return {'samples':seen,'threeLevelPointer':True,'widths':[1,2,4],'invalidFieldIsolated':True,'status':status}
    finally:client.unsubscribe(1)


def strict_overload(client):
    client.subscribe('memory_series',addresses=[0x20000100+i*64 for i in range(10)],period_us=200)
    recording=client.start_recording(strict=True)
    try:
        deadline=time.perf_counter()+5
        while time.perf_counter()<deadline:
            status=client.recording(recording['recording_id'])
            if status['finalized']:break
            time.sleep(.02)
        probe=client.subscription(1)
        require(status['phase']=='failed' and status['finalized'],'Strict overload was not sealed as failed')
        require(status['error'] in ('sequence_gap','invalid_sample'),'Unexpected strict failure')
        require(probe['state']==3 and probe['stop_reason']=='paused','Failed recording did not pause capture')
        require(inspect_recording(status['path'])['summary']['phase']=='failed','Failed footer missing')
        # Recovery is explicit, starts another file and never replays old reads.
        replacement=client.start_recording(strict=False);client.subscription(1,'resume');time.sleep(.5)
        replacement=client.recording(replacement['recording_id'],stop=True)
        require(replacement['phase']=='complete' and replacement['frames']>0,'Explicit realtime restart failed')
        return {'strictFailure':status,'pausedStatus':probe,'realtimeReplacement':replacement}
    finally:client.unsubscribe(1)


def slow_consumer(client,seconds):
    sub=client.subscribe('memory_series',addresses=[0x20000100],period_us=1000)
    recording=client.start_recording(strict=False)
    try:
        # No event consumer: the independent recorder must outlive history eviction.
        time.sleep(seconds)
        recorded=client.recording(recording['recording_id'],stop=True)
        parsed=inspect_recording(recorded['path'])
        batch=client.events(sub['stream_cursor'],256,0)
        status=client.subscription(1)
        require(recorded['phase']=='complete' and recorded['finalized'],'Realtime recording finalization')
        require(parsed['frames']>4096 and batch['consumer_gap']>0,'History pressure not reached')
        require(status['ring_dropped']==0 and status['failures']==0,'Probe pressure failure')
        return {'recording':recorded,'consumerGap':batch['consumer_gap'],
            'hostHistoryEvicted':batch['host_history_evicted'],'status':status,
            'recorderIndependentOfHistory':True}
    finally:client.unsubscribe(1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=10);args=p.parse_args()
    if not 6<=args.seconds<=3600:p.error('seconds must be 6..3600')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
        'requestedClockHz':4000000,'actualClockMeasured':False,'fixtureRetained':True}
    try:
        with ApiClient().open(args.device_id) as client:
            report['target']=client.target('connect')
            require(client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52','Fixture missing')
            report['strictThreeSubscriptions']=strict_recording(client,args.seconds)
            report['strictOverload']=strict_overload(client)
            report['pointerFields']=pointer_fields(client)
            report['slowConsumer']=slow_consumer(client,args.seconds)
            report['stateAtEnd']=client.target('state')
            report['complete']=True
    except BaseException as error:
        report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
