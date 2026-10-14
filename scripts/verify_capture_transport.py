"""Real probe fault-path acceptance; cannot establish target sampling performance."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.capture_protocol import FrameDecoder,decode_record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--expect-inaccessible-target',action='store_true')
    args=p.parse_args()
    if not args.expect_inaccessible_target:p.error('This script explicitly checks unavailable-target handling')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'scope':'ESP32 protocol/stream/fault handling; STM32 remains inaccessible'}
    api=ApiClient();device=None;ready_deadline=time.perf_counter()+30
    while time.perf_counter()<ready_deadline:
        snap=api.request('GET','/v1/status')
        device=next((d for d in snap['devices'] if d['device_id']==args.device_id and d['connected']),None)
        if device:break
        time.sleep(.5)
    if not device:raise ConnectionError('Probe did not become ready within 30 seconds')
    stream=None
    try:
        with api.open(args.device_id):
            def command(sub,payload=b''):
                r=api.exchange(bytes([0x81,sub])+payload)
                if r[:3]!=bytes([0x81,sub,0]):raise AssertionError(r.hex())
                return r[3:]
            assert api.exchange(b'\x11'+struct.pack('<I',4000000))==b'\x11\x00'
            assert api.exchange(b'\x02\x01')==b'\x02\x01'
            report['capabilities']=command(0).hex()
            with ApiClient().open(args.device_id) as foreign:
                assert foreign.exchange(b'\x81\x0b')==b'\x81\xff'
                report['foreignDenied']=True
            ticket=command(11)
            wrong=socket.create_connection((device['address'],3244),3);wrong.sendall(b'RMS1'+b'\0'*16)
            assert wrong.recv(1)==b'';wrong.close();report['badTicketDenied']=True
            stream=socket.create_connection((device['address'],3244),3);stream.sendall(b'RMS1'+ticket)
            assert stream.recv(4)==b'OKAY';stream.settimeout(3)
            for sid,kind,addr in ((1,1,0x20000100),(2,2,0x20000100),(3,3,0x20000400)):
                command(1,struct.pack('<BBBIII',sid,kind,1,1000 if sid==1 else 100000,0,0))
                command(2,bytes([sid,0,1])+struct.pack('<I',addr));command(3,bytes([sid]))
            parser=FrameDecoder();frames=[];deadline=time.perf_counter()+3
            while len(frames)<2 and time.perf_counter()<deadline:frames.extend(parser.feed(stream.recv(4096)))
            samples=[decode_record(f) for f in frames]
            assert len(samples)>=2 and all(s['flags']&1 and s['valid_bitmap']==0 for s in samples)
            report['partialSamples']=samples
            report['subscriptionStates']={}
            for sid in (1,2,3):
                stats=command(7,bytes([sid]));state,kind,count,cap,seq,completed,missed,dropped,failures,overrun,reason=struct.unpack('<BBBIIQQQQIB',stats)
                assert state==3 and failures>=1
                report['subscriptionStates'][sid]={'state':state,'failures':failures,'completed':completed}
            report['complete']=True
        stream.settimeout(3)
        assert stream.recv(1)==b'';report['ownerCloseEndsStream']=True
    finally:
        if stream:stream.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)

if __name__=='__main__':main()
