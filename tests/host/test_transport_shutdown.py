import asyncio
import threading
import unittest
from rmlink_connector.broker import Broker
from rmlink_connector.usb_transport import UsbTransport


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_closing_an_active_unowned_query_completes_the_waiter(self):
        entered=asyncio.Event()
        class Probe:
            packet_size=64
            async def connect(self):pass
            async def close(self):pass
            async def exchange_many(self,packets):
                entered.set();await asyncio.Event().wait()
        broker=Broker(Probe(),'test');await broker.start()
        caller=asyncio.create_task(broker.submit('info',[b'\x00\xff']))
        await entered.wait();self.assertIsNone(broker.owner)
        await broker.close()
        with self.assertRaises(ConnectionError):await asyncio.wait_for(caller,1)
    async def test_native_handle_is_kept_until_the_sync_io_thread_finishes(self):
        entered=threading.Event();release=threading.Event();order=[]
        class Native:
            def exchange_many(self,packets):
                entered.set();release.wait(3);order.append('io');return [b'\x00\x02\x40\x00']
            def close(self):order.append('close')
        transport=UsbTransport('test');transport.client=Native()
        exchange=asyncio.create_task(transport.exchange_many([b'\x00\xff']))
        await asyncio.to_thread(entered.wait,1)
        exchange.cancel();await asyncio.gather(exchange,return_exceptions=True)
        close=asyncio.create_task(transport.close());await asyncio.sleep(0)
        self.assertFalse(close.done());self.assertEqual(order,[])
        release.set();await close
        self.assertEqual(order,['io','close'])
