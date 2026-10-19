"""Independent identity/layout and shared package regression cases (no board)."""
import copy
import hashlib
import struct
import unittest
from rmlink_connector.broker import Broker
from rmlink_connector.target import TargetEngine
from rmlink_connector.target_pack import PACK,PACK_TAG,resolve_target,contains,flash_sectors,algorithm_bytes,validate_pack
from rmlink_connector.subscriptions import compile_config
from rmlink_connector.flash import FlashProgrammer
from test_target_engine import TargetModel
from test_flash_service import AlgorithmTarget

# Literal expectations are independent of the production catalogue.
DEVICES=[('STM32F103C8',0xc23,0x410,64,0xe0042000,0x1ffff7e0,0x1ba01477),
         ('STM32F407VE',0xc24,0x413,512,0xe0042000,0x1fff7a22,0x2ba01477),
         ('STM32F407IGT6',0xc24,0x413,1024,0xe0042000,0x1fff7a22,0x2ba01477),
         ('STM32H723VG',0xc27,0x483,1024,0x5c001000,0x1ff1e880,0x6ba02477)]

class ProfileModel(TargetModel):
    def __init__(self,row):
        super().__init__(512);self.features=2048|128;self.dp=row[6];self.selection=None
        self.memory[0xe000ed00]=0x41000000|(row[1]<<4)|1
        self.memory[row[4]]=row[2]
        self.memory[row[5]&~3]=row[3]<<((row[5]&3)*8)
    async def exchange_many(self,packets):
        results=[]
        for packet in packets:
            if packet[:4]==b'\x05\0\x01\x02':
                self.requests.append(packet);results.append(b'\x05\x01\x01'+struct.pack('<I',self.dp))
            elif packet[:2]==b'\x81\x15':
                self.requests.append(packet);self.selection=packet[2:]
                results.append(b'\x81\x15\0'+packet[2:])
            else:results.extend(await super().exchange_many([packet]))
        return results

class LoaderModel(AlgorithmTarget):
    def __init__(self,row):
        super().__init__();self.profile=resolve_target(row[0]);self.row=row
        self.base=0x24000000 if row[0]=='STM32H723VG' else 0x20000000
        self.stack=self.base+(320 if row[0]=='STM32H723VG' else 128)*1024
        self.buffer=self.base+0x10000;self.ram=bytearray(self.stack-self.base)
        self.flash=bytearray(b'\xa5'*(row[3]*1024));self.erases=[];self.programs=[]
    async def read_word(self,address):
        if address==self.row[4]:return self.row[2]
        if address==self.row[5]&~3:return self.row[3]<<((self.row[5]&3)*8)
        if address==0xe000edf0:return 0x30000
        return 0x12345678
    async def write_range(self,address,data):
        assert self.base<=address and address+len(data)<=self.stack and len(data)<=4096
        self.ram[address-self.base:address-self.base+len(data)]=data
    async def write_word(self,address,value):
        if address==0xe000edf0 and value==0xa05f0003:self.stops+=1
        if address!=0xe000edf0 or value!=0xa05f0001 or 15 not in self.regs:return
        entry=(self.regs[15]&~1)-self.base;self.entries.append(entry)
        assert self.regs[13]==self.stack and self.regs[14]==self.base+0x501
        offset=self.regs.get(0,0)-0x08000000
        if entry==0x200:
            assert offset==(self.row[3]*1024-128*1024)
            self.erases.append(self.regs[0]);self.flash[offset:offset+128*1024]=b'\xff'*(128*1024)
        if entry in (0x300,0x400):
            size=self.regs[1];assert size==(65536 if self.base==0x24000000 else 16384) and self.regs[2]==self.buffer
            data=self.ram[self.buffer-self.base:self.buffer-self.base+size]
            if entry==0x300:self.flash[offset:offset+size]=data;self.programs.append(self.regs[0])
            else:assert self.flash[offset:offset+size]==data
        self.regs[0]=0;self.regs[15]=self.base+0x500

class TargetPackageTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_orbit_target_connects_through_one_owner_and_selects_exact_profile(self):
        for row in DEVICES:
            model=ProfileModel(row);broker=Broker(model,'model');await broker.start()
            engine=TargetEngine(broker,'one-owner')
            try:
                result=await engine.connect(device=row[0])
                self.assertTrue(result['connected']);self.assertEqual(broker.owner,'one-owner')
                self.assertEqual(model.selection,struct.pack('<BI',resolve_target(row[0])['profileId'],PACK_TAG))
                self.assertEqual(engine.profile['flashSize'],row[3]*1024)
            finally:await broker.close()

    async def test_wrong_density_and_old_firmware_refuse_before_profile_selection(self):
        for old in (False,True):
            model=ProfileModel(DEVICES[1]);model.features=0 if old else 2048
            broker=Broker(model,'model');await broker.start();engine=TargetEngine(broker,'one-owner')
            try:
                with self.assertRaises(ValueError):await engine.connect(device='STM32F407IG')
                self.assertIsNone(model.selection);self.assertFalse(engine.connected)
            finally:await broker.close()

    async def test_m7_breakpoint_revision_uses_full_address_comparator(self):
        model=ProfileModel(DEVICES[3]);model.memory[0xe0002000]=0x10000060
        for i in range(6):model.memory[0xe0002008+4*i]=0
        broker=Broker(model,'model');await broker.start();engine=TargetEngine(broker,'one-owner')
        try:
            await engine.connect(device='STM32H723VG')
            result=await engine.execute('set_breakpoint',{'address':0x080e0002})
            self.assertEqual(result['id'],0);self.assertEqual(model.memory[0xe0002008],0x080e0003)
            await engine.execute('clear_breakpoint',{'id':0})
            self.assertEqual(model.memory[0xe0002008],0)
        finally:await broker.close()

    def test_pack_boundaries_algorithms_and_discontiguous_ram(self):
        for row in DEVICES:
            target=resolve_target(row[0]);code=algorithm_bytes(target)
            self.assertEqual(code[1280:1282],b'\x00\xbe')
            self.assertTrue(contains(target,target['flashBase']+target['flashSize']-1,1))
            self.assertFalse(contains(target,target['flashBase']+target['flashSize'],1))
        h7=resolve_target('STM32H723VG')
        for address in (0x20000000,0x2404fffc,0x30043ffc,0x38003ffc,0x38800ffc):
            self.assertEqual(compile_config({'kind':'memory_series','addresses':[address]},h7)['addresses'],[address])
        with self.assertRaises(ValueError):compile_config({'kind':'memory_series','addresses':[0x24050000]},h7)
        with self.assertRaises(ValueError):flash_sectors(resolve_target('STM32F407IG'),0x08020000,1024)
        self.assertEqual(flash_sectors(resolve_target('STM32F407IG'),0x080e0000,128*1024)[0]['number'],11)
        malformed=copy.deepcopy(PACK);malformed['targets'][1]['loader']['buffer']=0x2001fff0
        with self.assertRaises(ValueError):validate_pack(malformed)

    def test_unregistered_name_and_wrong_image_range_reject_without_target_io(self):
        with self.assertRaises(ValueError):resolve_target('STM32F407VG')
        target=AlgorithmTarget();target.profile=resolve_target('STM32F407VE')
        job=FlashProgrammer(target,b'\0'*0x600)
        with self.assertRaises(ValueError):job.stage({'action':'begin','address':0x08080000,'byte_length':0x20000,'sha256':'0'*64})
        self.assertEqual(target.entries,[]);self.assertEqual(target.stops,0)

    async def test_host_executor_uses_same_package_for_large_last_sectors(self):
        for row in DEVICES[1:]:
            target=LoaderModel(row);job=FlashProgrammer(target)
            data=bytes(range(256))*512;address=0x08000000+row[3]*1024-len(data)
            job.stage({'action':'begin','address':address,'byte_length':len(data),'sha256':hashlib.sha256(data).hexdigest(),'backend':'host','reset_after':False})
            for offset in range(0,len(data),4096):job.stage({'action':'chunk','offset':offset,'data':data[offset:offset+4096].hex()})
            result=await job.commit()
            self.assertTrue(result['verified']);self.assertEqual(target.erases,[address])
            self.assertEqual(len(target.programs),2 if row[0]=='STM32H723VG' else 8)
            self.assertEqual(target.flash[-len(data):],data)
            self.assertEqual(target.flash[:-len(data)],b'\xa5'*(len(target.flash)-len(data)))

if __name__=='__main__':unittest.main()
