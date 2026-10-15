"""Real control latency and public C ABI acceptance on the retained F103 fixture."""
import argparse
import ctypes
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.capture_protocol import decode_record


def controls(device_id,repetitions,loaded):
    rows=[]
    with ApiClient().open(device_id) as client:
        client.target('connect');sub=None
        if loaded:sub=client.subscribe('memory_series',addresses=[0x20000100+i*64 for i in range(10)],period_us=200)
        try:
            for i in range(repetitions):
                begin=time.perf_counter();client.target('halt');halt_ms=(time.perf_counter()-begin)*1000
                assert client.target('state')['state']=='Halted'
                if sub:assert client.subscription(1)['state']==3
                begin=time.perf_counter();client.target('run');run_ms=(time.perf_counter()-begin)*1000
                assert client.target('state')['state']=='Running'
                if sub:assert client.subscription(1)['state']==2
                rows.append({'halt_ms':halt_ms,'run_ms':run_ms});time.sleep(.02)
        finally:
            client.target('run')
            if sub:client.unsubscribe(1)
    return {'loaded':loaded,'rows':rows,'median_halt_ms':statistics.median(r['halt_ms'] for r in rows),
        'max_halt_ms':max(r['halt_ms'] for r in rows),'median_run_ms':statistics.median(r['run_ms'] for r in rows),
        'max_run_ms':max(r['run_ms'] for r in rows),'scope':'local API round trip including WiFi and control barriers'}


def c_sdk(device_id):
    lib=ctypes.CDLL(str(ROOT/'artifacts/build/sdk/rmlink.dll'))
    pointer=ctypes.c_void_p;text=ctypes.c_char_p;size=ctypes.c_size_t
    callback_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_uint64,pointer,size,pointer)
    lib.rm_open.argtypes=[text,ctypes.c_uint16,text];lib.rm_open.restype=pointer
    lib.rm_close.argtypes=[pointer]
    lib.rm_target_connect.argtypes=[pointer,ctypes.c_uint32,text,size]
    lib.rm_subscribe.argtypes=[pointer,text,text,size]
    lib.rm_stream.argtypes=[pointer,callback_type,pointer]
    lib.rm_call.argtypes=[pointer,text,text,text,text,size,ctypes.POINTER(size)]
    handle=lib.rm_open(device_id.encode(),3243,None);assert handle
    output=ctypes.create_string_buffer(8192);seen=[];errors=[]
    @callback_type
    def callback(cursor,data,length,context):
        try:
            sample=decode_record(ctypes.string_at(data,length))
            assert sample['valid_bitmap']==1 and not sample['flags']&3
            assert sample['values'][0]>>24==0
            if seen:assert cursor>seen[-1][0] and sample['sequence']==seen[-1][1]+1
            seen.append((cursor,sample['sequence']))
        except BaseException as error:errors.append(str(error));return 1
        return int(len(seen)%20==0)
    try:
        assert lib.rm_target_connect(handle,4000000,output,len(output))==200
        assert lib.rm_subscribe(handle,b'{"kind":"memory_series","addresses":[536871168],"period_us":10000}',output,len(output))==200
        config=json.loads(output.value)
        assert lib.rm_stream(handle,callback,None)==200
        assert len(seen)==20 and not errors,(seen,errors)
        assert lib.rm_stream(handle,callback,None)==200
        assert len(seen)==40 and not errors,(seen,errors)
        assert lib.rm_call(handle,b'DELETE',b'/subscriptions/1',None,output,len(output),None)==200
        return {'samples':len(seen),'crcVerified':True,'cursorResumeWithoutReplay':True,
            'firstCursor':seen[0][0],'lastCursor':seen[-1][0],'captureId':config['capture_id']}
    finally:lib.rm_close(handle)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--repetitions',type=int,default=20);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 1<=args.repetitions<=100:p.error('repetitions must be 1..100')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'requestedClockHz':4000000,
        'actualClockMeasured':False,'fixtureRetained':True}
    try:
        report['idleControls']=controls(args.device_id,args.repetitions,False)
        report['loadedControls']=controls(args.device_id,args.repetitions,True)
        report['cSdk']=c_sdk(args.device_id);report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

if __name__=='__main__':main()
