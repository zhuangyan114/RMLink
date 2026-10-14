"""Independent wire/consumer/plan checks, including split boundaries and pressure."""
import asyncio
from collections import deque
import struct
import unittest
import zlib
from rmlink_connector.capture_protocol import FrameDecoder,decode_record,HEADER,SAMPLE
from rmlink_connector.subscriptions import compile_config,Subscriptions


def record(seq=0,kind=1):
    data=struct.pack('<II',0x13579bdf,seq)
    if kind==3: data=struct.pack('<IQI',0,123,3)+b'a\x00b'
    payload=SAMPLE.pack(seq,123456789,42,3,0,len(data))+data
    return HEADER.pack(b'RML1',1,kind,1 if kind==1 else 3,0,7,1,seq,len(payload),1,32,zlib.crc32(payload))+payload


class CaptureWireTests(unittest.TestCase):
    def test_all_split_positions_and_coalescing(self):
        data=b''.join(record(i) for i in range(20))
        for chunk in (1,2,7,31,32,63,384,len(data)):
            parser=FrameDecoder(); frames=[]
            for i in range(0,len(data),chunk): frames.extend(parser.feed(data[i:i+chunk]))
            self.assertEqual([decode_record(f)['sequence'] for f in frames],list(range(20)))
            self.assertFalse(parser.pending)
    def test_crc_and_length_reject_before_allocation(self):
        f=bytearray(record()); f[-1]^=1
        with self.assertRaisesRegex(ValueError,'CRC'): decode_record(f)
        f=bytearray(record()); f[20:24]=struct.pack('<I',2**32-1)
        with self.assertRaises(ValueError): FrameDecoder().feed(f[:32])
    def test_rtt_binary_payload(self):
        result=decode_record(record(kind=3))
        self.assertEqual(result['data'],b'a\x00b'); self.assertEqual(result['byte_sequence'],123)
    def test_address_and_period_plan_bounds(self):
        self.assertEqual(compile_config({'kind':'memory_series','addresses':[0x20000000]})['period_us'],1000)
        for addresses in ([0xe000edf0],[0x20004ffe],[0x20005000],[],[0x20000000]*33,[True]):
            with self.assertRaises(ValueError): compile_config({'kind':'memory_series','addresses':addresses})
        for period in (0,199,10000001,1.5,True):
            with self.assertRaises(ValueError): compile_config({'kind':'memory_series','addresses':[0x20000000],'period_us':period})


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_cursors_and_known_pressure_gap(self):
        captures=Subscriptions(None)
        captures.records=deque([(i,record(i)) for i in range(100,110)],maxlen=10)
        first=await captures.read(99,3,0); second=await captures.read(105,2,0)
        self.assertEqual(first['cursor'],102); self.assertEqual(second['cursor'],107)
        self.assertEqual((await captures.read(80,3,0))['consumer_gap'],19)
    async def test_wait_wakes_on_stream_error(self):
        captures=Subscriptions(None)
        async def failed():
            await asyncio.sleep(.01)
            async with captures.condition: captures.error='lost'; captures.condition.notify_all()
        task=asyncio.create_task(failed())
        self.assertEqual((await captures.read(-1,1,5000))['error'],'lost')
        await task

if __name__=='__main__': unittest.main()
