"""Same F103 64KiB fixture: interleave host/probe Flash, independently verify.

Explicit target-write opt-in; retains the fixture. Total includes staging on the
PC, ESP32 upload (probe backend), target burn and independent host verification.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write',action='store_true');p.add_argument('--repetitions',type=int,default=5)
    p.add_argument('--backends',nargs='+',choices=('host','probe','auto'),default=['host','probe'])
    p.add_argument('--transport',choices=('wifi','usb'),default='wifi')
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not args.allow_target_write:p.error('Target Flash write authorization required')
    if not 1<=args.repetitions<=10:p.error('repetitions must be 1..10')
    if args.output.exists():p.error('Output exists; preserve prior results')
    image=(ROOT/'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes();sha=hashlib.sha256(image).hexdigest()
    if len(image)!=65536 or sha!='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4':raise ValueError('Fixture identity')
    api=ApiClient();report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
        'requestedClockHz':4000000,'actualClockMeasured':False,'fixtureSha256':sha,'fixtureRetained':True,
        'transport':args.transport,'repetitions':args.repetitions,'rows':[]}
    try:
        deadline=time.monotonic()+10
        while True:
            report['serviceBefore']=api.request('GET','/v1/status')
            device=next((d for d in report['serviceBefore']['devices'] if d['device_id']==args.device_id),None)
            if device is not None and device.get('connected'):break
            if time.monotonic()>=deadline:raise TimeoutError('Device not discovered after 10 seconds')
            time.sleep(.2)
        if device['owner'] is not None:raise RuntimeError('Target occupied')
        if device['transport']!=args.transport:api.set_transport(args.device_id,args.transport)
        for repetition in range(args.repetitions):
            order=args.backends if repetition%2==0 else list(reversed(args.backends))
            for backend in order:
                row={'repetition':repetition+1,'requestedBackend':backend};report['rows'].append(row)
                with api.open(args.device_id):
                    row['target']=api.target('connect',clock_hz=4000000)
                    path=f'/v1/sessions/{api.session}/flash';begin=time.perf_counter()
                    api.request('POST',path,{'action':'begin','address':0x08000000,'byte_length':len(image),'sha256':sha,'backend':backend})
                    for at in range(0,len(image),4096):api.request('POST',path,{'action':'chunk','offset':at,'data':image[at:at+4096].hex()})
                    row['computerStageMs']=(time.perf_counter()-begin)*1000;start=time.perf_counter()
                    row['result']=api.request('POST',path,{'action':'commit'})
                    row['commitMs']=(time.perf_counter()-start)*1000;row['totalMs']=(time.perf_counter()-begin)*1000
                    assert row['result']['verified'] and row['result']['phase']=='complete'
                    if backend!='auto':assert row['result']['backend']==backend
                    start=time.perf_counter()
                    data=b''.join(bytes.fromhex(api.target('read_memory',address=0x08000000+at,byte_length=4096)['data']) for at in range(0,len(image),4096))
                    assert data==image;row['extraReadbackSha256']=hashlib.sha256(data).hexdigest()
                    row['extraReadbackMs']=(time.perf_counter()-start)*1000
                    row['stateAfter']=api.target('state');assert row['stateAfter']['state']=='Running'
                print(json.dumps({'backend':backend,'repetition':repetition+1,'totalMs':row['totalMs'],
                                 'timings':row['result'].get('timings')}),flush=True)
        report['medians']={name:{k:statistics.median(r[k] for r in report['rows'] if r['requestedBackend']==name)
                               for k in ('computerStageMs','commitMs','totalMs')} for name in args.backends}
        report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        report['serviceAfter']=api.request('GET','/v1/status')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'failure':report.get('failure'),'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
