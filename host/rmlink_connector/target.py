"""Reusable Cortex-M target engine. One lock and owner for control + captures."""
import asyncio
import struct
import os
import sys
import time
from .diagnostics import decode_clock_header,summarize_clock
from contextlib import asynccontextmanager
from .target_pack import PACK_TAG, resolve_target, contains

DEFAULT_CLOCK_HZ=4_000_000

class TargetAccessError(ConnectionError):
    def __init__(self, message, code, completed=0):
        super().__init__(message)
        self.code=code; self.completed=completed


class TargetEngine:
    def __init__(self, broker, source):
        self.broker, self.source = broker, source
        self.lock=asyncio.Lock()
        self.connected=False
        self.clock=DEFAULT_CLOCK_HZ
        self.breakpoints={}
        self.capture_features=0
        self.packet_count=0
        self.profile=resolve_target()

    async def packet(self, request, priority='control'):
        self.packet_count+=1
        return (await self.broker.submit(self.source,[request],priority))[0]

    async def exact(self, request, expected):
        reply=await self.packet(request)
        if reply!=expected:
            if self.broker.owner not in (None,self.source):
                raise TargetAccessError('Target is busy in another debug session; end that session and retry. No Flash operation was started.','TargetBusy')
            raise ConnectionError(f'Target command {request[0]:02x} failed: {reply.hex()}')

    async def transfer(self, operations):
        request=bytearray([5,0,len(operations)]); reads=0
        for flags,value in operations:
            request.append(flags)
            if flags&2 and not flags&0x10:reads+=1
            else:request.extend(struct.pack('<I',value))
        if len(request)>self.broker.packet_size or 3+4*reads>self.broker.packet_size: raise ValueError('Transfer budget')
        reply=await self.packet(request)
        if len(reply)!=3+4*reads or reply[:3]!=bytes([5,len(operations),1]):
            ack=reply[2]&7 if len(reply)>=3 else 0
            code={2:'DapAckWait',4:'DapAckFault',7:'DapNoAck'}.get(ack,'DapPartialTransfer')
            raise TargetAccessError('DAP transfer incomplete: '+reply.hex(),code,reply[1] if len(reply)>1 else 0)
        return list(struct.unpack('<'+'I'*reads,reply[3:]))

    async def connect(self, clock_hz=DEFAULT_CLOCK_HZ, device=None):
        if type(clock_hz) is not int or not 1000<=clock_hz<=10000000: raise ValueError('clock_hz range')
        profile=resolve_target(device)
        async with self.lock:
            if self.broker.owner not in (None,self.source):
                raise TargetAccessError('Target is busy in another debug session; end that session and retry. No Flash operation was started.','TargetBusy')
            if self.connected:
                if clock_hz!=self.clock or profile['name']!=self.profile['name']:raise ValueError('Already connected to a different target/clock; open a new target session')
                return {'connected':True,'clock_hz':self.clock}
            try:
                await self.exact(b'\x11'+struct.pack('<I',clock_hz),b'\x11\x00')
                await self.exact(b'\x02\x01',b'\x02\x01')
                await self.exact(b'\x04\x02\x64\x00\xe8\x03',b'\x04\x00')
                await self.exact(b'\x13\x00',b'\x13\x00')
                for data,bits in ((b'\xff'*7,56),(b'\x9e\xe7',16),(b'\xff'*7,56),(b'\x00',8)):
                    await self.exact(bytes([0x12,bits])+data,b'\x12\x00')
                dp=(await self.transfer([(2,None)]))[0]
                await self.transfer([(0,0x1e),(8,0),(4,0x50000000)])
                for _ in range(100):
                    control=(await self.transfer([(6,None)]))[0]
                    if control&0xa0000000==0xa0000000: break
                    await asyncio.sleep(.01)
                else: raise ConnectionError('Debug power ACK timeout')
                cpuid=await self.read_word(0xe000ed00)
                preflight=profile['preflight']
                device_id=await self.read_word(preflight['idcodeAddress'])
                if (cpuid>>4)&0xfff!=profile['corePart'] or device_id&0xfff!=preflight['deviceId']:
                    raise ValueError('Target identity does not match '+profile['name'])
                capabilities=await self.vendor(0)
                self.capture_features=int.from_bytes(capabilities[17:21],'little') if len(capabilities)>=21 else 0
                if self.capture_features&2048:
                    if dp!=profile['dpIdcode']:raise ValueError('Target debug port identity mismatch')
                    raw=await self.read_word(preflight['flashSizeRegisterAddress']&~3)
                    capacity=(raw>>((preflight['flashSizeRegisterAddress']&3)*8))&0xffff
                    if capacity!=preflight['expectedFlashSizeKiB']:raise ValueError('Target Flash capacity mismatch')
                    selected=await self.vendor(21,struct.pack('<BI',profile['profileId'],PACK_TAG))
                    if selected!=struct.pack('<BI',profile['profileId'],PACK_TAG):raise ValueError('Probe target package mismatch')
                elif profile['profileId']!=1:
                    raise ValueError('Probe firmware upgrade required for shared target packages')
                self.profile=profile
                self.connected=True; self.clock=clock_hz
                return {'connected':True,'clock_hz':clock_hz,'dp_idcode':dp,'cpuid':cpuid,'device_id':device_id,'profile':profile['name'],'pack_tag':PACK_TAG}
            except BaseException:
                # Keep this source reusable. Broker.release would permanently close it.
                try:
                    if self.broker.owner==self.source: await self.packet(b'\x03')
                except Exception: pass
                raise

    async def vendor(self, sub, payload=b''):
        reply=await self.packet(bytes([0x81,sub])+payload)
        if len(reply)<3 or reply[:3]!=bytes([0x81,sub,0]):
            raise ConnectionError(f'Capture command {sub} failed: {reply.hex()}')
        return reply[3:]

    @asynccontextmanager
    async def control(self):
        if not self.connected: raise ValueError('Connect the target first')
        async with self.lock:
            started=time.perf_counter() if os.environ.get('RMLINK_STREAM_DIAGNOSTICS')=='1' else None
            await self.vendor(8)
            try: yield
            finally:
                await self.vendor(9)
                if started is not None:
                    print(f'RMLink capture barrier elapsed_ms={(time.perf_counter()-started)*1000:.3f}',file=sys.stderr,flush=True)

    async def read_word(self,address):
        return (await self.transfer([(8,0),(1,0x23000052),(5,address),(15,None)]))[0]

    async def write_word(self,address,value):
        await self.transfer([(8,0),(1,0x23000052),(5,address),(13,value)])

    async def write_range(self,address,data):
        if address%4 or not data or len(data)%4 or address+len(data)>2**32:raise ValueError('Word-aligned write required')
        done=0;limit=(self.broker.packet_size-18)//5
        while done<len(data):
            count=min((len(data)-done)//4,limit,(0x400-((address+done)&0x3ff))//4)
            values=struct.unpack('<'+'I'*count,data[done:done+count*4])
            try:await self.transfer([(8,0),(1,0x23000052),(5,address+done)]+[(13,v) for v in values])
            except TargetAccessError as error:
                error.completed_bytes=done+max(0,min(count,error.completed-3))*4
                raise
            done+=count*4
        return done

    async def register(self,index,value=None):
        if value is not None:await self.write_word(0xe000edf8,value)
        await self.write_word(0xe000edf4,index|(0x10000 if value is not None else 0))
        await self.poll_word(0xe000edf0,0x10000,0x10000)
        return await self.read_word(0xe000edf8)

    async def write_registers(self,registers):
        for index,value in registers:
            if type(index) is not int or index not in (*range(19),20) or type(value) is not int or not 0<=value<=0xffffffff:
                raise ValueError('Core register/value invalid')
        count=max(1,(self.broker.packet_size-18)//30)
        for offset in range(0,len(registers),count):
            operations=[(8,0),(1,0x23000042),(0x20,0x10000)]
            for index,value in registers[offset:offset+count]:
                operations.extend([(5,0xe000edf8),(13,value),(5,0xe000edf4),(13,index|0x10000),
                    (5,0xe000edf0),(0x1f,0x10000)])
            await self.transfer(operations) # value-match polls S_REGRDY on the probe

    async def read_registers(self,indices):
        if any(type(i) is not int or i not in (*range(19),20) for i in indices):raise ValueError('Core register invalid')
        count=max(1,(self.broker.packet_size-18)//26);values=[]
        for offset in range(0,len(indices),count):
            operations=[(8,0),(1,0x23000042),(0x20,0x10000)]
            for index in indices[offset:offset+count]:
                operations.extend([(5,0xe000edf4),(13,index),(5,0xe000edf0),(0x1f,0x10000),
                    (5,0xe000edf8),(15,None)])
            values.extend(await self.transfer(operations))
        return values

    async def poll_word(self,address,mask,expected,timeout=1):
        deadline=asyncio.get_running_loop().time()+timeout
        while True:
            value=await self.read_word(address)
            if value&mask==expected:return value
            if asyncio.get_running_loop().time()>=deadline:raise TimeoutError('Target status deadline')
            await asyncio.sleep(.001)

    def check_range(self,address,size):
        if type(address) is not int or type(size) is not int or address<0 or size<1 or size>4096 or address+size>2**32:
            raise ValueError('Memory range invalid')
        if (address%4 or size%4) and not contains(self.profile,address&~3,((address+size+3)&~3)-(address&~3)):
            raise ValueError('Subword reads require known RAM/Flash; peripheral widening is not automatic')

    async def read_batch(self,reads):
        self.check_reads(reads)
        words=[];ranges=[]
        for r in reads:
            address,size=r['address'],r['byte_length']
            ranges.append((address,size,len(words)*4+(address&3)))
            # Preserve repeated accesses, including peripheral side effects.
            words.extend(range(address&~3,(address+size+3)&~3,4))
        values=[];count=min((self.broker.packet_size-13)//6,(self.broker.packet_size-3)//4)
        for offset in range(0,len(words),count):
            operations=[(8,0),(1,0x23000052)]
            for a in words[offset:offset+count]:operations.extend([(5,a),(15,None)])
            values.extend(await self.transfer(operations))
        raw=struct.pack('<'+'I'*len(values),*values)
        return {'reads':[{'address':a,'data':raw[offset:offset+n].hex()} for a,n,offset in ranges]}

    def check_reads(self,reads):
        if not isinstance(reads,list) or not 1<=len(reads)<=32 or any(not isinstance(r,dict) for r in reads):
            raise ValueError('Expected 1..32 memory ranges')
        if any(type(r.get('byte_length')) is not int or r['byte_length']<1 for r in reads) or sum(r['byte_length'] for r in reads)>4096:
            raise ValueError('Batch byte budget')
        for r in reads:
            self.check_range(r.get('address'),r['byte_length'])

    def snapshot_range(self,address,size):
        return contains(self.profile,address,size)

    @staticmethod
    def breakpoint_value(address,ctrl):
        revision=ctrl>>28
        if revision==0:
            if address>=0x20000000:raise ValueError('FPB v1 cannot compare this address')
            return (address&0x1ffffffc)|1|((2 if address&2 else 1)<<30)
        if revision==1:return (address&0xfffffffe)|1
        raise ValueError('Unsupported FPB revision')

    async def read_snapshot_range(self,address,size):
        data=bytearray();limit=min(128,self.broker.packet_size-3)
        while len(data)<size:
            n=min(limit,size-len(data))
            reply=await self.vendor(20,struct.pack('<IH',address+len(data),n))
            if len(reply)!=n:raise TargetAccessError('Snapshot read length mismatch','MalformedResponse')
            data.extend(reply)
        return {'address':address,'data':data.hex()}

    async def read_range(self,address,size):
        self.check_range(address,size)
        data=bytearray();position=address&~3;end=(address+size+3)&~3
        # One contiguous range reads each word exactly once. Every packet
        # establishes SELECT/CSW/TAR and repairs TAR at each 1KiB wrap, including
        # a boundary inside the packet. No AP state is inherited from a peer.
        limit=min((self.broker.packet_size-3)//4,251)
        while position<end:
            count=min((end-position)//4,limit)
            operations=[(8,0),(1,0x23000052),(5,position)]
            for i in range(count):
                if i and ((position+i*4)&0x3ff)==0:operations.append((5,position+i*4))
                operations.append((15,None))
            try:
                values=await self.transfer(operations)
            except TargetAccessError as error:
                raw_done=len(data)+sum(bool(flags&2) for flags,_ in operations[:error.completed])*4
                error.completed_bytes=min(size,max(0,raw_done-(address&3)))
                raise
            data.extend(struct.pack('<'+'I'*len(values),*values))
            position+=count*4
        return {'address':address,'data':bytes(data[address&3:(address&3)+size]).hex()}

    async def reset_halted(self):
        """Catch SYSRESETREQ before any application instruction; no NRST needed."""
        demcr=await self.read_word(0xe000edfc)
        try:
            await self.write_word(0xe000edf0,0xa05f0003)
            await self.poll_word(0xe000edf0,0x20000,0x20000)
            await self.write_word(0xe000edfc,demcr|1)
            await self.write_word(0xe000ed0c,0x05fa0004)
            value=await self.poll_word(0xe000edf0,0x2020000,0x20000)
            return value
        finally:
            await self.write_word(0xe000edfc,demcr)

    async def run_to_address(self,body):
        requested=body.get('address');reset=body.get('reset',True)
        if type(requested) is not int or not self.profile['flashBase']<=requested<self.profile['flashBase']+self.profile['flashSize'] or type(reset) is not bool:
            raise ValueError('Startup requires a declared executable address and boolean reset')
        address=requested&~1
        await self.write_word(0xe000edf0,0xa05f0003)
        await self.poll_word(0xe000edf0,0x20000,0x20000)
        ctrl=await self.read_word(0xe0002000)
        count=((ctrl>>4)&15)|((ctrl>>8)&0x70)
        if not 1<=count<=32:raise ValueError('Unsupported FPB comparator count')
        saved=[await self.read_word(0xe0002008+4*i) for i in range(count)]
        comparator=self.breakpoint_value(address,ctrl)
        shared=next((i for i,v in enumerate(saved) if v==comparator),None)
        slot=shared if shared is not None else next((i for i,v in enumerate(saved) if not v&1 and i not in self.breakpoints),None)
        if slot is None:raise TargetAccessError('No free FPB comparator for startup','NoFreeBreakpoint')
        ignored=[i for i,v in enumerate(saved) if v&1 and i!=slot]
        try:
            for i in ignored:await self.write_word(0xe0002008+4*i,0)
            await self.write_word(0xe0002000,3)
            await self.write_word(0xe0002008+4*slot,comparator)
            dhcsr=await self.reset_halted() if reset else await self.read_word(0xe000edf0)
            reset_pc=await self.register(15);reset_lr=await self.register(14)
            if reset_pc&~1!=address:
                await self.write_word(0xe000edf0,0xa05f0001)
                await self.poll_word(0xe000edf0,0x20000,0x20000,timeout=3)
            final=await self.read_word(0xe000edf0)
            pc=await self.register(15);lr=await self.register(14)
            if not final&0x20000 or pc&~1!=address:
                raise TargetAccessError(f'Startup halted at 0x{pc:08x}, expected 0x{address:08x}','StartupEntryNotReached')
        except TimeoutError as error:
            raise TargetAccessError('Startup entry timeout; target will be halted and FPB restored','StartupTimeout') from error
        finally:
            # Halt before removing the temporary comparator, also on timeout.
            # Restore every original slot, including other owners' pre-existing
            # hardware values, without altering the session breakpoint map.
            try:
                await self.write_word(0xe000edf0,0xa05f0003)
                await self.poll_word(0xe000edf0,0x20000,0x20000)
                for i,value in enumerate(saved):await self.write_word(0xe0002008+4*i,value)
                await self.write_word(0xe0002000,ctrl|2)
            except Exception as error:
                raise TargetAccessError('Startup FPB restoration failed: '+str(error),'FpbCleanupFailed') from error
        return dict(state='Halted',requestedAddress=requested,entryAddress=address,resetRequested=reset,
                    resetPcValid=reset,resetDhcsr=dhcsr,resetPc=reset_pc,resetLr=reset_lr,
                    pc=pc,lr=lr,dhcsr=final,cleanupOk=True,sharedUserSlot=shared is not None,
                    temporaryBreakpointCount=0 if shared is not None else 1,temporarySlot=slot,ignoredUserSlots=ignored)

    async def execute(self, command, body):
        if command=='connect': return await self.connect(body.get('clock_hz',DEFAULT_CLOCK_HZ),body.get('device'))
        if command in ('read_memory','read_batch') and self.capture_features&128:
            reads=([{'address':body.get('address'),'byte_length':body.get('byte_length')}]
                   if command=='read_memory' else body.get('reads'))
            self.check_reads(reads)
            if all(self.snapshot_range(r['address'],r['byte_length']) for r in reads):
                if not self.connected:raise ValueError('Connect the target first')
                async with self.lock:
                    results=[await self.read_snapshot_range(r['address'],r['byte_length']) for r in reads]
                return results[0] if command=='read_memory' else {'reads':results}
        if command=='state' and self.capture_features&16:
            if not self.connected:raise ValueError('Connect the target first')
            async with self.lock:reply=await self.packet(b'\x81\x11','background')
            if len(reply)!=7 or reply[:3]!=b'\x81\x11\x00':
                raise TargetAccessError('Core state read failed: '+reply.hex(),'DapStateReadFailed')
            value=int.from_bytes(reply[3:7],'little')
            return {'dhcsr':value,'state':'Halted' if value&0x20000 else 'Running'}
        async with self.control():
            if command=='run_to_address':return await self.run_to_address(body)
            if command=='read_memory':
                address,size=body.get('address'),body.get('byte_length')
                return await self.read_range(address,size)
            if command=='read_batch':
                return await self.read_batch(body.get('reads'))
            if command=='write_memory':
                address=body.get('address'); data=bytes.fromhex(body.get('data',''))
                if type(address) is not int or address<0 or address%4 or not data or len(data)%4 or len(data)>4096 or address+len(data)>2**32:
                    raise ValueError('Aligned word write required')
                done=await self.write_range(address,data)
                return {'address':address,'bytes_written':done}
            if command=='state':
                value=await self.read_word(0xe000edf0)
                return {'dhcsr':value,'state':'Halted' if value&0x20000 else 'Running'}
            if command in ('halt','run','step'):
                if command=='step':
                    await self.write_word(0xe000edf0,0xa05f000b)
                await self.write_word(0xe000edf0,{'halt':0xa05f0003,'run':0xa05f0001,'step':0xa05f000d}[command])
                value=await self.poll_word(0xe000edf0,0x20000,0 if command=='run' else 0x20000)
                return {'command':command,'dhcsr':value,'state':'Halted' if value&0x20000 else 'Running'}
            if command in ('read_register','write_register'):
                index=body.get('index')
                if type(index) is not int or index not in (*range(19),20):raise ValueError('Core register index invalid')
                if not (await self.read_word(0xe000edf0))&0x20000:raise ValueError('Core must be halted')
                if command=='write_register':
                    value=body.get('value')
                    if type(value) is not int or not 0<=value<=0xffffffff:raise ValueError('Register value invalid')
                    await self.write_word(0xe000edf8,value)
                await self.write_word(0xe000edf4,index|(0x10000 if command=='write_register' else 0))
                await self.poll_word(0xe000edf0,0x10000,0x10000)
                return {'index':index,'value':await self.read_word(0xe000edf8)}
            if command=='set_breakpoint':
                address=body.get('address')
                if type(address) is not int or address&1 or not contains(self.profile,address,2):
                    raise ValueError('FPB requires an even address in a declared memory window')
                ctrl=await self.read_word(0xe0002000)
                comparator=self.breakpoint_value(address,ctrl)
                count=((ctrl>>4)&15)|((ctrl>>8)&0x70)
                for index in range(count):
                    if index not in self.breakpoints and not (await self.read_word(0xe0002008+index*4))&1:break
                else:raise ValueError('No free FPB comparator')
                await self.write_word(0xe0002000,3)
                await self.write_word(0xe0002008+index*4,comparator)
                self.breakpoints[index]=address
                return {'id':index,'address':address,'kind':'hardware'}
            if command=='clear_breakpoint':
                index=body.get('id')
                if type(index) is not int or index not in self.breakpoints:raise ValueError('Unknown breakpoint')
                await self.write_word(0xe0002008+index*4,0);self.breakpoints.pop(index)
                return {'cleared':index}
            if command=='reset':
                value=await self.reset_halted()
                return {'requested':True,'physical_nrst':False,'dhcsr':value,
                    'state':'Halted' if value&0x20000 else 'Running'}
            if command=='benchmark_local':
                a,stride,count,fields=[body.get(k) for k in ('address','stride','read_count','field_count')]
                if any(type(v) is not int for v in (a,stride,count,fields)): raise ValueError('Benchmark integers required')
                data=await self.vendor(10,struct.pack('<IIIB',a,stride,count,fields))
                reads,us,checksum=struct.unpack('<IQI',data)
                return {'reads':reads,'elapsed_us':us,'checksum':checksum,'clock_hz':self.clock,'actual_clock_measured':False}
            if command=='measure_swd_clock':
                if not self.capture_features&64:raise ValueError('Firmware does not support SWCLK trace')
                mode=body.get('mode','dp_read')
                if mode not in ('sequence','dp_read'):raise ValueError('Trace mode must be sequence or dp_read')
                reply=await self.packet(bytes([0x81,18,int(mode=='dp_read')]))
                if len(reply)==3 and reply[:2]==b'\x81\x12' and reply[2]:
                    return {'measurement_valid':False,'device_status':reply[2],'mode':mode}
                if reply[:3]!=b'\x81\x12\0':raise ValueError('Clock trace response')
                header=decode_clock_header(reply[3:]);words=[]
                while len(words)<header['symbol_count']:
                    offset=len(words);count=min(header['symbol_count']-offset,(self.broker.packet_size-9)//4)
                    data=await self.vendor(19,struct.pack('<IBB',header['trace_id'],offset,count))
                    if len(data)!=6+4*count or data[:6]!=struct.pack('<IBB',header['trace_id'],offset,count):
                        raise ValueError('Clock trace fragment identity or length')
                    words.extend(struct.unpack('<'+'I'*count,data[6:]))
                return summarize_clock(header,words)
            raise ValueError('Unsupported target command')

    async def cleanup(self):
        if not self.connected:return
        async with self.control():
            for index in list(self.breakpoints):
                await self.write_word(0xe0002008+index*4,0);self.breakpoints.pop(index,None)
