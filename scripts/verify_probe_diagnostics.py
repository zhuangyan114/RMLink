"""Real no-owner health + owner RMT clock capture; retain target Flash/state."""
import argparse
import ctypes as C
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():p.error('Preserve existing evidence')
    api=ApiClient();key='50787d15a924';report=dict(capturedAt=datetime.now().astimezone().isoformat(),complete=False,clocks=[])
    try:
        deadline=time.monotonic()+10
        while True:
            before=api.request('GET','/v1/status');device=next((d for d in before['devices'] if d['device_id']==key and d['connected']),None)
            if device is not None:break
            if time.monotonic()>=deadline:raise TimeoutError('Device not connected')
            time.sleep(.2)
        assert device['owner'] is None and device['connected'];report['device']=device
        report['healthBefore']=api.device_health(key);assert report['healthBefore']['supported']
        assert next(d for d in api.request('GET','/v1/status')['devices'] if d['device_id']==key)['owner'] is None
        # Discovery-only C client also reads health without reserving the target.
        dll=C.CDLL(str(ROOT/'artifacts/build/sdk/rmlink.dll'))
        dll.rm_open.argtypes=[C.c_char_p,C.c_uint16,C.c_char_p];dll.rm_open.restype=C.c_void_p
        dll.rm_close.argtypes=[C.c_void_p]
        dll.rm_device_health.argtypes=[C.c_void_p,C.c_char_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
        client=dll.rm_open(None,3243,None);assert client
        try:
            out=C.create_string_buffer(8192);required=C.c_size_t()
            assert dll.rm_device_health(client,key.encode(),out,len(out),C.byref(required))==200
            report['cDiscoveryHealth']=json.loads(out.value);assert report['cDiscoveryHealth']['supported']
        finally:dll.rm_close(client)
        for clock in (100000,1000000,2000000,4000000):
            with api.open(key):
                target=api.target('connect',clock_hz=clock);state=api.target('state')['state']
                assert api.device_health(key)['requested_swj_clock_hz']==clock
                for mode in ('sequence','dp_read'):
                    for repetition in range(3):
                        result=api.target('measure_swd_clock',mode=mode)
                        report['clocks'].append(dict(clock=clock,mode=mode,repetition=repetition+1,result=result))
                        assert result['measurement_valid'],result
                        assert result['requested_clock_hz']==clock
                        if mode=='dp_read':assert result['dap_status']==1 and result['dp_idcode']==target['dp_idcode']
                        cpuid=api.target('read_memory',address=0xe000ed00,byte_length=4)['data']
                        assert int.from_bytes(bytes.fromhex(cpuid),'little')==0x411fc231
                        assert api.target('state')['state']==state
        with api.open(key):
            api.target('connect',clock_hz=4000000)
            data=b''.join(bytes.fromhex(api.target('read_memory',address=0x08000000+at,byte_length=4096)['data']) for at in range(0,65536,4096))
            report['finalFlashSha256']=hashlib.sha256(data).hexdigest()
            assert report['finalFlashSha256']=='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4'
        report['healthAfter']=api.device_health(key);report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        if api.session:api.close()
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'failure':report.get('failure'),'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
