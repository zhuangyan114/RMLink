"""One-owner, staged main-Flash programming using a documented target RAM loader.

Shared-package complete erase-sector images. No option-byte writes or uncertain replay.
Independently built MIT algorithms are pinned in host/algorithms.
"""
import asyncio
import hashlib
import secrets
import struct
import time
import zlib
from .target_pack import PACK,PACK_TAG,resolve_target,flash_sectors,algorithm_bytes

BASE=0x20000000
BUFFER=0x20003c00
STACK=0x20005000
PAGE=1024


class FlashJobError(RuntimeError):
    """Transport-independent API error retaining the final Flash evidence."""
    def __init__(self,error,progress):
        super().__init__(str(error));self.progress=dict(progress)
        self.status=409 if progress['phase']=='cancelled' else 400 if isinstance(error,ValueError) else 503 if isinstance(error,(OSError,ConnectionError,TimeoutError)) else 500


class ProbeImageMemoryUnavailable(RuntimeError):
    """An explicit BEGIN rejection before upload or any target writes."""


class FlashProgrammer:
    def __init__(self,target,algorithm=None):
        self.target=target;self.algorithm=algorithm
        self.image=None;self.received=None;self.address=0;self.digest=None
        self.busy=False;self.cancelled=False;self.backend='auto';self.verify_on_host=False
        self.progress={'phase':'idle','programmed_bytes':0,'erased_pages':0,'verified':False}

    @property
    def profile(self):return getattr(self.target,'profile',None) or resolve_target()
    @property
    def metadata(self):return PACK['algorithms'][self.profile['algorithm']]
    def option_registers(self):return self.profile['optionRegisters']

    def stage(self,body):
        if self.busy:raise ValueError('Flash operation already running')
        action=body.get('action')
        if action=='begin':
            backend=body.get('backend','auto')
            if backend not in ('auto','host','probe'):raise ValueError('Flash backend must be auto, host or probe')
            verify_on_host=body.get('verify_on_host',False)
            if type(verify_on_host) is not bool:raise ValueError('verify_on_host must be boolean')
            reset_after=body.get('reset_after',True)
            if type(reset_after) is not bool:raise ValueError('reset_after must be boolean')
            address=body.get('address',0x08000000);length=body.get('byte_length');digest=body.get('sha256')
            flash_sectors(self.profile,address,length)
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('sha256 required')
            self.image=bytearray(length);self.received=bytearray(length);self.address=address;self.digest=digest;self.cancelled=False;self.backend=backend;self.verify_on_host=verify_on_host
            self.staged_profile=self.profile['name']
            self.reset_after=reset_after
            self.progress={'phase':'staging','programmed_bytes':0,'erased_pages':0,'verified':False,
                           'address':address,'byte_length':length,'image_sha256':digest,'target':self.staged_profile,'pack_tag':PACK_TAG,
                           'host_verified':False,'probe_verified':False,'verification':'pending'}
            return {'staged_bytes':length,'address':address,'page_bytes':self.metadata['pageSize'],'target':self.staged_profile}
        if action=='chunk':
            if self.progress['phase']!='staging':raise ValueError('Begin a new upload before staging data')
            offset=body.get('offset');data=bytes.fromhex(body.get('data',''))
            if self.image is None or type(offset) is not int or offset<0 or not 1<=len(data)<=4096 or offset+len(data)>len(self.image):raise ValueError('Upload chunk range')
            for i,b in enumerate(data):
                if self.received[offset+i] and self.image[offset+i]!=b:raise ValueError('Conflicting upload overlap')
            self.image[offset:offset+len(data)]=data;self.received[offset:offset+len(data)]=b'\x01'*len(data)
            return {'received_bytes':sum(self.received)}
        raise ValueError('Unsupported Flash staging action')

    def check_cancel(self):
        if self.cancelled:raise RuntimeError('Flash cancelled; the current target write may have completed')

    def cancel(self):
        phase=self.progress['phase']
        if phase in ('complete','failed','cancelled') or self.image is None and not self.busy:
            return {'cancel_requested':False,'already_terminal':True,'phase':phase}
        self.cancelled=True
        if not self.busy:self.progress['phase']='cancelled'
        return {'cancel_requested':True,'phase':self.progress['phase']}

    async def invoke(self,entry,args,timeout=None):
        self.check_cancel();target=self.target
        loader=self.profile['loader'];base=loader['address'];bkpt=base+self.metadata['entries']['bkpt']
        timeout=timeout or max(self.profile['eraseTimeoutMs'],self.profile['programTimeoutMs'])/1000
        await target.write_word(0xe000edf0,0xa05f0003)
        await target.poll_word(0xe000edf0,0x20000,0x20000)
        await target.write_registers(list(enumerate(args))+[(9,self.metadata['staticBase'] or base),(13,loader['stack']),(14,bkpt+1),(15,base+entry+1),(16,0x01000000)])
        await target.write_word(0xe000ed30,0x1f) # Clear old stop reasons, not target Flash.
        await target.write_word(0xe000edf0,0xa05f0001)
        deadline=asyncio.get_running_loop().time()+timeout
        while True:
            self.check_cancel()
            state=await target.read_word(0xe000edf0)
            if state&0x20000:break
            if asyncio.get_running_loop().time()>deadline:raise TimeoutError('RAM Flash algorithm deadline')
            await asyncio.sleep(.001)
        pc,result=await target.read_registers([15,0])
        if pc&~1 not in (bkpt,bkpt+2):raise RuntimeError(f'Unexpected algorithm stop PC {pc:#x}')
        if result:raise RuntimeError(f'Flash algorithm returned {result} at entry {entry:#x}')

    def code(self):
        if self.algorithm is not None:return self.algorithm
        return algorithm_bytes(self.profile)

    async def probe_command(self,sub,payload=b''):
        reply=await self.target.packet(bytes([0x82,sub])+payload)
        if len(reply)<3 or reply[:3]!=bytes([0x82,sub,0]):
            raise ConnectionError(f'Probe Flash command {sub} failed: {reply.hex()}')
        return reply[3:]

    async def probe_status(self,operation):
        data=await self.probe_command(5,struct.pack('<I',operation))
        if len(data)!=43:raise ValueError('Probe Flash status length')
        state,error,flags,op,address,size,received,erased,programmed,verified,us,fault=struct.unpack('<BBBIIIIIIIQI',data)
        if op!=operation or address!=self.address or size!=len(self.image):raise ValueError('Probe Flash operation identity changed')
        names={1:'upload_probe',2:'probe_ready',3:'preflight',4:'load_algorithm',5:'erase',6:'program',
               7:'verify_page',8:'verify_probe',9:'reset',10:'probe_complete',11:'failed',12:'cancelled'}
        result=dict(phase=names.get(state,'unknown'),probe_state=state,probe_error=error,
                    probe_received_bytes=received,erased_pages=erased,programmed_bytes=programmed,
                    probe_verified_bytes=verified,probe_elapsed_us=us,probe_error_address=fault,
                    probe_verified=bool(flags&2),halt_confirmed=bool(flags&4),
                    write_completion_may_be_unknown=bool(flags&1) and state!=10)
        self.progress.update(result);return result

    async def commit_probe(self):
        target=self.target;operation=secrets.randbelow(0xffffffff)+1
        self.progress.update(backend='probe',operation_id=operation,verified=False)
        created=commit_sent=False;terminal=False;clock=time.perf_counter();timings={}
        try:
            caps=await self.probe_command(0)
            if len(caps)!=11 or caps[0] not in (1,2) or caps[1]!=self.profile['profileId'] or int.from_bytes(caps[2:6],'little')!=self.profile['flashSize'] or (int.from_bytes(caps[6:8],'little') or 65536)!=self.metadata['pageSize']:
                raise ValueError('Unsupported buffered Flash capabilities')
            chunk=int.from_bytes(caps[8:10],'little')
            if not 1<=chunk<=target.broker.packet_size-16:raise ValueError('Probe upload chunk capacity')
            if not self.reset_after and not caps[10]&16:raise ValueError('Probe firmware upgrade required for sparse Flash jobs')
            self.progress['erase_strategy']='mass' if caps[10]&8 and self.address==self.profile['flashBase'] and len(self.image)==self.profile['flashSize'] else 'page' if self.profile['profileId']==1 else 'sector'
            options=[await target.read_word(a) for a in self.option_registers()]
            begin=await target.packet(b'\x82\x01'+struct.pack('<III',operation,self.address,len(self.image))+bytes.fromhex(self.digest)+(bytes([0xa5,int(self.reset_after)]) if caps[10]&16 else b''))
            if begin==b'\x82\x01\x04':raise ProbeImageMemoryUnavailable('Probe image cache memory unavailable')
            if begin!=b'\x82\x01\0':raise ConnectionError('Probe Flash BEGIN failed: '+begin.hex())
            created=True
            self.progress['phase']='upload_probe';start=time.perf_counter()
            packets=[];ends=[];upload_packets=upload_batches=0
            for at in range(0,len(self.image),chunk):
                self.check_cancel();data=bytes(self.image[at:at+chunk])
                packets.append(b'\x82\x02'+struct.pack('<IIHI',operation,at,len(data),zlib.crc32(data))+data);ends.append(at+len(data))
                if len(packets)==8 or at+len(data)==len(self.image):
                    replies=await target.broker.submit(target.source,packets,'control')
                    target.packet_count+=len(packets);upload_packets+=len(packets);upload_batches+=1
                    if len(replies)!=len(packets) or any(reply!=b'\x82\x02\0'+struct.pack('<I',end) for reply,end in zip(replies,ends)):
                        raise ConnectionError('Probe Flash upload incomplete; no implicit replay')
                    self.progress['probe_received_bytes']=ends[-1];packets=[];ends=[]
            await self.probe_command(3,struct.pack('<I',operation))
            timings['probe_upload_ms']=(time.perf_counter()-start)*1000
            self.progress.update(phase='probe_ready',upload_packets=upload_packets,upload_batches=upload_batches)
            self.check_cancel();commit_sent=True;start=time.perf_counter()
            # Set before sending: a lost response does not mean commit did not run.
            self.progress['write_completion_may_be_unknown']=True
            await self.probe_command(4,struct.pack('<I',operation))
            target.breakpoints.clear();deadline=asyncio.get_running_loop().time()+(150 if self.profile['profileId']==1 else 630);cancel_sent=False
            while True:
                status=await self.probe_status(operation)
                if status['probe_state'] in (10,11,12):terminal=True;break
                if self.cancelled and not cancel_sent:
                    reply=await target.packet(b'\x82\x06'+struct.pack('<I',operation))
                    if reply==b'\x82\x06\x03':
                        # Completion can win between STATUS and CANCEL. Read
                        # its terminal truth; never restart or replay COMMIT.
                        status=await self.probe_status(operation)
                        if status['probe_state'] in (10,11,12):terminal=True;break
                        raise ConnectionError('Probe rejected cancellation in a nonterminal state')
                    if reply!=b'\x82\x06\0':raise ConnectionError('Probe Flash cancellation failed: '+reply.hex())
                    cancel_sent=True
                if asyncio.get_running_loop().time()>=deadline:raise TimeoutError('Buffered Flash operation deadline')
                await asyncio.sleep(.02)
            timings['probe_commit_ms']=(time.perf_counter()-start)*1000
            self.progress['probe_stage_us']=dict(zip(('upload','preflight','loader','erase','program','page_verify','full_verify'),
                struct.unpack('<QQQQQQQ',await self.probe_command(9,struct.pack('<I',operation)))))
            if status['probe_state']!=10:raise RuntimeError(f'Probe Flash ended in {status["phase"]}, error={status["probe_error"]}')
            if not status['probe_verified'] or status['programmed_bytes']!=len(self.image) or status['probe_verified_bytes']!=len(self.image):
                raise RuntimeError('Incomplete probe verification')
            timings['host_verify_ms']=0
            self.progress['verification']='probe_full_compare'
            if self.verify_on_host:
                self.progress['phase']='verify_host';start=time.perf_counter();digest=hashlib.sha256()
                for at in range(0,len(self.image),4096):
                    self.check_cancel();part=await target.read_range(self.address+at,min(4096,len(self.image)-at));digest.update(bytes.fromhex(part['data']))
                if digest.hexdigest()!=self.digest:raise RuntimeError('Independent Flash verification failed')
                timings['host_verify_ms']=(time.perf_counter()-start)*1000
                self.progress.update(host_verified=True,verification='probe_and_host')
            start=time.perf_counter()
            if [await target.read_word(a) for a in self.option_registers()]!=options:raise RuntimeError('Option bytes changed unexpectedly')
            timings['host_finalize_ms']=(time.perf_counter()-start)*1000;timings['enhanced_total_ms']=(time.perf_counter()-clock)*1000
            self.progress.update(phase='complete',verified=True,sha256=self.digest,option_bytes_unchanged=True,
                                 write_completion_may_be_unknown=False,timings=timings)
            return dict(self.progress,address=self.address,byte_length=len(self.image),original_restored=False)
        except BaseException:
            self.progress['write_completion_may_be_unknown']=commit_sent and not terminal or self.progress.get('write_completion_may_be_unknown',False)
            if created and not terminal:
                try:
                    await self.probe_command(6,struct.pack('<I',operation))
                    deadline=asyncio.get_running_loop().time()+2
                    while asyncio.get_running_loop().time()<deadline:
                        status=await self.probe_status(operation)
                        if status['probe_state'] in (10,11,12):break
                        await asyncio.sleep(.02)
                except Exception as error:self.progress['probe_cleanup_error']=str(error)
            raise

    async def commit(self,before_commit=None):
        if self.busy or self.image is None or not all(self.received):raise ValueError('Complete staged image required')
        if self.progress['phase'] in ('complete','failed','cancelled'):raise ValueError('Begin a new upload; Flash commit is never replayed')
        if hashlib.sha256(self.image).hexdigest()!=self.digest:raise ValueError('Staged image hash mismatch')
        if self.profile['name']!=self.staged_profile:raise ValueError('Target changed after image staging')
        target=self.target
        self.busy=True;self.cancelled=False;self.progress['phase']='preflight'
        modified=False
        try:
            if before_commit is not None:await before_commit()
            self.check_cancel()
            if not target.connected:await target.connect()
            async with target.control():
                supported=bool(getattr(target,'capture_features',0)&32)
                if self.backend=='probe' and not supported:raise ValueError('Probe firmware does not support buffered Flash')
                if supported and self.backend!='host':
                    try:return await self.commit_probe()
                    except ProbeImageMemoryUnavailable:
                        if self.backend!='auto':raise
                        self.progress.pop('operation_id',None)
                        self.progress['probe_fallback']='image_cache_memory_unavailable'
                code=self.code()
                self.progress['backend']='host'
                self.progress['erase_strategy']='sector'
                p=self.profile['preflight'];loader=self.profile['loader'];entries=self.metadata['entries'];page=self.metadata['pageSize']
                capacity=await target.read_word(p['flashSizeRegisterAddress']&~3)
                capacity=(capacity>>((p['flashSizeRegisterAddress']&3)*8))&0xffff
                if await target.read_word(p['idcodeAddress'])&0xfff!=p['deviceId'] or capacity!=p['expectedFlashSizeKiB']:
                    raise ValueError('Flash identity/capacity mismatch for '+self.profile['name'])
                options=[await target.read_word(a) for a in self.option_registers()]
                await target.write_word(0xe000edf0,0xa05f0003);await target.poll_word(0xe000edf0,0x20000,0x20000)
                for slot in list(target.breakpoints):await target.write_word(0xe0002008+4*slot,0)
                target.breakpoints.clear()
                # TargetEngine writes are word aligned and bounded; split the
                # loader/buffer transfers without creating another SWD owner.
                for at in range(0,len(code),4096):await target.write_range(loader['address']+at,code[at:at+4096])
                await self.invoke(entries['init'],[self.address,8000000,2])
                for sector in flash_sectors(self.profile,self.address,len(self.image)):
                    self.check_cancel();self.progress['phase']='erase';modified=True
                    await self.invoke(entries['eraseSector'],[sector['address']]);self.progress['erased_pages']+=1
                    offset=sector['address']-self.address
                    for at in range(offset,offset+sector['size'],page):
                        self.check_cancel();self.progress['phase']='program'
                        data=bytes(self.image[at:at+page]);buffer=loader['buffer']
                        for part in range(0,len(data),4096):await target.write_range(buffer+part,data[part:part+4096])
                        await self.invoke(entries['programPage'],[self.address+at,len(data),buffer])
                        self.progress['programmed_bytes']+=len(data)
                        if 'verify' in entries:
                            self.progress['phase']='verify';await self.invoke(entries['verify'],[self.address+at,len(data),buffer])
                await self.invoke(entries['uninit'],[2])
                # Independent host-side byte verification as well as the RAM algorithm.
                digest=hashlib.sha256()
                for offset in range(0,len(self.image),4096):
                    self.check_cancel();n=min(4096,len(self.image)-offset)
                    result=await target.read_range(self.address+offset,n);digest.update(bytes.fromhex(result['data']))
                if digest.hexdigest()!=self.digest:raise RuntimeError('Independent Flash verification failed')
                if [await target.read_word(a) for a in self.option_registers()]!=options:raise RuntimeError('Option bytes changed unexpectedly')
                self.progress.update(verified=True,sha256=self.digest,option_bytes_unchanged=True,phase='reset',
                                     host_verified=True,verification='host_sha256')
                if self.reset_after:
                    await target.write_word(0xe000ed0c,0x05fa0004)
                    await asyncio.sleep(.02)
                    await target.write_word(0xe000edf0,0xa05f0001)
                self.progress['phase']='complete'
                return dict(self.progress,address=self.address,byte_length=len(self.image),original_restored=False)
        except BaseException as error:
            self.progress.update(phase='cancelled' if self.cancelled else 'failed',error=str(error),
                                 write_completion_may_be_unknown=modified or self.progress.get('write_completion_may_be_unknown',False))
            # End in a stopped state rather than resume a partial program. No replay.
            try:await target.write_word(0xe000edf0,0xa05f0003)
            except Exception:pass
            raise
        finally:self.busy=False
