"""Independent Flash invocation oracle: staged hash, exact addresses, failure/no replay."""
from contextlib import asynccontextmanager
import hashlib
import struct
import unittest
from rmlink_connector.flash import FlashProgrammer,BASE,BUFFER,PAGE

class AlgorithmTarget:
    connected=True
    def __init__(self):
        self.ram=bytearray(20*1024);self.flash=bytearray(b'\xff'*65536);self.breakpoints={};self.regs={};self.stops=0;self.entries=[];self.bad_return=False
    @asynccontextmanager
    async def control(self):yield
    async def read_word(self,address):
        return {0xe0042000:0x410,0x1ffff7e0:64,0xe000edf0:0x30000}.get(address,0xffff)
    async def poll_word(self,*args):return 0x30000
    async def write_range(self,address,data):
        assert BASE<=address and address+len(data)<=BASE+len(self.ram)
        self.ram[address-BASE:address-BASE+len(data)]=data
    async def register(self,index,value=None):
        if value is not None:self.regs[index]=value
        return self.regs.get(index,0)
    async def write_registers(self,registers):
        for index,value in registers:await self.register(index,value)
    async def read_registers(self,indices):return [await self.register(i) for i in indices]
    async def write_word(self,address,value):
        if address==0xe000edf0 and value==0xa05f0003:self.stops+=1
        if address==0xe000edf0 and value==0xa05f0001 and 15 in self.regs:
            entry=(self.regs[15]&~1)-BASE;self.entries.append(entry)
            a=self.regs.get(0,0)-0x08000000
            if entry==0x200:self.flash[a:a+PAGE]=b'\xff'*PAGE
            if entry==0x300:
                n,buf=self.regs[1],self.regs[2]-BASE;self.flash[a:a+n]=self.ram[buf:buf+n]
            if entry==0x400:
                n,buf=self.regs[1],self.regs[2]-BASE;assert self.flash[a:a+n]==self.ram[buf:buf+n]
            self.regs[0]=3 if self.bad_return else 0;self.regs[15]=BASE+0x500
    async def read_range(self,address,size):return {'data':self.flash[address-0x08000000:address-0x08000000+size].hex()}

def upload(flash,data):
    flash.stage({'action':'begin','address':0x08000000,'byte_length':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    flash.stage({'action':'chunk','offset':0,'data':data.hex()})

class FlashTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_image_and_no_implicit_replay(self):
        target=AlgorithmTarget();flash=FlashProgrammer(target,b'\0'*0x600);data=bytes(range(256))*8
        upload(flash,data);result=await flash.commit()
        self.assertTrue(result['verified']);self.assertFalse(result['original_restored'])
        self.assertEqual(target.flash[:len(data)],data)
        self.assertEqual(target.entries[:7],[0,0x200,0x300,0x400,0x200,0x300,0x400])
        before=len(target.entries)
        with self.assertRaises(ValueError):await flash.commit()
        self.assertEqual(len(target.entries),before)
    async def test_bad_hash_and_upload_overlap_never_execute_target(self):
        target=AlgorithmTarget();flash=FlashProgrammer(target,b'\0'*0x600);data=b'x'*1024;upload(flash,data)
        with self.assertRaises(ValueError):flash.stage({'action':'chunk','offset':0,'data':'ff'})
        flash.image[2]^=1
        with self.assertRaises(ValueError):await flash.commit()
        self.assertEqual(target.entries,[])
    async def test_algorithm_failure_halts_and_does_not_replay(self):
        target=AlgorithmTarget();target.bad_return=True;flash=FlashProgrammer(target,b'\0'*0x600);upload(flash,b'x'*1024)
        with self.assertRaises(RuntimeError):await flash.commit()
        self.assertEqual(target.entries,[0]);self.assertFalse(flash.busy);self.assertGreater(target.stops,0)
        with self.assertRaises(ValueError):await flash.commit()

if __name__=='__main__':unittest.main()
