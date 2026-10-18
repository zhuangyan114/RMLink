"""One physical connection and one whole-session target owner per probe.

API and virtual USB clients share this broker. Priorities apply between batches;
a batch remains ordered. Abort bypasses the job queue, never another owner.
"""
import asyncio
from dataclasses import dataclass
import itertools
import sys

from .transport import TcpTransport
from rmlink_protocol import without_usb_padding

PRIORITIES = {'control': 0, 'watch': 1, 'timeline': 2, 'background': 3}


def denied(command):
    if command == 2: return b'\x02\x00'
    if command == 5: return b'\x05\x00\x08'
    if command == 6: return b'\x06\x00\x00\x08'
    return bytes([command, 0xff])


def informational(p):
    return p[0] in (0, 1) or (len(p) >= 2 and (
        (p[0] == 0x80 and p[1] in (0, 4, 6, 7, 9, 10, 11, 12, 14)) or (p[0] == 0x81 and p[1] in (0,13)) or
        (p[0] == 0x82 and p[1] == 0) or (p[0] == 0x83 and p[1] == 0)))


def provisioning(job, packet, transport, owner):
    return (job.source == 'wifi-config' and owner is None and getattr(transport, 'native_usb', False)
            and len(packet) >= 2 and packet[0] == 0x80 and packet[1] in (2, 3))


@dataclass
class Job:
    source: str
    packets: list
    future: asyncio.Future


class Broker:
    def __init__(self, transport, device_id):
        self.transport, self.device_id = transport, device_id
        self.queue = asyncio.PriorityQueue(maxsize=64)
        self.ids = itertools.count()
        self.owner = None
        self.maintenance = None
        self.active = None
        self.closed_sources = set()
        self.task = None
        self.failed = None
        self.on_lost = set()

    async def start(self):
        await self.transport.connect()
        self.task = asyncio.create_task(self._worker())

    @property
    def packet_size(self):
        return getattr(self.transport, 'packet_size', 64)

    async def submit(self, source, packets, priority='control'):
        packets = [bytes(p) for p in packets]
        if priority not in PRIORITIES or not 1 <= len(packets) <= 8 or any(not 1 <= len(p) <= self.packet_size or p[0] == 7 for p in packets):
            raise ValueError('Expected 1..8 packets within negotiated capacity; Abort uses its own path')
        if self.failed or source in self.closed_sources: raise ConnectionError('Session/transport is closed')
        packets = [without_usb_padding(p) for p in packets]
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((PRIORITIES[priority], next(self.ids), Job(source, packets, future)))
        # Client cancellation cannot abandon an in-flight write or shift responses.
        return await asyncio.shield(future)

    async def abort(self, source):
        if self.owner != source: return False
        if self.active and self.active.source == source: await self.transport.abort()
        return True

    async def release(self, source):
        self.closed_sources.add(source)
        await self.abort(source)
        if not self.failed:
            # FIFO/priority cleanup after the active batch; stale source jobs skip.
            future = asyncio.get_running_loop().create_future()
            await self.queue.put((-1, next(self.ids), Job(source, [], future)))
            await asyncio.shield(future)

    async def _worker(self):
        try:
            while True:
                _, _, job = await self.queue.get()
                self.active = job
                if not job.packets:
                    if self.owner == job.source:
                        await self.transport.exchange_many([b'\x03'])
                        self.owner = None
                    job.future.set_result([])
                    self.active = None
                    continue
                if job.source in self.closed_sources:
                    job.future.set_exception(ConnectionError('Client closed before execution'))
                    self.active = None
                    continue
                replies = []
                # Contiguous non-transition packets are sent together, max 8.
                i = 0
                while i < len(job.packets):
                    if job.source in self.closed_sources:
                        job.future.set_exception(ConnectionError('Client closed during batch'))
                        break
                    p = job.packets[i]
                    ota = p[0] == 0x83 and len(p) >= 2 and p[1] != 0
                    maintenance = ota and job.source == self.maintenance and self.owner is None
                    if ((self.maintenance and job.source != self.maintenance and not informational(p)) or
                        (ota and not maintenance)):
                        replies.append(denied(p[0])); i += 1; continue
                    if not informational(p) and not maintenance and not provisioning(job,p,self.transport,self.owner) and p[0] not in (2, 0x11) and self.owner != job.source:
                        if p[0] == 3 and self.owner is None: replies.append(b'\x03\x00')
                        else: replies.append(denied(p[0]))
                        i += 1; continue
                    if p[0] in (2, 0x11):
                        if self.owner not in (None, job.source): replies.append(denied(p[0]))
                        else:
                            response = (await self.transport.exchange_many([p]))[0]
                            if response == (b'\x02\x01' if p[0] == 2 else b'\x11\x00'): self.owner = job.source
                            replies.append(response)
                        i += 1; continue
                    if p[0] == 3:
                        response = (await self.transport.exchange_many([p]))[0]
                        if response == b'\x03\x00': self.owner = None
                        replies.append(response); i += 1; continue
                    group = [p]; i += 1
                    while i < len(job.packets):
                        n = job.packets[i]
                        ota = n[0] == 0x83 and len(n) >= 2 and n[1] != 0
                        maintenance = ota and job.source == self.maintenance and self.owner is None
                        if (self.maintenance and job.source != self.maintenance and not informational(n)) or (ota and not maintenance): break
                        if n[0] in (2, 3, 0x11) or (not informational(n) and not maintenance and not provisioning(job,n,self.transport,self.owner) and self.owner != job.source): break
                        group.append(n); i += 1
                    replies.extend(await self.transport.exchange_many(group))
                if not job.future.done(): job.future.set_result(replies)
                self.active = None
        except asyncio.CancelledError:
            if self.active and not self.active.future.done():
                self.active.future.set_exception(ConnectionError('Physical connection closed; completion may be unknown'))
            pass
        except Exception as exc:
            self.failed = f'{type(exc).__name__}: {exc}'
            commands=[p[0] for p in self.active.packets] if self.active else []
            print(f'RMLink physical connection lost: device={self.device_id} reason={self.failed} commands={commands}; completion may be unknown',file=sys.stderr,flush=True)
            if self.active and not self.active.future.done(): self.active.future.set_exception(ConnectionError(self.failed))
            self.owner = None
            for callback in tuple(self.on_lost): callback()
        finally:
            while not self.queue.empty():
                _, _, pending = self.queue.get_nowait()
                if not pending.future.done(): pending.future.set_exception(ConnectionError('Physical connection lost'))

    async def close(self):
        if self.owner and not self.failed:
            try: await self.release(self.owner)
            except (OSError, ConnectionError): pass
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.transport.close()
