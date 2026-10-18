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
    async def close(self):
        if self.pending:await asyncio.gather(*self.pending,return_exceptions=True)
        if self.client:await asyncio.to_thread(self.client.close)
        self.client=None
