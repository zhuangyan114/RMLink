"""Bounded memory-only burst aggregation preserves immediate control/IN paths."""
import asyncio
from contextlib import suppress
from unittest.mock import patch
import unittest

from rmlink_connector.usbip import ImportSession


class Writer:
    def __init__(self): self.writes = []
    def write(self, packet): self.writes.append(packet)
    async def drain(self): pass


class Broker:
    device_id = '50787d15a924'
    def __init__(self):
        self.groups = []
        self.called = asyncio.Event()
    async def submit(self, source, packets):
        self.groups.append(list(packets))
        self.called.set()
        # Real Broker.submit yields while the TCP exchange is in flight.
        await asyncio.sleep(0)
        return [bytes([p[0], 0]) for p in packets]


class BatchingTests(unittest.IsolatedAsyncioTestCase):
    async def stop(self, task, session):
        session.done = True
        task.cancel()
        with suppress(asyncio.CancelledError): await asyncio.wait_for(task, .5)

    async def test_memory_burst_waits_for_demand_and_sends_in_order(self):
        broker = Broker()
        session = ImportSession(None, Writer(), broker, coalesce_us=1000)
        entered = asyncio.Event()
        async def event_wait(awaitable, timeout):
            self.assertEqual(timeout, .001)
            entered.set()
            return await awaitable
        requests = [bytes([6, 0, n, 0, 0x0f]) for n in range(2, 10)]
        session.commands.put_nowait(requests[0])
        task = asyncio.create_task(session.pump())
        try:
            with patch('rmlink_connector.usbip.asyncio.wait_for', side_effect=event_wait):
                await entered.wait()
                self.assertFalse(broker.called.is_set())
                for request in requests[1:]: session.commands.put_nowait(request)
                session.flush_requested.set()
                await broker.called.wait()
                self.assertEqual(broker.groups, [requests])
        finally:
            await self.stop(task, session)

    async def test_control_and_existing_in_skip_window(self):
        for command, pending_in in ((2, False), (0x11, False), (5, True), (6, True)):
            broker = Broker()
            session = ImportSession(None, Writer(), broker, coalesce_us=1000)
            if pending_in:
                session.in_urbs.append((1, 64))
                session.in_sequences.add(1)
                session.outstanding_dap = 1
            session.commands.put_nowait(bytes([command]))
            task = asyncio.create_task(session.pump())
            try:
                await asyncio.wait_for(broker.called.wait(), .5)
                self.assertEqual(broker.groups, [[bytes([command])]])
            finally:
                await self.stop(task, session)

    async def test_accepted_out_executes_when_no_in_follows(self):
        broker = Broker()
        session = ImportSession(None, Writer(), broker, coalesce_us=1000)
        session.commands.put_nowait(b'\x06\x00\x02\x00\x0f')
        task = asyncio.create_task(session.pump())
        try:
            await asyncio.wait_for(broker.called.wait(), .5)
            self.assertEqual(len(broker.groups), 1)
        finally:
            await self.stop(task, session)

    async def test_full_window_does_not_wait(self):
        broker = Broker()
        session = ImportSession(None, Writer(), broker, coalesce_us=1000)
        packets = [b'\x06\x00\x01\x00\x0f']*8
        for packet in packets: session.commands.put_nowait(packet)
        task = asyncio.create_task(session.pump())
        try:
            await asyncio.wait_for(broker.called.wait(), .5)
            self.assertEqual(broker.groups, [packets])
        finally:
            await self.stop(task, session)


if __name__ == '__main__': unittest.main()
