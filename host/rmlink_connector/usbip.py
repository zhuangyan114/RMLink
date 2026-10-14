"""Loopback USB/IP v1.1.1 server translating URBs to the shared DAP broker.

Protocol based on https://docs.kernel.org/usb/usbip_protocol.html . No USB/IP
implementation is copied from a reference repository. Endpoints are bounded,
pending IN URBs can be unlinked, and cancellation never replays target writes.
"""
import asyncio
from collections import deque
import contextlib
import struct
import uuid

from .descriptors import Descriptors
import logging
import os

OP = struct.Struct('>HHI')
URB = struct.Struct('>IIIIIiiiii8s')
DEVICE = struct.Struct('>256s32sIIIHHHBBBBBB')


def device_record(device_id):
    busid = 'rmlink-' + device_id
    return DEVICE.pack(b'/rmlink/' + device_id.encode(), busid.encode(), 1, 1, 2,
                       0x303a, 0x1002, 0x0100, 0, 0, 0, 1, 1, 1)


class ImportSession:
    def __init__(self, reader, writer, broker, *, coalesce_us=None):
        self.reader, self.writer, self.broker = reader, writer, broker
        self.source = 'usbip-' + uuid.uuid4().hex
        self.descriptors = Descriptors(broker.device_id)
        self.packet_size = getattr(broker, 'packet_size', 64)
        self.send_lock = asyncio.Lock()
        self.in_urbs = deque()
        self.responses = deque()
        self.in_sequences = set()
        self.out_sequences = set()
        self.outstanding_dap = 0
        self.commands = asyncio.Queue(maxsize=8)
        if coalesce_us is None:
            coalesce_us = int(os.environ.get('RMLINK_USBIP_BATCH_US', '0'))
        if not 0 <= coalesce_us <= 2000:
            raise ValueError('RMLINK_USBIP_BATCH_US must be between 0 and 2000')
        self.coalesce_seconds = coalesce_us / 1000000
        self.flush_requested = asyncio.Event()
        self.sender = None
        self.done = False

    def lost(self): self.writer.close()

    async def send_submit(self, seq, direction, ep, status, data=b'', actual=None):
        if self.done: return
        if actual is None: actual = len(data)
        # Basic RET fields devid/direction/endpoint are unused and must be zero.
        packet = URB.pack(3, seq, 0, 0, 0, status, actual, 0, 0, 0, bytes(8))
        if direction == 1 and status == 0: packet += data
        async with self.send_lock:
            self.writer.write(packet)
            await asyncio.wait_for(self.writer.drain(), 3)

    async def drain_in(self):
        while self.in_urbs and self.responses:
            seq, length = self.in_urbs.popleft()
            self.in_sequences.remove(seq)
            response = self.responses.popleft()
            self.outstanding_dap -= 1
            if length < len(response):
                # Consume the reply and close: reusing a truncated DAP stream is unsafe.
                await self.send_submit(seq, 1, 3, -75)
                self.writer.close(); return
            await self.send_submit(seq, 1, 3, 0, response)

    async def pump(self):
        while not self.done:
            request = await self.commands.get()
            self.flush_requested.clear()
            # A synchronous host can submit its next OUT only after the ACK.
            # Aggregate a TAR setup or multiword block until IN demand or a
            # full window. Single-word/control transfers and Abort bypass it.
            bulk_memory = (request[:4] == b'\x05\x00\x01\x05'
                           or (request[0] == 6 and len(request) >= 5
                               and int.from_bytes(request[2:4], 'little') > 1))
            if (self.coalesce_seconds and bulk_memory
                    and not self.in_urbs and self.commands.qsize() < 7):
                try:
                    await asyncio.wait_for(self.flush_requested.wait(), self.coalesce_seconds)
                except asyncio.TimeoutError:
                    pass  # Accepted OUT must also execute if no IN follows.
            if self.done:
                return
            group = [request]
            # Preserve FIFO order, and batch packets already received by the RX loop.
            while len(group) < 8 and not self.commands.empty(): group.append(self.commands.get_nowait())
            responses = await self.broker.submit(self.source, group)
            self.responses.extend(responses)
            await self.drain_in()

    async def run(self):
        self.broker.on_lost.add(self.lost)
        self.sender = asyncio.create_task(self.pump())
        self.sender.add_done_callback(lambda task: self.writer.close() if not task.cancelled() and task.exception() else None)
        try:
            while True:
                raw = await self.reader.readexactly(48)
                cmd, seq, devid, direction, ep, flags, length, start, packets, interval, setup = URB.unpack(raw)
                if os.environ.get('RMLINK_USBIP_TRACE') == '1':
                    logging.info('USB/IP cmd=%d seq=%d dir=%d ep=%d len=%d field=%#x',
                                 cmd, seq, direction, ep, length, flags & 0xffffffff)
                if direction not in (0, 1) or devid != 0x10001:
                    raise ValueError(f'Invalid USB/IP device {devid:#x} or direction {direction}')
                if cmd == 2:  # UNLINK: union starts with target seqnum.
                    unlink_seq = flags & 0xffffffff
                    if unlink_seq in self.in_sequences:
                        self.in_urbs = deque(u for u in self.in_urbs if u[0] != unlink_seq)
                        self.in_sequences.remove(unlink_seq)
                        result = -104  # A successfully unlinked SUBMIT gets no RET_SUBMIT.
                    else: result = 0
                    async with self.send_lock:
                        self.writer.write(URB.pack(4, seq, 0, 0, 0, result, 0, 0, 0, 0, bytes(8)))
                        await self.writer.drain()
                    continue
                # usbip-win2 encodes non-isochronous URBs with -1; Linux uses 0.
                if cmd != 1 or packets not in (0, -1) or length < 0 or length > 4096 or ep not in (0, 3):
                    raise ValueError(f'Unsupported URB cmd={cmd} ep={ep} length={length} packets={packets}')
                if seq in self.in_sequences or seq in self.out_sequences: raise ValueError('Duplicate active sequence')
                data = await asyncio.wait_for(self.reader.readexactly(length), 3) if direction == 0 else b''
                if ep == 0:
                    if bool(setup[0] & 0x80) != bool(direction): raise ValueError('Control direction mismatch')
                    try:
                        response = self.descriptors.control(setup, data)
                        await self.send_submit(seq, direction, ep, 0, response, len(response) if direction else length)
                    except ValueError: await self.send_submit(seq, direction, ep, -32)
                elif direction == 0:
                    if not 1 <= length <= self.packet_size:
                        await self.send_submit(seq, direction, ep, -32); continue
                    if data[0] == 7:
                        await self.broker.abort(self.source)
                        await self.send_submit(seq, 0, ep, 0, actual=length)
                        continue
                    if self.outstanding_dap >= 8: raise ValueError('Host exceeded advertised DAP packet count')
                    self.outstanding_dap += 1
                    self.commands.put_nowait(data)
                    if self.commands.qsize() >= 7:
                        self.flush_requested.set()
                    # ACK means accepted USB data, not successful target execution.
                    await self.send_submit(seq, 0, ep, 0, actual=length)
                else:
                    # USB physical packets remain 64; a logical DAP transfer may
                    # span several. WinUSB can also round its read allocation up.
                    if not 1 <= length <= 4096 or len(self.in_urbs) >= 8:
                        raise ValueError(f'Bulk IN length={length}, pending={len(self.in_urbs)}, seq={seq}')
                    self.in_sequences.add(seq)
                    self.in_urbs.append((seq, length))
                    self.flush_requested.set()
                    await self.drain_in()
        finally:
            self.done = True
            self.broker.on_lost.discard(self.lost)
            # Source closes before cancelling its pending worker awaits.
            await self.broker.release(self.source)
            self.sender.cancel()
            await asyncio.gather(self.sender, return_exceptions=True)


