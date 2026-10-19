"""Native wired transport for the same Broker and enhanced target engine."""
import asyncio
import time
from rmlink_native_usb import UsbClient
from .telemetry import Traffic


class UsbTransport:
    native_usb=True
    def __init__(self,serial):
        self.serial=serial;self.client=None;self.packet_size=64;self.pending=set()
        self.traffic=Traffic()
        self.stream_pending=set()
    async def connect(self):
        self.client=await asyncio.to_thread(UsbClient,bcd_device=0x0002,serial=self.serial)
        self.packet_size=self.client.packet_size
    async def exchange_many(self,packets):
        started=time.monotonic()
        task=asyncio.create_task(asyncio.to_thread(self.client.exchange_many,packets))
        self.pending.add(task);task.add_done_callback(self.pending.discard)
        replies=await asyncio.shield(task)
        self.traffic.tx_bytes+=len(packets)*self.packet_size
        self.traffic.rx_bytes+=sum(map(len,replies))
        self.traffic.last_batch_ms=(time.monotonic()-started)*1000
        return replies
    async def abort(self):
        # Writes are independent of the worker's IN wait; do not add another owner.
        await asyncio.to_thread(self.client.abort_transfer)
        self.traffic.tx_bytes+=self.packet_size
    async def open_capture_stream(self,ticket):
        task=asyncio.create_task(asyncio.to_thread(self.client.open_stream,ticket))
        try:await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task,return_exceptions=True)
            await self.close_capture_stream()
            raise
    def _stream_finished(self,task):
        self.stream_pending.discard(task)
        if not task.cancelled():task.exception() # cancelled reader may no longer await its worker
    async def read_capture_stream(self):
        task=asyncio.create_task(asyncio.to_thread(self.client.read_stream))
        self.stream_pending.add(task);task.add_done_callback(self._stream_finished)
        data=await asyncio.shield(task)
        self.traffic.rx_bytes+=len(data)
        return data
    async def close_capture_stream(self):
        if self.client and getattr(self.client,'streaming',False):
            await asyncio.to_thread(self.client.stop_stream)
            if self.stream_pending:await asyncio.gather(*self.stream_pending,return_exceptions=True)
            await asyncio.to_thread(self.client.close_stream)
    async def close(self):
        await self.close_capture_stream()
        if self.pending:await asyncio.gather(*self.pending,return_exceptions=True)
        if self.client:await asyncio.to_thread(self.client.close)
        self.client=None
