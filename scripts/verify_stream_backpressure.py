"""F103 fixture only: deliberately stop reading TCP3244, then check recovery.

Uses one public raw-DAP lease and its own capture receiver. Does not start the
high-level target engine or a second SWD session. No Flash/RAM application writes,
halt or reset. Intentional overflow is separate from a loss-free soak.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.capture_protocol import FrameDecoder,decode_record
from rmlink_swd import SwdSession
from verify_subscription_soak import Measurements
from benchmark_pointer_subscriptions import require


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--fixture-confirmed',action='store_true')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not args.fixture_confirmed:p.error('Requires the RMLink F103 fixture')
    if args.output.exists():p.error('Output exists; preserve previous evidence')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
            'requestedClockHz':4000000,'actualClockMeasured':False,
            'scope':'intentional TCP receive backpressure; ten static sparse dynamic leaves, Watch and RTT; not a loss-free soak or natural-failure diagnosis',
            'timelinePeriodUs':1000,'watchPeriodUs':100000,'rttPeriodUs':5000,
            'fixtureFlashRetained':True,'phases':[]}
    api=ApiClient();stream=None;ids=[];receiver=None
    stop=threading.Event();reading=threading.Event();io_lock=threading.Lock();data_lock=threading.Lock()
    try:
        device=next(d for d in api.request('GET','/v1/devices') if d['device_id']==args.device_id)
        require(device['owner'] is None,'Target occupied')
        report['deviceBefore']=device
        with api.open(args.device_id):
            def command(sub,payload=b''):
                reply=api.exchange(bytes([0x81,sub])+payload)
                require(reply[:3]==bytes([0x81,sub,0]),f'Capture command failed: {reply.hex()}')
                return reply[3:]
            def states():
                result={}
                for sid in ids:
                    data=command(7,bytes([sid]))
                    state,kind,count,capture,seq,completed,missed,dropped,failures,overrun,reason=struct.unpack('<BBBIIQQQQIB',data)
                    result[str(sid)]=dict(state=state,kind=kind,capture_id=capture,completed=completed,
                        last_sequence=seq,deadline_missed=missed,ring_dropped=dropped,
                        failures=failures,budget_overrun=overrun,stop_reason=reason)
                return result
            with SwdSession(api,4000000) as swd:
                require(swd.read32(0xe000ed00)==0x411fc231,'Expected Cortex-M3')
                require(swd.read32(0x20000000)==0x524d4c4b,'Fixture missing')
                require(not swd.read32(0xe000edf0)&0x20000,'Target is halted')
                ticket=command(11);require(len(ticket)==16,'Stream ticket size')
                stream=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
                stream.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,8192)
                stream.settimeout(3);stream.connect((device['address'],3244))
                report['receiveBufferBytes']=stream.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
                stream.sendall(b'RMS1'+ticket)
                hello=bytearray()
                while len(hello)<4:
                    part=stream.recv(4-len(hello));require(part,'Stream handshake EOF');hello.extend(part)
                require(hello==b'OKAY','Stream binding rejected');stream.settimeout(.1)
                try:
                    for sid,kind,addresses,period in [
                        (1,1,[0x20000100+i*64 for i in range(10)],1000),
                        (2,2,[0x20000100+i*64 for i in range(4)],100000),
                        (3,3,[0x20000400],5000)]:
                        command(1,struct.pack('<BBBIIIB',sid,kind,len(addresses),period,0,0,0));ids.append(sid)
                        command(2,bytes([sid,0,len(addresses)])+struct.pack('<'+'I'*len(addresses),*addresses))
                        command(3,bytes([sid]))
                    decoder=FrameDecoder();measure=Measurements();receiver_errors=[]
                    def receive():
                        try:
                            while not stop.is_set():
                                if not reading.wait(.05):continue
                                with io_lock:
                                    if not reading.is_set() or stop.is_set():continue
                                    try:data=stream.recv(4096)
                                    except socket.timeout:continue
                                    require(data,'Stream closed under backpressure')
                                with data_lock:
                                    for frame in decoder.feed(data):measure.accept(0,decode_record(frame))
                        except BaseException as error:
                            if not stop.is_set():receiver_errors.append(f'{type(error).__name__}: {error}')
                    def measurements():
                        with data_lock:return measure.summary()
                    reading.set();receiver=threading.Thread(target=receive,daemon=True);receiver.start()
                    def receive_for(seconds):
                        begin=time.perf_counter();heartbeat=begin
                        while time.perf_counter()-begin<seconds:
                            require(not receiver_errors,f'Receiver failed: {receiver_errors}')
                            if time.perf_counter()>=heartbeat:
                                command(12);heartbeat=time.perf_counter()+1
                            time.sleep(.01)
                        return time.perf_counter()-begin
                    receive_for(3)
                    before=states();report['warmup']=dict(statuses=before,measurements=measurements())
                    require(all(s['state']==2 and not s['ring_dropped'] and not s['failures'] for s in before.values()),'Warmup failed')
                    for seconds in (.05,1.5):
                        row={'requestedNoReadSeconds':seconds};report['phases'].append(row)
                        before=states();reading.clear()
                        with io_lock:pass # finish at most one already-started recv
                        begin=time.perf_counter()
                        # Keep control and capture lease alive while withholding
                        # only the data socket. Each wait is bounded to 100 ms.
                        while time.perf_counter()-begin<seconds:
                            command(12);time.sleep(min(.1,max(0,seconds-(time.perf_counter()-begin))))
                        row['actualNoReadSeconds']=time.perf_counter()-begin
                        reading.set() # status traffic must not extend the no-read window
                        row['whileBlocked']=states()
                        row['receiveRecoverySeconds']=receive_for(3)
                        after=states();row['afterRecovery']=after
                        row['dropDelta']={sid:after[sid]['ring_dropped']-before[sid]['ring_dropped'] for sid in before}
                        row['measurements']=measurements()
                        require(all(s['state']==2 and not s['failures'] for s in after.values()),'SWD or stream stopped')
                        require(all(after[sid]['completed']>before[sid]['completed'] for sid in before),'Subscription did not recover')
                        if seconds<.1:require(not any(row['dropDelta'].values()),'Short receive pause unexpectedly overflowed')
                        else:require(row['dropDelta']['1']>0,'Long pause did not exercise board Timeline overflow')
                    before=states();before_measure=measurements()['kinds']['1']
                    settle_seconds=receive_for(3);after=states()
                    after_measure=measurements()['kinds']['1']
                    report['settledRecovery']=dict(observedSeconds=settle_seconds,before=before,after=after,
                        receivedTimelineFrames=after_measure['frames']-before_measure['frames'],
                        receivedTimelineHz=(after_measure['frames']-before_measure['frames'])*1e6/(after_measure['lastUs']-before_measure['lastUs']))
                    require(all(after[sid]['ring_dropped']==before[sid]['ring_dropped'] for sid in before),'Dropping continued after recovery')
                    require(all(after[sid]['state']==2 and not after[sid]['failures'] and
                                after[sid]['completed']>before[sid]['completed'] for sid in before),'Settled subscriptions failed')
                    report['coreStateDuringCapture']=int.from_bytes(command(17),'little')
                    require(not report['coreStateDuringCapture']&0x20000,'Core halted under backpressure')
                    require(not receiver_errors,f'Receiver failed: {receiver_errors}')
                    with data_lock:report['decoderPendingBytes']=len(decoder.pending)
                finally:
                    for sid in reversed(ids):
                        try:command(4,bytes([sid]))
                        except Exception as error:report.setdefault('cleanupErrors',[]).append(str(error))
                    stop.set();reading.set()
                    if receiver:receiver.join(timeout=2)
                    require(not receiver or not receiver.is_alive(),'Receiver did not stop')
                    if stream:stream.close();stream=None
                require(swd.read32(0xe000ed00)==0x411fc231,'Post-overflow CPUID read failed')
                require(swd.read32(0x20000000)==0x524d4c4b,'Fixture changed')
                require(not swd.read32(0xe000edf0)&0x20000,'Target stopped')
        report['deviceAfter']=next(d for d in api.request('GET','/v1/devices') if d['device_id']==args.device_id)
        require(report['deviceAfter']['owner'] is None,'Owner leaked')
        require(not report.get('cleanupErrors'),'Cleanup failed')
        report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        if stream:stream.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'failure':report.get('failure'),'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
