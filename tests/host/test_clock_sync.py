import struct
import time
import unittest
from rmlink_connector.broker import Broker
from rmlink_connector.clock_sync import ClockSync


class ClockProbe:
    packet_size=512
    bad=False
    clock=1000000
    async def connect(self):pass
    async def close(self):pass
    async def exchange_many(self,packets):
        result=[]
        for p in packets:
            if p[0]==2:result.append(b'\x02\x01');continue
            if p[0]==3:result.append(b'\x03\x00');continue
            self.clock+=100
            token=b'X'*8 if self.bad else p[2:10]
            result.append(b'\x81\x0d\x00'+token+struct.pack('<QQ',self.clock,self.clock))
        return result


class ClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_clock_queries_do_not_claim_or_interrupt_an_existing_owner(self):
        probe=ClockProbe();broker=Broker(probe,'test');await broker.start();sync=ClockSync()
        try:
            result=await sync.measure(broker,4)
            self.assertIsNone(broker.owner)
            self.assertEqual(result['mapping'],min(result['measurements'],key=lambda r:r['uncertainty_ns']))
            self.assertFalse(result['one_way_delay_measured'])
            await broker.submit('debugger',[b'\x02\x01'])
            again=await sync.measure(broker,2)
            self.assertEqual(broker.owner,'debugger')
            self.assertEqual(again['clock_epoch'],result['clock_epoch'])
            probe.clock=0
            rebooted=await sync.measure(broker,1)
            self.assertNotEqual(rebooted['clock_epoch'],result['clock_epoch'])
        finally:await broker.close()
    async def test_bad_echo_and_unbounded_sample_request_rejected(self):
        probe=ClockProbe();probe.bad=True;broker=Broker(probe,'test');await broker.start()
        try:
            sync=ClockSync()
            with self.assertRaises(ConnectionError):await sync.measure(broker,1)
            for count in (0,17,True):
                with self.assertRaises(ValueError):await sync.measure(broker,count)
            self.assertIsNone(broker.owner)
        finally:await broker.close()