class UsbIpServer:
    def __init__(self, brokers):
        self.brokers, self.imported = brokers, set()
        self.server = None
        self.connections = set()

    async def start(self, port=3240):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', port, limit=8192)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.connections.add(writer)
        imported = None
        try:
            version, code, status = OP.unpack(await asyncio.wait_for(reader.readexactly(8), 3))
            if version != 0x0111 or status: return
            if code == 0x8005:
                ids = [key for key, broker in self.brokers.items() if not broker.failed]
                writer.write(OP.pack(version, 5, 0) + struct.pack('>I', len(ids)))
                for key in ids: writer.write(device_record(key) + bytes([0xff, 0, 0, 0]))
                await writer.drain()
            elif code == 0x8003:
                busid = (await asyncio.wait_for(reader.readexactly(32), 3)).split(b'\0', 1)[0].decode('ascii')
                key = busid.removeprefix('rmlink-')
                if key not in self.brokers or self.brokers[key].failed:
                    writer.write(OP.pack(version, 3, 4)); await writer.drain(); return
                if key in self.imported:
                    writer.write(OP.pack(version, 3, 2)); await writer.drain(); return
                self.imported.add(key); imported = key
                writer.write(OP.pack(version, 3, 0) + device_record(key))
                await writer.drain()
                await ImportSession(reader, writer, self.brokers[key]).run()
        except asyncio.IncompleteReadError:
            pass
        except (OSError, TimeoutError, ValueError, asyncio.QueueFull, ConnectionError) as exc:
            logging.warning('USB/IP connection closed: %s', exc)
        finally:
            if imported: self.imported.discard(imported)
            self.connections.discard(writer)
            writer.close()
            with contextlib.suppress(OSError): await writer.wait_closed()

    async def close(self):
        if self.server: self.server.close(); await self.server.wait_closed()
        for writer in tuple(self.connections): writer.close()
