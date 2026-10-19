import asyncio
import threading
import unittest
from rmlink_connector.broker import Broker
from rmlink_connector.usb_transport import UsbTransport


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_fault_diagnostics_can_observe_another_owner_without_acquiring_it(self):
        requests=[]
        class Probe:
            packet_size=64
            async def connect(self):pass
            async def close(self):pass
            async def exchange_many(self,packets):
                requests.extend(packets);return [p+b'\x02' for p in packets]
        broker=Broker(Probe(),'test');await broker.start();broker.owner='active-target'
        try:
            reply=await broker.submit('device-health',[b'\x80\x0d'],'background')
            self.assertEqual(reply,[b'\x80\x0d\x02'])
            self.assertEqual(requests,[b'\x80\x0d']);self.assertEqual(broker.owner,'active-target')
        finally:broker.owner=None;await broker.close()
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
    async def test_cancelled_stream_read_finishes_before_alternate_and_handle_close(self):
        entered=threading.Event();release=threading.Event();order=[]
        class Native:
            streaming=True
            def read_stream(self):
                entered.set();release.wait(3);order.append('read');return b''
            def stop_stream(self):order.append('abort');release.set()
            def close_stream(self):order.append('alternate0');self.streaming=False
            def close(self):order.append('close')
        transport=UsbTransport('test');transport.client=Native()
        read=asyncio.create_task(transport.read_capture_stream())
        await asyncio.to_thread(entered.wait,1)
        read.cancel();await asyncio.gather(read,return_exceptions=True)
        await transport.close()
        self.assertEqual(order,['abort','read','alternate0','close'])
    async def test_cancelled_open_drains_control_then_restores_the_alternate(self):
        entered=threading.Event();release=threading.Event();order=[]
        class Native:
            streaming=False
            def open_stream(self,ticket):
                entered.set();release.wait(3);self.streaming=True;order.append('open')
            def stop_stream(self):order.append('abort')
            def close_stream(self):order.append('alternate0');self.streaming=False
        transport=UsbTransport('test');transport.client=Native()
        opening=asyncio.create_task(transport.open_capture_stream(b'X'*16))
        await asyncio.to_thread(entered.wait,1);opening.cancel();await asyncio.sleep(0)
        self.assertFalse(opening.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await opening
        self.assertEqual(order,['open','abort','alternate0'])
        self.assertFalse(transport.client.streaming)
    async def test_aborted_worker_error_after_reader_cancel_is_consumed(self):
        entered=threading.Event();release=threading.Event();warnings=[]
        class Native:
            streaming=True
            def read_stream(self):
                entered.set();release.wait(3);raise OSError('transfer aborted')
            def stop_stream(self):release.set()
            def close_stream(self):self.streaming=False
            def close(self):pass
        loop=asyncio.get_running_loop();old=loop.get_exception_handler()
        loop.set_exception_handler(lambda _,context:warnings.append(context))
        try:
            transport=UsbTransport('test');transport.client=Native()
            read=asyncio.create_task(transport.read_capture_stream())
            await asyncio.to_thread(entered.wait,1);read.cancel()
            await asyncio.gather(read,return_exceptions=True);await transport.close()
            await asyncio.sleep(.01)
            self.assertFalse(transport.stream_pending);self.assertEqual(warnings,[])
        finally:loop.set_exception_handler(old)
