"""Independent MEM-AP model: TAR wraps at 1KiB and reads have side effects."""
import struct
import unittest
from rmlink_connector.target import TargetEngine,TargetAccessError


class MemAp:
    def __init__(self,capacity):
        self.packet_size=capacity;self.packets=[];self.addresses=[];self.fail_after=None
    async def submit(self,source,packets,priority):
        assert source=='owner' and len(packets)==1
        p=packets[0];self.packets.append(p)
        assert p[:2]==b'\x05\0' and len(p)<=self.packet_size
        assert p[3:18]==b'\x08'+struct.pack('<I',0)+b'\x01'+struct.pack('<I',0x23000052)+b'\x05'+p[14:18]
        tar=None;values=[];position=3
        for completed in range(p[2]):
            flag=p[position];position+=1
            if flag in (8,1,5):
                value=int.from_bytes(p[position:position+4],'little');position+=4
                if flag==5:tar=value
                continue
            assert flag==15 and tar is not None
            if self.fail_after is not None and len(self.addresses)>=self.fail_after:
                return [bytes([5,completed,4])+struct.pack('<'+'I'*len(values),*values)]
            self.addresses.append(tar);values.append(tar^0x5aa55aa5)
            tar=(tar&~0x3ff)|((tar+4)&0x3ff)
        assert position==len(p)
        return [bytes([5,p[2],1])+struct.pack('<'+'I'*len(values),*values)]


class RangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_capacities_wrap_and_exact_peripheral_read_order(self):
        for capacity in (64,128,256,512):
            for address,size in ((0x080003fd,4096),(0x20004ffb,5),(0x400003f8,32)):
                with self.subTest(capacity=capacity,address=address):
                    bus=MemAp(capacity);target=TargetEngine(bus,'owner')
                    result=await target.read_range(address,size)
                    words=list(range(address&~3,(address+size+3)&~3,4))
                    raw=struct.pack('<'+'I'*len(words),*(a^0x5aa55aa5 for a in words))
                    self.assertEqual(bytes.fromhex(result['data']),raw[address&3:(address&3)+size])
                    self.assertEqual(bus.addresses,words)
                    self.assertTrue(all(len(p)<=capacity for p in bus.packets))
    async def test_partial_read_is_counted_and_never_replayed(self):
        bus=MemAp(64);bus.fail_after=3;target=TargetEngine(bus,'owner')
        with self.assertRaises(TargetAccessError) as raised:await target.read_range(0x20000001,100)
        self.assertEqual(raised.exception.completed_bytes,11)
        self.assertEqual(raised.exception.code,'DapAckFault');self.assertEqual(len(bus.packets),1)
        self.assertEqual(bus.addresses,[0x20000000,0x20000004,0x20000008])
        bus=MemAp(64);bus.fail_after=3;target=TargetEngine(bus,'owner')
        with self.assertRaises(TargetAccessError) as raised:await target.read_range(0x200003ff,32)
        self.assertEqual(raised.exception.completed_bytes,9)
        self.assertEqual(bus.addresses,[0x200003fc,0x20000400,0x20000404])
    async def test_subword_peripheral_is_rejected_without_io(self):
        bus=MemAp(512);target=TargetEngine(bus,'owner')
        for address,size in ((0x40000001,4),(0x40000000,3),(0x20000000,4097)):
            with self.assertRaises(ValueError):await target.read_range(address,size)
        self.assertEqual(bus.packets,[])


if __name__=='__main__':unittest.main()
