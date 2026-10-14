"""One-owner, staged main-Flash programming using a documented target RAM loader.

F103C8 full-page binary images only. No option-byte writes or uncertain replay.
The independently built MIT algorithm is pinned in host/algorithms.
"""
import asyncio
import hashlib
from pathlib import Path
import sys

BASE=0x20000000
BUFFER=0x20003c00
STACK=0x20005000
PAGE=1024


class FlashProgrammer:
    def __init__(self,target,algorithm=None):
        self.target=target;self.algorithm=algorithm
        self.image=None;self.received=None;self.address=0;self.digest=None
        self.busy=False;self.cancelled=False
        self.progress={'phase':'idle','programmed_bytes':0,'erased_pages':0,'verified':False}

    def stage(self,body):
        if self.busy:raise ValueError('Flash operation already running')
        action=body.get('action')
        if action=='begin':
            address=body.get('address',0x08000000);length=body.get('byte_length');digest=body.get('sha256')
            if type(address) is not int or type(length) is not int or address%PAGE or length%PAGE or not 1<=length<=65536 or not 0x08000000<=address or address+length>0x08010000:
                raise ValueError('F103C8 main-Flash page-aligned binary image required')
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('sha256 required')
            self.image=bytearray(length);self.received=bytearray(length);self.address=address;self.digest=digest;self.cancelled=False
            self.progress={'phase':'staging','programmed_bytes':0,'erased_pages':0,'verified':False}
            return {'staged_bytes':length,'address':address,'page_bytes':PAGE}
        if action=='chunk':
            offset=body.get('offset');data=bytes.fromhex(body.get('data',''))
            if self.image is None or type(offset) is not int or offset<0 or not 1<=len(data)<=4096 or offset+len(data)>len(self.image):raise ValueError('Upload chunk range')
            for i,b in enumerate(data):
                if self.received[offset+i] and self.image[offset+i]!=b:raise ValueError('Conflicting upload overlap')
            self.image[offset:offset+len(data)]=data;self.received[offset:offset+len(data)]=b'\x01'*len(data)
            return {'received_bytes':sum(self.received)}
        raise ValueError('Unsupported Flash staging action')

    def check_cancel(self):
        if self.cancelled:raise RuntimeError('Flash cancelled; the current target write may have completed')

    async def invoke(self,entry,args,timeout=3):
        self.check_cancel();target=self.target
        await target.write_word(0xe000edf0,0xa05f0003)
        await target.poll_word(0xe000edf0,0x20000,0x20000)
        for i,value in enumerate(args):await target.register(i,value)
        for i,value in ((9,BASE),(13,STACK),(14,BASE+0x501),(15,BASE+entry+1),(16,0x01000000)):
            await target.register(i,value)
        await target.write_word(0xe000ed30,0x1f) # Clear old stop reasons, not target Flash.
        await target.write_word(0xe000edf0,0xa05f0001)
        deadline=asyncio.get_running_loop().time()+timeout
        while True:
            self.check_cancel()
            state=await target.read_word(0xe000edf0)
            if state&0x20000:break
            if asyncio.get_running_loop().time()>deadline:raise TimeoutError('RAM Flash algorithm deadline')
            await asyncio.sleep(.001)
        pc=await target.register(15);result=await target.register(0)
        if pc&~1 not in (BASE+0x500,BASE+0x502):raise RuntimeError(f'Unexpected algorithm stop PC {pc:#x}')
        if result:raise RuntimeError(f'Flash algorithm returned {result} at entry {entry:#x}')

    def code(self):
        if self.algorithm is not None:return self.algorithm
        if getattr(sys,'frozen',False):path=Path(sys._MEIPASS)/'rmlink_algorithms/stm32f103c8.bin'
        else:path=Path(__file__).resolve().parents[2]/'artifacts/build/flash-algorithms/stm32f103c8.bin'
        data=path.read_bytes()
        if not 0x510<=len(data)<=0x3c00 or len(data)%4:raise ValueError('Built-in Flash algorithm image invalid')
        return data

    async def commit(self):
        if self.busy or self.image is None or not all(self.received):raise ValueError('Complete staged image required')
        if self.progress['phase'] in ('complete','failed','cancelled'):raise ValueError('Begin a new upload; Flash commit is never replayed')
        if hashlib.sha256(self.image).hexdigest()!=self.digest:raise ValueError('Staged image hash mismatch')
        code=self.code();target=self.target
        self.busy=True;self.cancelled=False;self.progress['phase']='preflight'
        modified=False
        try:
            if not target.connected:await target.connect()
            async with target.control():
                if await target.read_word(0xe0042000)&0xfff!=0x410 or await target.read_word(0x1ffff7e0)&0xffff!=64:
                    raise ValueError('Flash preflight requires STM32F103C8 64 KiB')
                options=[await target.read_word(0x1ffff800+i*4) for i in range(4)]
                await target.write_word(0xe000edf0,0xa05f0003);await target.poll_word(0xe000edf0,0x20000,0x20000)
                for slot in list(target.breakpoints):await target.write_word(0xe0002008+4*slot,0)
                target.breakpoints.clear()
                await target.write_range(BASE,code)
                await self.invoke(0,[self.address,8000000,2])
                for offset in range(0,len(self.image),PAGE):
                    self.check_cancel();self.progress['phase']='erase';modified=True
                    await self.invoke(0x200,[self.address+offset]);self.progress['erased_pages']+=1
                    self.check_cancel();self.progress['phase']='program'
                    data=bytes(self.image[offset:offset+PAGE]);await target.write_range(BUFFER,data)
                    await self.invoke(0x300,[self.address+offset,len(data),BUFFER])
                    self.progress['programmed_bytes']+=len(data)
                    self.progress['phase']='verify';await self.invoke(0x400,[self.address+offset,len(data),BUFFER])
                await self.invoke(0x100,[2])
                # Independent host-side byte verification as well as the RAM algorithm.
                digest=hashlib.sha256()
                for offset in range(0,len(self.image),4096):
                    self.check_cancel();n=min(4096,len(self.image)-offset)
                    result=await target.read_range(self.address+offset,n);digest.update(bytes.fromhex(result['data']))
                if digest.hexdigest()!=self.digest:raise RuntimeError('Independent Flash verification failed')
                if [await target.read_word(0x1ffff800+i*4) for i in range(4)]!=options:raise RuntimeError('Option bytes changed unexpectedly')
                self.progress.update(verified=True,sha256=self.digest,option_bytes_unchanged=True,phase='reset')
                await target.write_word(0xe000ed0c,0x05fa0004)
                await asyncio.sleep(.02)
                await target.write_word(0xe000edf0,0xa05f0001)
                self.progress['phase']='complete'
                return dict(self.progress,address=self.address,byte_length=len(self.image),original_restored=False)
        except BaseException as error:
            self.progress.update(phase='failed',error=str(error),write_completion_may_be_unknown=modified)
            # End in a stopped state rather than resume a partial program. No replay.
            try:await target.write_word(0xe000edf0,0xa05f0003)
            except Exception:pass
            raise
        finally:self.busy=False
