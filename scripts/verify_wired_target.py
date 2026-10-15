"""Valid STM32 subscriptions via native USB64; restore Wi-Fi preference on exit."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from verify_subscriptions import strict_recording


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--seconds',type=float,default=10);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 2<=args.seconds<=300:p.error('seconds must be 2..300')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'transport':'native USB64',
        'requestedClockHz':4000000,'actualClockMeasured':False,'fixtureRetained':True}
    api=ApiClient();switched=False
    try:
        report['selection']=api.set_transport(args.device_id,'usb');switched=True
        assert report['selection']['connected']
        with api.open(args.device_id):
            assert api.packet_size==64;report['target']=api.target('connect')
            report['coreState']=api.target('state');assert report['coreState']['state']=='Running'
            assert api.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52'
            report['threeSubscriptions']=strict_recording(api,args.seconds)
        report['wifiRestored']=api.set_transport(args.device_id,'wifi');switched=False
        assert report['wifiRestored']['connected'];report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        if switched:
            try:report['wifiRestored']=ApiClient().set_transport(args.device_id,'wifi')
            except Exception as error:report['restoreError']=str(error)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

if __name__=='__main__':main()
