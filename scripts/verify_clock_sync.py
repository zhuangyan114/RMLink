"""Real ESP32 clock mapping, owner isolation and native C SDK; no target SWD."""
import argparse
import ctypes
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-id',default='50787d15a924')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();api=ApiClient()
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'scope':'ESP32 clock mapping; no STM32 access'}
    try:
        deadline=time.perf_counter()+30
        while time.perf_counter()<deadline:
            devices=api.request('GET','/v1/status')['devices']
            device=next((d for d in devices if d['device_id']==args.device_id and d['connected']),None)
            if device:break
            time.sleep(.5)
        else:raise ConnectionError('Device not ready')
        if device['owner'] is not None:raise RuntimeError('Clock acceptance cannot claim an occupied target')
        measurements=[api.time_sync(args.device_id,8) for _ in range(10)]
        uncertainty=[r['mapping']['uncertainty_ns']/1e6 for r in measurements]
        report.update(clockEpoch=measurements[0]['clock_epoch'],uncertaintyMedianMs=statistics.median(uncertainty),
            uncertaintyRangeMs=[min(uncertainty),max(uncertainty)],measurements=measurements)
        assert all(r['clock_epoch']==measurements[0]['clock_epoch'] for r in measurements)
        with api.open(args.device_id):
            assert api.exchange(b'\x02\x01')==b'\x02\x01' # reserve only, no DP/AP read
            foreign=ApiClient();foreign.time_sync(args.device_id,4)
            status=api.request('GET','/v1/status')
            assert next(d for d in status['devices'] if d['device_id']==args.device_id)['owner']==api.session
            report['foreignClockQueryPreservesOwner']=True
        dll=ROOT/'artifacts/build/sdk/rmlink.dll'
        lib=ctypes.CDLL(str(dll));lib.rm_open.argtypes=[ctypes.c_char_p,ctypes.c_uint16,ctypes.c_char_p];lib.rm_open.restype=ctypes.c_void_p
        lib.rm_time_sync.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        lib.rm_close.argtypes=[ctypes.c_void_p]
        client=lib.rm_open(None,3243,None);assert client
        try:
            output=ctypes.create_string_buffer(8192);required=ctypes.c_size_t()
            assert lib.rm_time_sync(client,args.device_id.encode(),4,output,len(output),ctypes.byref(required))==200
            assert json.loads(output.value)['samples']==4
            report['nativeCDiscoveryOnlyClockQuery']=True
        finally:lib.rm_close(client)
        report['complete']=True
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='measurements'}))

if __name__=='__main__':main()
