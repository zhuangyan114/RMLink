"""Shared service subscriptions: one probe stream, bounded independent consumers."""
import asyncio
import struct
import os
import sys
import time
import socket
from collections import deque
from .capture_protocol import FrameDecoder, CompactFrameDecoder, KINDS, decode_record
from .usb_capture import UsbCaptureReader
from .recording import Recording
from .tcp_options import configure_probe_tcp, probe_tcp_info


def compile_config(body):
    kind=body.get('kind')
    if kind not in KINDS: raise ValueError('kind must be memory_series, memory_snapshot or rtt_stream')
    addresses=body.get('addresses')
    fields=body.get('fields')
    if fields is not None:
        if addresses is not None or not isinstance(fields,list) or not 1<=len(fields)<=32 or any(not isinstance(f,dict) for f in fields):
            raise ValueError('Choose addresses or fields')
        addresses=[f.get('address') for f in fields]
    if kind=='rtt_stream':
        if fields is not None or addresses is not None:raise ValueError('RTT uses control_block_address')
        addresses=[body.get('control_block_address')]
    if fields is None: fields=[{'address':a,'offsets':[],'byte_length':4} for a in addresses] if isinstance(addresses,list) else []
    for f in fields:
        offsets=f.get('offsets',[])
        if not isinstance(offsets,list) or len(offsets)>4 or any(type(v) is not int or not -2**31<=v<2**31 for v in offsets) or type(f.get('byte_length',4)) is not int or f.get('byte_length',4) not in (1,2,4):
            raise ValueError('Bounded pointer offsets and width required')
        if kind=='rtt_stream' and offsets: raise ValueError('RTT requires explicit control block address')
    if not isinstance(addresses,list) or not 1<=len(addresses)<=32 or any(type(a) is not int or not (
        0x20000000<=a<=0x20004fff or (kind!='rtt_stream' and 0x08000000<=a<=0x0800ffff)) for a in addresses):
        raise ValueError('Supported aligned RAM/Flash addresses required')
    for a,f in zip(addresses,fields):
        width=4 if f.get('offsets') else f.get('byte_length',4)
        if a%width or not (0x20000000<=a and a+width<=0x20005000 or kind!='rtt_stream' and 0x08000000<=a and a+width<=0x08010000):
            raise ValueError('Field width/alignment/range invalid')
    period=body.get('period_us',5000 if kind=='rtt_stream' else 100000 if kind=='memory_snapshot' else 1000)
    limit=body.get('sample_limit',0); channel=body.get('channel',0)
    if type(period) is not int or not 200<=period<=10000000 or type(limit) is not int or not 0<=limit<=2**32-1:
        raise ValueError('Invalid period or sample_limit')
    if type(channel) is not int or not 0<=channel<16: raise ValueError('RTT channel invalid')
    sid={'memory_series':1,'memory_snapshot':2,'rtt_stream':3}[kind]
    if kind=='rtt_stream' and channel: sid=4
    strict=body.get('strict_deadlines',False)
    if type(strict) is not bool:raise ValueError('strict_deadlines must be boolean')
    return dict(subscription_id=sid,kind=kind,addresses=addresses,fields=fields,period_us=period,sample_limit=limit,channel=channel,strict_deadlines=strict)


