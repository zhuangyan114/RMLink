"""F103 fixture: provoke a DP SELECT parity error, verify read-only recovery.

The malformed write addresses the debug port's SELECT register only. It never
addresses application memory or Flash. No reset/halt, and one native SWD owner.
"""
import argparse
from datetime import datetime
import hashlib
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
    p.add_argument('--trials',type=int,default=10)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not args.fixture_confirmed or not 1<=args.trials<=20:p.error('Confirm fixture; trials must be 1..20')
    if args.output.exists():p.error('Preserve previous evidence')
    report=dict(startedAt=datetime.now().astimezone().isoformat(),completed=False,trials=[],
                requestedClockHz=4000000,actualClockMeasured=False)
    try:
        with UsbClient(bcd_device=2,serial='50787D15A924') as usb,SwdSession(usb,4000000) as swd:
            if swd.read32(0x20000000)!=0x524d4c4b:raise AssertionError('Fixture missing')
            def flash_hash():return hashlib.sha256(struct.pack('<16384I',*swd.read_words(0x08000000,16384,pipelined=True))).hexdigest()
            report['flashBefore']=flash_hash();options=swd.read_words(0x1ffff800,4)
            def command(sub,payload=b''):
                reply=usb.exchange(bytes([0x81,sub])+payload)
                if reply[:3]!=bytes([0x81,sub,0]):raise AssertionError(f'Capture {sub}: {reply.hex()}')
                return reply[3:]
            def stats():
                reply=usb.exchange(b'\x80\x0d')
                if len(reply)!=32 or reply[:4]!=b'\x80\x0d\0\x01':raise AssertionError('Read fault recovery unsupported')
                return dict(zip(('attempts','recovered','exhausted','write_faults_not_replayed','last_dp_ctrl_stat','last_fault_uptime_us'),struct.unpack_from('<5IQ',reply,4)))
            def inject():
                # DP SELECT write header B1; five input clocks cover both
                # turnarounds and ACK. SELECT=0 with deliberately wrong parity.
                reply=usb.exchange(bytes([0x1d,4,8,0xb1,0x85,33,0,0,0,0,1,8,0]))
                if len(reply)!=3 or reply[:2]!=b'\x1d\0' or (reply[2]>>1)&7!=1:raise AssertionError('DP SELECT ACK')
                ctrl=swd.dp_read(4)
                if ctrl&0xb2!=0x80:raise AssertionError(f'Expected WDATAERR only: {ctrl:#x}')
                return ctrl
            report['before']=stats()
            for trial in range(args.trials):
                command(1,struct.pack('<BBBIIIB',1,1,4,1000,100,0,0))
                command(2,b'\x01\x00\x04'+struct.pack('<4I',*[0x20000100+i*64 for i in range(4)]))
                usb.open_stream(command(11));decoder=FrameDecoder();frames=0
                try:
                    ctrl=inject();command(3,b'\x01')
                    state=command(7,b'\x01');capture=int.from_bytes(state[3:7],'little')
                    deadline=time.perf_counter()+3
                    while frames<20:
                        if time.perf_counter()>deadline:raise TimeoutError('Recovery produced no frames')
                        for frame in decoder.feed(usb.read_stream()):
                            sample=decode_record(frame)
                            if sample['capture_id']!=capture:continue
                            if sample['flags']&1 or any(v>>24!=i for i,v in enumerate(sample['values'])):raise AssertionError('Invalid recovered sample')
                            frames+=1
                    state=struct.unpack('<BBBIIQQQQIB',command(7,b'\x01'))
                    if state[7] or state[8]:raise AssertionError('Capture dropped/failed after recovery')
                finally:
                    try:command(4,b'\x01')
                    finally:usb.close_stream()
                if swd.dp_read(4)&0xb2:raise AssertionError('Sticky DP error remains')
                current=stats()
                if current['recovered']!=report['before']['recovered']+trial+1:raise AssertionError('Recovery counter mismatch')
                report['trials'].append(dict(trial=trial+1,injectedCtrlStat=hex(ctrl),validFrames=frames))
            inject()
            if command(20,struct.pack('<IH',0x20000000,4))!=b'KLMR':raise AssertionError('Read-memory recovery')
            inject()
            if int.from_bytes(command(17),'little')&0x20000:raise AssertionError('Core halted')
            report['after']=stats()
            if report['after']['recovered']-report['before']['recovered']!=args.trials+2:raise AssertionError('Control recovery counters')
            if report['after']['exhausted']!=report['before']['exhausted']:raise AssertionError('Recovery exhausted')
            if report['after']['write_faults_not_replayed']!=report['before']['write_faults_not_replayed']:raise AssertionError('Unexpected target write')
            report['flashAfter']=flash_hash()
            if report['flashBefore']!=report['flashAfter'] or swd.read_words(0x1ffff800,4)!=options:raise AssertionError('Flash or options changed')
            report['running']=not bool(swd.read32(0xe000edf0)&0x20000)
            if not report['running']:raise AssertionError('Target stopped')
        report['completed']=True
    except BaseException as exc:report['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        report['finishedAt']=datetime.now().astimezone().isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
