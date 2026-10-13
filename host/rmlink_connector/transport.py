"""Bounded CMSIS-DAP TCP transport. No write replay after uncertain failures."""
import asyncio
import socket
import struct


class TcpTransport:
    def __init__(self, host, port=3241):
        self.host, self.port = host, port
        self.reader = self.writer = None
        self.write_lock = asyncio.Lock()

    async def connect(self):
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 3)
        self.writer.get_extra_info('socket').setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    async def send(self, packets):
        frames = b''.join(struct.pack('<4sHBB', b'DAP\0', len(p), 1, 0) + p for p in packets)
        async with self.write_lock:
            self.writer.write(frames)
            await asyncio.wait_for(self.writer.drain(), 3)

    async def receive(self):
        header = await asyncio.wait_for(self.reader.readexactly(8), 3)
        magic, size, kind, reserved = struct.unpack('<4sHBB', header)
        if magic != b'DAP\0' or kind != 2 or reserved or not 1 <= size <= 64:
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