class Subscriptions:
    def __init__(self,target):
        self.target=target
        self.configs={}; self.task=None; self.reader=self.writer=None
        self.records=deque(maxlen=4096)
        self.condition=asyncio.Condition()
        self.next_sequence=0; self.host_dropped=0; self.error=None
        self.paused_for_control=set()
        self.halted_for_control=False
        self.compact_stream=False
        self.delta_stream=False
        self.recording=None
        self.observed={}
        self.last_status={}

    def accept_frames(self,frames):
        for frame in frames:
            sid=frame[6]
            capture=int.from_bytes(frame[8:12],'little')
            timestamp=int.from_bytes(frame[36:44],'little')
            observed=self.observed.get(sid)
            if observed is None or observed['capture_id']!=capture:
                observed=self.observed[sid]=dict(capture_id=capture,frames=0,first_us=timestamp,last_us=timestamp)
            observed['frames']+=1;observed['last_us']=timestamp
            cursor=self.next_sequence
            if self.recording:self.recording.offer(cursor,frame)
            if len(self.records)==self.records.maxlen:self.host_dropped+=1
            self.records.append((cursor,frame));self.next_sequence+=1

    async def start_recording(self,directory,body):
        if self.recording and any(task and not task.done() for task in (self.recording.task,self.recording.failure_task)):
            raise ValueError('Recording already active or finalizing')
        ids=body.get('subscription_ids',list(self.configs));strict=body.get('strict',True)
        if type(strict) is not bool or not isinstance(ids,list) or not ids or any(type(i) is not int or i not in self.configs for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Existing subscription_ids and boolean strict required')
        configs={i:self.configs[i] for i in ids}
        async def failed():
            for sid in ids:
                async with self.target.lock:
                    if self.configs.get(sid,{}).get('capture_id')!=configs[sid]['capture_id']:continue
                    state=await self.target.vendor(7,bytes([sid]))
                    if state[0]==2:await self.target.vendor(5,bytes([sid]))
        recording=Recording(directory,self.target.broker.device_id,configs,failed,strict)
        result=await recording.start();self.recording=recording;return result

    async def _stream(self):
        decoder=CompactFrameDecoder(delta=self.delta_stream) if self.compact_stream else FrameDecoder()
        diagnostics=os.environ.get('RMLINK_STREAM_DIAGNOSTICS')=='1'
        window=time.perf_counter() if diagnostics else 0; wire=received=max_wait=max_decode=0
        if diagnostics:
            sock=self.writer.get_extra_info('socket')
            print(f'RMLink capture reader rcvbuf={sock.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)} compact={self.compact_stream}',file=sys.stderr,flush=True)
        try:
            while True:
                before=time.perf_counter() if diagnostics else 0
                data=await self.reader.read(4096)
                if not data: raise ConnectionError('Probe capture stream closed')
                traffic=getattr(self.target.broker.transport,'traffic',None)
                if traffic:traffic.rx_bytes+=len(data)
                read_at=time.perf_counter() if diagnostics else 0
                frames=decoder.feed(data)
                decoded_at=time.perf_counter() if diagnostics else 0
                async with self.condition:
                    self.accept_frames(frames)
                    self.condition.notify_all()
                if diagnostics:
                    wire+=len(data);received+=len(frames)
                    max_wait=max(max_wait,read_at-before);max_decode=max(max_decode,decoded_at-read_at)
                    if decoded_at-window>=10:
                        counters=probe_tcp_info(self.writer.get_extra_info('socket'))
                        print(f'RMLink capture reader seconds={decoded_at-window:.3f} bytes={wire} frames={received} max_read_wait_ms={max_wait*1000:.3f} max_decode_ms={max_decode*1000:.3f} tcp={counters}',file=sys.stderr,flush=True)
                        window=decoded_at;wire=received=max_wait=max_decode=0
        except asyncio.CancelledError: pass
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
            if self.recording and self.recording.accepting:self.recording.fail('capture_stream_failed')
            async with self.condition: self.condition.notify_all()

    async def _usb_stream(self):
        reader=UsbCaptureReader();decoder=FrameDecoder()
        async def exchange(packet):
            async with self.target.lock:return await self.target.packet(packet,'background')
        try:
            while True:
                data=await reader.read(exchange)
                if data is None:await asyncio.sleep(.001);continue
                frames=decoder.feed(data)
                async with self.condition:
                    self.accept_frames(frames)
                    self.condition.notify_all()
        except asyncio.CancelledError:pass
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
            if self.recording and self.recording.accepting:self.recording.fail('capture_stream_failed')
            async with self.condition:self.condition.notify_all()

    async def ensure_stream(self):
        if self.task and not self.task.done(): return
        if self.error:raise ConnectionError('Capture stream failed; close the owner session before reconnecting')
        if getattr(self.target.broker.transport,'native_usb',False):
            self.error=None;self.task=asyncio.create_task(self._usb_stream());return
        ticket=await self.target.vendor(11)
        if len(ticket)!=16: raise ValueError('Stream ticket size')
        host=self.target.broker.transport.host
        self.reader,self.writer=await asyncio.wait_for(asyncio.open_connection(host,3244),3)
        configure_probe_tcp(self.writer.get_extra_info('socket'))
        self.delta_stream=bool(self.target.capture_features&512)
        self.compact_stream=self.delta_stream or bool(self.target.capture_features&256)
        self.writer.write((b'RMS3' if self.delta_stream else b'RMS2' if self.compact_stream else b'RMS1')+ticket); await self.writer.drain()
        traffic=getattr(self.target.broker.transport,'traffic',None)
        if traffic:traffic.tx_bytes+=4+len(ticket)
        if await asyncio.wait_for(self.reader.readexactly(4),3)!=b'OKAY': raise ConnectionError('Stream binding rejected')
        if traffic:traffic.rx_bytes+=4
        self.error=None; self.task=asyncio.create_task(self._stream())

    async def create(self,body):
        config=compile_config(body)
        if not self.target.connected: await self.target.connect()
        async with self.target.lock:
            await self.ensure_stream()
            sid=config['subscription_id']
            if sid in self.configs: raise ValueError('Subscription slot already exists; stop/dispose it first')
            await self.target.vendor(1,struct.pack('<BBBIIIB',sid,KINDS[config['kind']],len(config['addresses']),
                config['period_us'],config['sample_limit'],config['channel'],int(config['strict_deadlines'])))
            chunk=(self.target.broker.packet_size-5)//4
            for i in range(0,len(config['addresses']),chunk):
                addresses=config['addresses'][i:i+chunk]
                await self.target.vendor(2,bytes([sid,i,len(addresses)])+struct.pack('<'+'I'*len(addresses),*addresses))
            if config['kind']!='rtt_stream':
                for i,f in enumerate(config['fields']):
                    offsets=f.get('offsets',[])
                    await self.target.vendor(15,bytes([sid,i,len(offsets),f.get('byte_length',4)])+struct.pack('<'+'i'*len(offsets),*offsets))
            config['stream_cursor']=self.next_sequence-1
            await self.target.vendor(3,bytes([sid]))
            # Orbit restores Timeline while its launch is still halted. Keep
            # that series paused until the existing owner confirms Continue;
            # otherwise queued pre-Continue frames become a stale backlog.
            if sid==1 and self.halted_for_control:
                await self.target.vendor(5,bytes([sid]));self.paused_for_control.add(sid)
            stats=await self.target.vendor(7,bytes([sid]))
            config['capture_id']=int.from_bytes(stats[3:7],'little')
            self.configs[sid]=config
            return config

    async def action(self,sid,action):
        if sid not in self.configs: raise ValueError('Unknown subscription')
        commands={'stop':4,'pause':5,'resume':6,'status':7}
        if action not in commands: raise ValueError('Unsupported subscription action')
        if action=='resume' and self.error:raise ConnectionError('Capture stream failed; create a new owner session')
        async with self.target.lock: data=await self.target.vendor(commands[action],bytes([sid]))
        if action=='status':
            state,kind,count,capture,seq,frames,missed,dropped,failures,overrun,reason=struct.unpack('<BBBIIQQQQIB',data)
            result=dict(state=state,kind=kind,field_count=count,capture_id=capture,last_sequence=seq,
                completed=frames,deadline_missed=missed,ring_dropped=dropped,failures=failures,
                stop_reason={0:None,1:'paused',2:'stopped',3:'swd_failure',4:'lease_expired',5:'stream_lost',6:'budget_overrun',7:'sample_limit'}.get(reason,'unknown'),
                budget_overrun=overrun,host_history_evicted=self.host_dropped,
                host_dropped=self.host_dropped,stream_error=self.error)
            self.last_status[sid]=dict(result,observed_at=time.monotonic())
            return result
        return {'subscription_id':sid,'action':action}

    async def delete(self,sid):
        if self.recording and self.recording.accepting and str(sid) in self.recording.configs:
            self.recording.fail('subscription_removed')
        await self.action(sid,'stop'); self.configs.pop(sid,None)
        self.paused_for_control.discard(sid)
        return {'disposed':sid}

    async def target_halted(self):
        self.halted_for_control=True
        if 1 in self.configs and (await self.action(1,'status'))['state']==2:
            await self.action(1,'pause');self.paused_for_control.add(1)

    async def target_running(self):
        self.halted_for_control=False
        for sid in list(self.paused_for_control):
            if sid in self.configs:await self.action(sid,'resume')
            self.paused_for_control.discard(sid)

    async def read(self,after=-1,limit=128,wait_ms=1000):
        if type(after) is not int or after< -1 or type(limit) is not int or not 1<=limit<=256 or type(wait_ms) is not int or not 0<=wait_ms<=5000:
            raise ValueError('Invalid stream cursor/limit/timeout')
        async with self.condition:
            if not any(seq>after for seq,_ in self.records) and not self.error:
                try: await asyncio.wait_for(self.condition.wait(),wait_ms/1000)
                except TimeoutError: pass
            rows=[(seq,frame) for seq,frame in self.records if seq>after][:limit]
            gap=self.records[0][0]-after-1 if self.records and after>=0 else 0
            return {'records': rows,
                    'cursor':rows[-1][0] if rows else after,'consumer_gap':max(0,gap),
                    'host_history_evicted':self.host_dropped,
                    'host_dropped':self.host_dropped,'error':self.error}

    async def poll(self,after=-1,limit=128,wait_ms=1000):
        result=await self.read(after,limit,wait_ms)
        result['records']=[{'cursor':seq,'frame':frame.hex()} for seq,frame in result['records']]
        return result

    async def heartbeat(self):
        if self.configs:
            async with self.target.lock: await self.target.vendor(12)

    async def close(self):
        if self.recording:
            if self.recording.accepting:self.recording.fail('session_closed')
            await self.recording.stop()
        for sid in list(self.configs):
            try: await self.action(sid,'stop')
            except Exception: pass
        if self.task: self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        if self.writer:
            self.writer.close()
            try: await self.writer.wait_closed()
            except OSError: pass
        self.configs.clear()
