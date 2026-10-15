"""Real WinHTTP C ABI: short output is not replayed, parallel cancel, recovery."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]
SHA='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write',action='store_true');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not args.allow_target_write:p.error('Target-write opt-in required')
    if args.output.exists():p.error('Choose a new evidence file')
    image=(ROOT/'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest()==SHA
    path=ROOT/'artifacts/build/sdk/rmlink.dll';dll=C.CDLL(str(path))
    dll.rm_open.argtypes=[C.c_char_p,C.c_uint16,C.c_char_p];dll.rm_open.restype=C.c_void_p
    dll.rm_close.argtypes=[C.c_void_p]
    dll.rm_call.argtypes=[C.c_void_p,C.c_char_p,C.c_char_p,C.c_char_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
    dll.rm_call.restype=C.c_int
    report=dict(capturedAt=datetime.now().astimezone().isoformat(),complete=False,
                dllSha256=hashlib.sha256(path.read_bytes()).hexdigest(),checks={})
    client=None
    try:
        client=dll.rm_open(b'50787d15a924',3243,None);assert client,'Cannot open C SDK owner'
        def call(method,suffix,body=None,capacity=16384):
            out=C.create_string_buffer(capacity);required=C.c_size_t()
            status=dll.rm_call(client,method.encode(),suffix.encode(),
                None if body is None else json.dumps(body,separators=(',',':')).encode(),out,capacity,C.byref(required))
            return status,json.loads(out.value) if status>0 else None,required.value
        def ok(method,suffix,body=None):
            status,value,required=call(method,suffix,body)
            assert status==200,(method,suffix,status,value,required)
            return value
        def stage(data):
            ok('POST','/flash',dict(action='begin',address=0x08000000,byte_length=len(data),
                                  sha256=hashlib.sha256(data).hexdigest(),backend='probe'))
            for at in range(0,len(data),4096):ok('POST','/flash',dict(action='chunk',offset=at,data=data[at:at+4096].hex()))
        def readback():
            return b''.join(bytes.fromhex(ok('POST','/target',dict(command='read_memory',address=0x08000000+at,byte_length=4096))['data'])
                            for at in range(0,len(image),4096))
        report['target']=ok('POST','/target',dict(command='connect',clock_hz=4000000))
        stage(image[:1024]);status,_,required=call('POST','/flash',{'action':'commit'},capacity=16)
        assert status==-122 and required>16,(status,required)
        report['shortOutput']={'return':status,'required':required,'progress':ok('GET','/flash')}
        assert report['shortOutput']['progress']['verified'] and report['shortOutput']['progress']['phase']=='complete'
        status,_,_=call('POST','/flash',{'action':'commit'});assert status==400
        assert ok('GET','/flash')['phase']=='complete'
        assert readback()==image
        report['checks']['shortOutputDoesNotReplayAndGetRetainsResult']=True
        report['checks']['partialFirstPagePreservesOtherPages']=True
        stage(image)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(call,'POST','/flash',{'action':'commit'})
            deadline=time.monotonic()+15;cancelled=False
            while not future.done():
                state=ok('GET','/flash')
                if state.get('busy') and state.get('erased_pages',0)>=1:
                    report['beforeCancel']=state;report['cancelReply']=ok('POST','/flash',{'action':'cancel'})
                    cancelled=True;break
                if time.monotonic()>deadline:raise TimeoutError('C commit did not enter erase/program')
                time.sleep(.01)
            status,error,_=future.result(timeout=30)
            assert cancelled and status==409 and error['code']=='FlashCancelled',(cancelled,status,error)
            assert error['progress']['halt_confirmed'];report['cancelled']=error
        assert ok('POST','/target',{'command':'state'})['state']=='Halted'
        report['checks']['parallelWinHttpCancelStopsTarget']=True
        stage(image);report['recovery']=ok('POST','/flash',{'action':'commit'})
        assert report['recovery']['verified'] and report['recovery']['sha256']==SHA
        data=readback()
        assert data==image;assert ok('POST','/target',{'command':'state'})['state']=='Running'
        report['fixtureSha256']=hashlib.sha256(data).hexdigest();report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        if client:dll.rm_close(client)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
