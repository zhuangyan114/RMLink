"""F103 fixture only: withhold USB IN reads, retain Timeline in PSRAM, recover.

One native SWD owner; no Flash/application-memory writes, reset or halt.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from rmlink_native_usb import UsbClient
from rmlink_swd import SwdSession
from rmlink_connector.capture_protocol import FrameDecoder,decode_record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture-confirmed',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not args.fixture_confirmed:p.error('The F103 fixture must be installed')
    if args.output.exists():p.error('Preserve existing evidence')
    report=dict(startedAt=datetime.now().astimezone().isoformat(),completed=False,
                requestedClockHz=4000000,actualClockMeasured=False,readPauseSeconds=2)
    try:
        with UsbClient(bcd_device=2,serial='50787D15A924') as usb:
            def command(sub,payload=b''):
                reply=usb.exchange(bytes([0x81,sub])+payload)
                if reply[:3]!=bytes([0x81,sub,0]):raise AssertionError(f'Capture command {sub}: {reply.hex()}')
                return reply[3:]
            def state():
                values=struct.unpack('<BBBIIQQQQIB',command(7,b'\x01'))
                return dict(zip(('state','kind','fields','capture','sequence','completed','missed','dropped','failures','budget','reason'),values))
            with SwdSession(usb,4000000) as swd:
                if swd.read32(0x20000000)!=0x524d4c4b:raise AssertionError('Fixture missing')
                capabilities=command(0)
                report['recordBufferBytes']=int.from_bytes(capabilities[13:17],'little')
                if report['recordBufferBytes']<2*1024*1024:raise AssertionError('PSRAM ring not active')
                ticket=command(11);usb.open_stream(ticket)
                decoder=FrameDecoder();frames=last_seq=last_stamp=0
                def receive_for(seconds):
                    nonlocal frames,last_seq,last_stamp
                    begin=time.perf_counter();heartbeat=begin
                    while time.perf_counter()-begin<seconds:
                        data=usb.read_stream()
                        for frame in decoder.feed(data):
                            sample=decode_record(frame)
                            if sample['subscription_id']!=1 or sample['flags']&1 or len(sample['values'])!=10:raise AssertionError('Invalid Timeline frame')
                            if any(v>>24!=i for i,v in enumerate(sample['values'])):raise AssertionError('Wrong dynamic field identity')
                            if last_seq and sample['sequence']<=last_seq:raise AssertionError('Repeated/reordered USB record')
                            if last_stamp and sample['timestamp_us']<=last_stamp:raise AssertionError('Nonmonotonic timestamp')
                            last_seq=sample['sequence'];last_stamp=sample['timestamp_us'];frames+=1
                        if time.perf_counter()>=heartbeat:command(12);heartbeat=time.perf_counter()+.5
                try:
                    command(1,struct.pack('<BBBIIIB',1,1,10,1000,0,0,0))
                    command(2,b'\x01\x00\x0a'+struct.pack('<10I',*[0x20000100+i*64 for i in range(10)]))
                    command(3,b'\x01');receive_for(2)
                    report['beforePause']=state();before=frames
                    begin=time.perf_counter()
                    while time.perf_counter()-begin<2:command(12);time.sleep(.05)
                    report['afterPause']=state()
                    report['framesRetainedWhileBlocked']=report['afterPause']['completed']-report['beforePause']['completed']
                    receive_for(3)
                    report['afterRecovery']=state();report['framesReceivedDuringRecovery']=frames-before
                    report['framesChecked']=frames
                    for row in ('beforePause','afterPause','afterRecovery'):
                        if report[row]['state']!=2 or report[row]['dropped'] or report[row]['failures']:raise AssertionError(f'Capture failed: {row}')
                    if report['framesRetainedWhileBlocked']<1500 or report['framesReceivedDuringRecovery']<4000:raise AssertionError('PSRAM backlog did not recover')
                finally:
                    try:command(4,b'\x01')
                    finally:usb.close_stream()
                if swd.read32(0xe000ed00)!=0x411fc231:raise AssertionError('Control did not recover')
        report['completed']=True
    except BaseException as exc:report['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        report['finishedAt']=datetime.now().astimezone().isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
