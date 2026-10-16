"""Inspect the existing 4 MHz SWD path with RMT and retain fixture Flash/state."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from rmlink_usb import UsbClient  # sets up the shared host import path
from rmlink_connector.client import ApiClient
from fixture_clock import require_fixture_72mhz

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Preserve existing evidence')
    api=ApiClient();key='50787d15a924'
    report=dict(at=datetime.now().astimezone().isoformat(),complete=False,clocks=[])
    try:
        deadline=time.monotonic()+45
        while True:
            device=next((d for d in api.request('GET','/v1/status')['devices'] if d['device_id']==key and d.get('connected')),None)
            if device:break
            if time.monotonic()>=deadline:raise TimeoutError('Probe did not reconnect')
            time.sleep(.5)
        report['device']=device
        if report['device']['owner'] is not None:raise RuntimeError('Target occupied')
        with api.open(key):
            target=api.target('connect',clock_hz=4000000)
            report['targetClock']=require_fixture_72mhz(api)
            initial_state=api.target('state')['state']
            for mode in ('sequence','dp_read'):
                for repetition in range(3):
                    result=api.target('measure_swd_clock',mode=mode)
                    report['clocks'].append(dict(mode=mode,repetition=repetition+1,result=result))
                    if not result['measurement_valid'] or result['requested_clock_hz']!=4000000:
                        raise AssertionError('Invalid RMT capture')
                    if mode=='dp_read' and (result['dap_status']!=1 or result['dp_idcode']!=target['dp_idcode']):
                        raise AssertionError('DP read failed')
                    if api.target('read_memory',address=0xe000ed00,byte_length=4)['data']!='31c21f41':
                        raise AssertionError('CPUID changed')
                    if api.target('state')['state']!=initial_state:raise AssertionError('Core state changed')
            data=b''.join(bytes.fromhex(api.target('read_memory',address=0x08000000+at,byte_length=4096)['data'])
                for at in range(0,65536,4096))
            expected=(ROOT/'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes()
            report['flashSha256']=hashlib.sha256(data).hexdigest()
            if data!=expected:raise AssertionError('Fixture Flash changed')
        report['complete']=True
    except BaseException as error:
        report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(complete=report['complete'],failure=report.get('failure'))))


if __name__=='__main__':main()
