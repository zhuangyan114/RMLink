"""Shared service subscriptions: one probe stream, bounded independent consumers."""
import asyncio
import struct
from collections import deque
from .capture_protocol import FrameDecoder, KINDS, decode_record


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
    return dict(subscription_id=sid,kind=kind,addresses=addresses,fields=fields,period_us=period,sample_limit=limit,channel=channel)


class Subscriptions:
    def __init__(self,target):
        self.target=target
        self.configs={}; self.task=None; self.reader=self.writer=None
        self.records=deque(maxlen=4096)
        self.condition=asyncio.Condition()
        self.next_sequence=0; self.host_dropped=0; self.error=None
        self.paused_for_control=set()

    async def _stream(self):
        decoder=FrameDecoder()
        try:
            while True:
                data=await self.reader.read(4096)
                if not data: raise ConnectionError('Probe capture stream closed')
                frames=decoder.feed(data)
                async with self.condition:
                    for frame in frames:
                        if len(self.records)==self.records.maxlen: self.host_dropped+=1
                        self.records.append((self.next_sequence,frame)); self.next_sequence+=1
                    self.condition.notify_all()
        except asyncio.CancelledError: pass
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
            async with self.condition: self.condition.notify_all()

    async def ensure_stream(self):
        if self.task and not self.task.done(): return
        ticket=await self.target.vendor(11)
        if len(ticket)!=16: raise ValueError('Stream ticket size')
        host=self.target.broker.transport.host
        self.reader,self.writer=await asyncio.wait_for(asyncio.open_connection(host,3244),3)
        self.writer.write(b'RMS1'+ticket); await self.writer.drain()
        if await asyncio.wait_for(self.reader.readexactly(4),3)!=b'OKAY': raise ConnectionError('Stream binding rejected')
        self.error=None; self.task=asyncio.create_task(self._stream())

    async def create(self,body):
        config=compile_config(body)
        if not self.target.connected: await self.target.connect()
        async with self.target.lock:
            await self.ensure_stream()
            sid=config['subscription_id']
            if sid in self.configs: raise ValueError('Subscription slot already exists; stop/dispose it first')
            await self.target.vendor(1,struct.pack('<BBBIII',sid,KINDS[config['kind']],len(config['addresses']),
                config['period_us'],config['sample_limit'],config['channel']))
            chunk=(self.target.broker.packet_size-5)//4
            for i in range(0,len(config['addresses']),chunk):
                addresses=config['addresses'][i:i+chunk]
                await self.target.vendor(2,bytes([sid,i,len(addresses)])+struct.pack('<'+'I'*len(addresses),*addresses))
            if config['kind']!='rtt_stream':
                for i,f in enumerate(config['fields']):
                    offsets=f.get('offsets',[])
                    await self.target.vendor(15,bytes([sid,i,len(offsets),f.get('byte_length',4)])+struct.pack('<'+'i'*len(offsets),*offsets))
            await self.target.vendor(3,bytes([sid]))
            stats=await self.target.vendor(7,bytes([sid]))
            config['capture_id']=int.from_bytes(stats[3:7],'little')
            self.configs[sid]=config
            return config

    async def action(self,sid,action):
        if sid not in self.configs: raise ValueError('Unknown subscription')
        commands={'stop':4,'pause':5,'resume':6,'status':7}
        if action not in commands: raise ValueError('Unsupported subscription action')
        async with self.target.lock: data=await self.target.vendor(commands[action],bytes([sid]))
        if action=='status':
            state,kind,count,capture,seq,frames,missed,dropped,failures,overrun,reason=struct.unpack('<BBBIIQQQQIB',data)
            return dict(state=state,kind=kind,field_count=count,capture_id=capture,last_sequence=seq,
                completed=frames,deadline_missed=missed,ring_dropped=dropped,failures=failures,
                stop_reason={0:None,1:'paused',2:'stopped',3:'swd_failure',4:'lease_expired',5:'stream_lost',6:'budget_overrun',7:'sample_limit'}.get(reason,'unknown'),
                budget_overrun=overrun,host_dropped=self.host_dropped,stream_error=self.error)
        return {'subscription_id':sid,'action':action}

    async def delete(self,sid):
        await self.action(sid,'stop'); self.configs.pop(sid,None)
        self.paused_for_control.discard(sid)
        return {'disposed':sid}

    async def target_halted(self):
        if 1 in self.configs and (await self.action(1,'status'))['state']==2:
            await self.action(1,'pause');self.paused_for_control.add(1)

    async def target_running(self):
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
                    'host_dropped':self.host_dropped,'error':self.error}

    async def poll(self,after=-1,limit=128,wait_ms=1000):
        result=await self.read(after,limit,wait_ms)
        result['records']=[{'cursor':seq,'frame':frame.hex()} for seq,frame in result['records']]
        return result

    async def heartbeat(self):
        if self.configs:
            async with self.target.lock: await self.target.vendor(12)

    async def close(self):
        for sid in list(self.configs):
            try: await self.action(sid,'stop')
            except Exception: pass
        if self.task: self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        if self.writer:
            self.writer.close()
            try: await self.writer.wait_closed()
            except OSError: pass
        self.configs.clear()
