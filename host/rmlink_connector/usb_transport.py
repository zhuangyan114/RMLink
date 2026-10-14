"""Native wired transport for the same Broker and enhanced target engine."""
import asyncio
from rmlink_native_usb import UsbClient


class UsbTransport:
    native_usb=True
    def __init__(self,serial):
        self.serial=serial;self.client=None;self.packet_size=64;self.pending=set()
    async def connect(self):
        self.client=await asyncio.to_thread(UsbClient,serial=self.serial)
        self.packet_size=self.client.packet_size
    async def exchange_many(self,packets):
        task=asyncio.create_task(asyncio.to_thread(self.client.exchange_many,packets))
        self.pending.add(task);task.add_done_callback(self.pending.discard)
        replies=await asyncio.shield(task)
        return replies
    async def abort(self):
        # Writes are independent of the worker's IN wait; do not add another owner.
        await asyncio.to_thread(self.client.abort_transfer)
    async def close(self):
        if self.pending:await asyncio.gather(*self.pending,return_exceptions=True)
        if self.client:await asyncio.to_thread(self.client.close)
        self.client=None
