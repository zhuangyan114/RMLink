"""Real wired USB capture fragments and service transport selection; no target writes."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.capture_protocol import decode_record
from rmlink_connector.usb_capture import UsbCaptureReader


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--expect-inaccessible-target',action='store_true')
    args=p.parse_args()
    if not args.expect_inaccessible_target:p.error('This acceptance explicitly expects a failed target sample')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
        'scope':'Real ESP32 wired capture, NO_ACK handling and transport switching; STM32 inaccessible'}
    api=ApiClient();switched=False
    try:
        deadline=time.perf_counter()+30
        while time.perf_counter()<deadline:
            status=api.request('GET','/v1/status')
            device=next((d for d in status['devices'] if d['device_id']==args.device_id and d['connected']),None)
            if device:break
            time.sleep(.5)
        else:raise ConnectionError('Device not ready')
        report['usbSelection']=api.set_transport(args.device_id,'usb');switched=True
        assert report['usbSelection']['connected']
        with api.open(args.device_id):
            assert api.packet_size==64
            assert api.exchange(b'\x11'+struct.pack('<I',4000000))==b'\x11\x00'
            assert api.exchange(b'\x02\x01')==b'\x02\x01'
            report['clockMapping']=api.time_sync(args.device_id,4)['mapping']
            def cmd(sub,data=b''):
                reply=api.exchange(bytes([0x81,sub])+data)
                assert reply[:3]==bytes([0x81,sub,0]),reply.hex()
                return reply
            cmd(1,struct.pack('<BBBIII',1,1,1,1000,0,0));cmd(2,b'\x01\x00\x01'+struct.pack('<I',0x20000100));cmd(3,b'\x01')
            pull=b'\x81\x10'+struct.pack('<IH',1,0)
            first=api.exchange(pull)
            assert first==api.exchange(pull);report['repeatedFragmentStable']=True
            async def receive():
                reader=UsbCaptureReader()
                async def exchange(request):return await asyncio.to_thread(api.exchange,request)
                deadline=time.perf_counter()+3
                while time.perf_counter()<deadline:
                    frame=await reader.read(exchange)
                    if frame is not None:return decode_record(frame)
                    await asyncio.sleep(.001)
                raise TimeoutError('No wired sample')
            sample=asyncio.run(receive());assert sample['flags']&1 and sample['valid_bitmap']==0
            report['partialSample']=sample
            with ApiClient().open(args.device_id) as foreign:
                assert foreign.exchange(b'\x02\x01')==b'\x02\x00';report['foreignControlDenied']=True
            try:api.set_transport(args.device_id,'wifi')
            except Exception:report['switchDuringSessionDenied']=True
            else:raise AssertionError('Transport switched under an active session')
        report['wifiRestored']=api.set_transport(args.device_id,'wifi');switched=False
        assert report['wifiRestored']['connected'];report['complete']=True
    finally:
        if switched:
            try:report['wifiRestored']=ApiClient().set_transport(args.device_id,'wifi')
            except Exception as exc:report['restoreError']=str(exc)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

if __name__=='__main__':main()
