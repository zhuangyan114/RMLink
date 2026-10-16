"""Bounded CMSIS-DAP TCP transport. No write replay after uncertain failures."""
import asyncio
import struct
from rmlink_protocol import packet_size_from_info
from .tcp_options import configure_probe_tcp


class TcpTransport:
    def __init__(self, host, port=3241):
        self.host, self.port = host, port
        self.reader = self.writer = None
        self.write_lock = asyncio.Lock()
        self.packet_size = 64

    async def connect(self):
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 3)
        configure_probe_tcp(self.writer.get_extra_info('socket'))
        self.packet_size = packet_size_from_info((await self.exchange_many([b'\x00\xff']))[0])

    async def send(self, packets):
        if not 1 <= len(packets) <= 8 or any(not 1 <= len(p) <= self.packet_size for p in packets):
            raise ValueError('TCP DAP batch exceeds negotiated capacity')
        frames = b''.join(struct.pack('<4sHBB', b'DAP\0', len(p), 1, 0) + p for p in packets)
        async with self.write_lock:
            self.writer.write(frames)
            await asyncio.wait_for(self.writer.drain(), 3)

    async def receive(self):
        header = await asyncio.wait_for(self.reader.readexactly(8), 3)
        magic, size, kind, reserved = struct.unpack('<4sHBB', header)
        if magic != b'DAP\0' or kind != 2 or reserved or not 1 <= size <= self.packet_size:
            raise ConnectionError('Invalid TCP DAP response; transport completion is unknown')
        return await asyncio.wait_for(self.reader.readexactly(size), 3)

    async def exchange_many(self, packets):
        await self.send(packets)
        return [await self.receive() for _ in packets]

    async def abort(self): await self.send([b'\x07'])

    async def close(self):
        if self.writer:
            self.writer.close()
            try: await self.writer.wait_closed()
            except OSError: pass
        self.reader = self.writer = None
