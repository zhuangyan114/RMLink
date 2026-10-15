"""Public high-level target operations against an independent memory/DP oracle."""
import struct
import unittest
from rmlink_connector.broker import Broker
from rmlink_connector.target import TargetEngine,TargetAccessError

class TargetModel:
    def __init__(self,capacity):self.packet_size=capacity;self.memory={0xe000edf0:0x30000,0xe0002000:0x60,0xe000ed00:0x411fc231,0xe0042000:0x410};self.requests=[];self.fault=False;self.regs={};self.mask=0xffffffff;self.features=0;self.csw=0
    async def connect(self):pass
    async def close(self):pass
    async def abort(self):pass
    def read(self,address):return self.memory.get(address,address^0x13579bdf)
    async def exchange_many(self,packets):
        out=[]
        for p in packets:
            self.requests.append(p)
            if p[0]==0x81:
                if p[1]==0:out.append(bytes([0x81,0,0])+bytes(17)+struct.pack('<I',self.features))
                elif p[1]==17:out.append(b'\x81\x11\x05' if self.fault else b'\x81\x11\x00'+struct.pack('<I',self.memory[0xe000edf0]))
                else:out.append(bytes([0x81,p[1],0]))
                continue
            if p[0]!=5:out.append(bytes([p[0],1 if p[0]==2 else 0]));continue
            if self.fault:out.append(b'\x05\x00\x07');continue
            pos=3;address=None;data=[]
            for _ in range(p[2]):
                flags=p[pos];pos+=1
                if flags&0x10:
                    value=struct.unpack_from('<I',p,pos)[0];pos+=4
                    assert self.read(address)&self.mask==value
                elif flags&2:
                    data.append(self.read(address) if flags==15 else 0xf0000040)
                    if flags==15 and self.csw&0x30==0x10:address=(address&~0x3ff)|((address+4)&0x3ff)
                else:
                    value=struct.unpack_from('<I',p,pos)[0];pos+=4
                    if flags==0x20:self.mask=value
                    elif flags==1:self.csw=value
                    elif flags==5:address=value
                    elif flags==13:
                        self.memory[address]=value
                        if address==0xe000edf0:self.memory[address]=0x30000 if value&2 or value&4 else 0x10000
                        if address==0xe000edf4:
                            if value&0x10000:self.regs[value&31]=self.memory[0xe000edf8]
                            else:self.memory[0xe000edf8]=self.regs.get(value,0x08000100+value*4)
                        if self.csw&0x30==0x10:address=(address&~0x3ff)|((address+4)&0x3ff)
            out.append(bytes([5,p[2],1])+struct.pack('<'+'I'*len(data),*data))
        return out

class TargetEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_advertised_core_state_uses_one_owner_packet_without_sampling_barrier(self):
        model=TargetModel(512);model.features=16;broker=Broker(model,'model');await broker.start()
        engine=TargetEngine(broker,'owner')
        try:
            await engine.connect();before=len(model.requests)
            self.assertEqual((await engine.execute('state',{}))['state'],'Halted')
            self.assertEqual(model.requests[before:],[b'\x81\x11'])
            model.fault=True
            with self.assertRaises(TargetAccessError):await engine.execute('state',{})
        finally:await broker.close()
    async def check_capacity(self,capacity):
        model=TargetModel(capacity);broker=Broker(model,'model');await broker.start()
        engine=TargetEngine(broker,'owner');await engine.connect()
        try:
            result=await engine.execute('read_memory',{'address':0x200003ff,'byte_length':1025})
            expected=b''.join(struct.pack('<I',model.read(a)) for a in range(0x200003fc,0x20000800,4))[3:1028]
            self.assertEqual(bytes.fromhex(result['data']),expected)
            before=len(model.requests)
            reads=[{'address':0x200003ff,'byte_length':3},{'address':0x200003fc,'byte_length':8},
                {'address':0xe000ed00,'byte_length':4},{'address':0xe000ed00,'byte_length':4}]
            batch=await engine.execute('read_batch',{'reads':reads})
            for request,value in zip(reads,batch['reads']):
                address,size=request['address'],request['byte_length']
                oracle=b''.join(struct.pack('<I',model.read(a)) for a in range(address&~3,(address+size+3)&~3,4))
                self.assertEqual(bytes.fromhex(value['data']),oracle[address&3:(address&3)+size])
            wire=[p for p in model.requests[before:] if p[0]==5]
            self.assertEqual(len(wire),1) # all ranges fit one independently addressed command
            target_packets=[p for p in model.requests if p[0]==5]
            self.assertTrue(all(len(p)<=capacity for p in target_packets))
            self.assertTrue(all(p[3]==8 and p[8]==1 for p in target_packets[3:]))
            data=bytes(range(256))*8
            result=await engine.execute('write_memory',{'address':0x200003f8,'data':data.hex()})
            self.assertEqual(result['bytes_written'],len(data))
            actual=b''.join(struct.pack('<I',model.memory[a]) for a in range(0x200003f8,0x200003f8+len(data),4))
            self.assertEqual(actual,data)
            self.assertEqual((await engine.execute('read_register',{'index':15}))['value'],0x0800013c)
            async with engine.control():
                await engine.write_registers([(0,0x12345678),(13,0x20005000)])
                self.assertEqual(await engine.read_registers([0,13]),[0x12345678,0x20005000])
            self.assertEqual((await engine.execute('run',{}))['state'],'Running')
            with self.assertRaises(ValueError):await engine.execute('read_register',{'index':0})
            self.assertEqual((await engine.execute('halt',{}))['state'],'Halted')
            self.assertEqual((await engine.execute('step',{}))['state'],'Halted')
            self.assertEqual((await engine.execute('reset',{}))['state'],'Halted')
            # Free comparator oracle must start at zero, then preserve occupied slots.
            for i in range(6):model.memory[0xe0002008+i*4]=0
            bp=await engine.execute('set_breakpoint',{'address':0x08000102})
            self.assertEqual(model.memory[0xe0002008],0x88000101)
            await engine.execute('clear_breakpoint',{'id':bp['id']});self.assertEqual(model.memory[0xe0002008],0)
            model.fault=True
            with self.assertRaises(TargetAccessError) as ctx:await engine.execute('state',{})
            self.assertEqual(ctx.exception.code,'DapNoAck')
            self.assertEqual(model.requests[-1],b'\x81\x09') # finally closes barrier even on failure
        finally:await broker.close()
    async def test_native64_and_wireless512(self):
        await self.check_capacity(64);await self.check_capacity(512)

if __name__=='__main__':unittest.main()
