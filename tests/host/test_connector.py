"""Independent host tests: broker ownership, USB/IP golden bytes and API bounds."""
import asyncio
import json
import struct
import tempfile
import unittest

from rmlink_connector.broker import Broker
from rmlink_connector.descriptors import Descriptors
from rmlink_connector.discovery import dns_name, parse_dns
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from rmlink_connector.usbip import OP, URB, DEVICE, UsbIpServer

ID = '50787d15a924'


class FakeTransport:
    def __init__(self): self.requests = []; self.block = None; self.started = asyncio.Event(); self.closed = False
    async def connect(self): pass
    async def exchange_many(self, packets):
        self.requests.extend(packets)
        result = []
        for p in packets:
            if self.block and p[0] == 5:
                self.started.set(); await self.block.wait()
            if p[:2] == b'\x00\xff': result.append(b'\x00\x02\x40\x00')
            elif p[:2] == b'\x00\xfe': result.append(b'\x00\x01\x08')
            elif p == b'\x80\x09': result.append(b'\x80\x09\x00' + bytes.fromhex(ID))
            elif p[0] == 2: result.append(b'\x02\x01')
            elif p[0] == 3: result.append(b'\x03\x00')
            elif p[0] == 5: result.append(b'\x05\x00\x11')
            elif p[0] == 0x11:
                valid = len(p) >= 5 and 1000 <= int.from_bytes(p[1:5], 'little') <= 10000000
                result.append(b'\x11\x00' if valid else b'\x11\xff')
            else: result.append(b'\xff')
        return result
    async def abort(self): self.block.set()
    async def close(self): self.closed = True


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = FakeTransport(); self.broker = Broker(self.transport, ID); await self.broker.start()
    async def asyncTearDown(self): await self.broker.close()

    async def test_whole_session_foreign_connect_transfer_disconnect_denied(self):
        self.assertEqual(await self.broker.submit('a', [b'\x02\x01']), [b'\x02\x01'])
        before = len(self.transport.requests)
        self.assertEqual(await self.broker.submit('b', [b'\x02\x01', b'\x05\x00\x00', b'\x03']),
                         [b'\x02\x00', b'\x05\x00\x08', b'\x03\xff'])
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(self.broker.owner, 'a')

    async def test_info_free_during_foreign_owner(self):
        await self.broker.submit('a', [b'\x02\x01'])
        self.assertEqual(await self.broker.submit('b', [b'\x00\xff']), [b'\x00\x02\x40\x00'])

    async def test_preconnect_clock_reserves_session_and_denies_foreign_changes(self):
        clock = b'\x11' + (2000000).to_bytes(4, 'little')
        self.assertEqual(await self.broker.submit('a', [clock]), [b'\x11\x00'])
        self.assertEqual(self.broker.owner, 'a')
        before = len(self.transport.requests)
        self.assertEqual(await self.broker.submit('b', [clock, b'\x02\x01', b'\x03']),
                         [b'\x11\xff', b'\x02\x00', b'\x03\xff'])
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(await self.broker.submit('a', [b'\x02\x01', b'\x03']),
                         [b'\x02\x01', b'\x03\x00'])
        self.assertIsNone(self.broker.owner)

    async def test_invalid_preconnect_clock_does_not_claim_session(self):
        packets = [b'\x11', b'\x11' + bytes(4), b'\x11' + (10000001).to_bytes(4, 'little')]
        self.assertEqual(await self.broker.submit('a', packets), [b'\x11\xff'] * 3)
        self.assertIsNone(self.broker.owner)
        self.assertEqual(await self.broker.submit('b', [b'\x02\x01']), [b'\x02\x01'])

    async def test_clock_transition_in_batch_and_release_before_connect(self):
        clock = b'\x11' + (2000000).to_bytes(4, 'little')
        self.assertEqual(await self.broker.submit('a', [b'\x00\xff', clock, b'\x02\x01', b'\x05\x00\x00']),
                         [b'\x00\x02\x40\x00', b'\x11\x00', b'\x02\x01', b'\x05\x00\x11'])
        await self.broker.release('a')
        await self.broker.submit('b', [clock])
        await self.broker.release('b')
        self.assertIsNone(self.broker.owner)
        self.assertEqual(self.transport.requests[-1], b'\x03')

    async def test_abort_bypasses_worker_but_checks_owner(self):
        await self.broker.submit('a', [b'\x02\x01'])
        self.transport.block = asyncio.Event()
        task = asyncio.create_task(self.broker.submit('a', [b'\x05\x00\x00']))
        await self.transport.started.wait()
        self.assertFalse(await self.broker.abort('b')); self.assertFalse(task.done())
        self.assertTrue(await self.broker.abort('a')); await task

    async def test_release_cancels_and_drops_queued_writes(self):
        await self.broker.submit('a', [b'\x02\x01'])
        self.transport.block = asyncio.Event()
        active = asyncio.create_task(self.broker.submit('a', [b'\x05\x00\x00']))
        await self.transport.started.wait()
        queued = asyncio.create_task(self.broker.submit('a', [b'\x05\x00\x00']))
        await asyncio.sleep(0)
        await self.broker.release('a')
        await asyncio.gather(active, queued, return_exceptions=True)
        self.assertEqual(self.transport.requests.count(b'\x05\x00\x00'), 1)
        self.assertIsNone(self.broker.owner)

    async def test_validate_entire_batch_before_sending(self):
        with self.assertRaises(ValueError): await self.broker.submit('a', [b'\x02\x01', bytes(65)])
        with self.assertRaises(ValueError): await self.broker.submit('a', [b'\x07'])
        self.assertEqual(self.transport.requests, [])

    async def test_priority_between_batches(self):
        await self.broker.submit('a', [b'\x02\x01'])
        self.transport.block = asyncio.Event()
        active = asyncio.create_task(self.broker.submit('a', [b'\x05\x00\x00']))
        await self.transport.started.wait()
        slow = asyncio.create_task(self.broker.submit('a', [b'\x00\xfe'], 'background'))
        fast = asyncio.create_task(self.broker.submit('a', [b'\x00\xff'], 'control'))
        await asyncio.sleep(0); await self.broker.abort('a')
        await asyncio.gather(active, slow, fast)
        self.assertEqual(self.transport.requests[-2:], [b'\x00\xff', b'\x00\xfe'])


class DescriptorDnsTests(unittest.TestCase):
    def test_descriptor_lengths_and_multi_sz(self):
        d = Descriptors(ID)
        self.assertEqual(len(d.device), 18); self.assertEqual(len(d.config), 32)
        self.assertEqual(len(d.ms_os), 162); self.assertEqual(len(d.bos), 33)
        self.assertEqual(d.ms_os[-4:], bytes(4)); self.assertIn(b'WINUSB\0\0', d.ms_os)
        self.assertEqual(d.control(struct.pack('<BBHHH', 0x80, 6, 0x100, 0, 8)), d.device[:8])
        self.assertEqual(d.control(struct.pack('<BBHHH', 0xc0, 0x20, 0, 7, 162)), d.ms_os)
        with self.assertRaises(ValueError): d.control(struct.pack('<BBHHH', 0x80, 6, 0x600, 0, 10))

    def test_dns_pointer_cycle_and_truncation(self):
        with self.assertRaises(ValueError): dns_name(b'\xc0\x00', 0)
        with self.assertRaises(ValueError): dns_name(b'\x05abc', 0)
        with self.assertRaises(ValueError): parse_dns(bytes(12))


class UsbIpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = Broker(FakeTransport(), ID); await self.broker.start()
        self.server = UsbIpServer({ID: self.broker}); self.port = await self.server.start(0)
        self.clients = []
    async def asyncTearDown(self):
        for writer in self.clients: writer.close(); await writer.wait_closed()
        await self.server.close(); await asyncio.sleep(.02); await self.broker.close()
    async def connect(self, import_device=True):
        r, w = await asyncio.open_connection('127.0.0.1', self.port); self.clients.append(w)
        if import_device:
            w.write(OP.pack(0x111, 0x8003, 0) + ('rmlink-' + ID).encode().ljust(32, b'\0')); await w.drain()
            self.assertEqual(await r.readexactly(8), OP.pack(0x111, 3, 0))
            self.assertEqual(len(await r.readexactly(312)), DEVICE.size)
        return r, w
    async def submit(self, w, seq, direction, ep, length, setup=bytes(8), data=b''):
        w.write(URB.pack(1, seq, 0x10001, direction, ep, 0, length, 0, 0, 0, setup) + data); await w.drain()
    async def response(self, r, payload=False):
        h = URB.unpack(await asyncio.wait_for(r.readexactly(48), 2))
        return h, await r.readexactly(h[6]) if payload and h[5] == 0 else b''

    async def test_devlist_golden_layout(self):
        r, w = await self.connect(False); w.write(OP.pack(0x111, 0x8005, 0)); await w.drain()
        self.assertEqual(await r.readexactly(12), OP.pack(0x111, 5, 0) + b'\x00\x00\x00\x01')
        device = DEVICE.unpack(await r.readexactly(312))
        self.assertEqual(device[1].rstrip(b'\0'), b'rmlink-' + ID.encode())
        self.assertEqual(await r.readexactly(4), b'\xff\x00\x00\x00')

    async def test_control_and_bulk_in_before_out(self):
        r, w = await self.connect()
        await self.submit(w, 1, 1, 0, 18, struct.pack('<BBHHH', 0x80, 6, 0x100, 0, 18))
        h, data = await self.response(r, True); self.assertEqual(h[6], 18); self.assertEqual(data[0:2], b'\x12\x01')
        await self.submit(w, 2, 1, 3, 64)
        await self.submit(w, 3, 0, 3, 64, data=b'\x00\xff' + bytes(62))
        h, _ = await self.response(r); self.assertEqual(h[1], 3); self.assertEqual(h[6], 64)
        h, data = await self.response(r, True); self.assertEqual(h[1], 2); self.assertEqual(data, b'\x00\x02\x40\x00')

    async def test_unlink_pending_in_no_late_completion(self):
        r, w = await self.connect()
        await self.submit(w, 1, 1, 3, 64)
        w.write(URB.pack(2, 2, 0x10001, 1, 3, 1, 0, 0, 0, 0, bytes(8))); await w.drain()
        h, _ = await self.response(r); self.assertEqual(h[0], 4); self.assertEqual(h[5], -104)
        await self.submit(w, 3, 0, 3, 2, data=b'\x00\xff')
        h, _ = await self.response(r); self.assertEqual(h[1], 3)
        await self.submit(w, 4, 1, 3, 64)
        h, data = await self.response(r, True); self.assertEqual(h[1], 4); self.assertEqual(data, b'\x00\x02\x40\x00')

    async def test_winusb_rounded_read_and_non_iso_marker(self):
        r, w = await self.connect()
        # Captured usbip-win2/WinUSB behavior: non-ISO -1 and 512-byte IN URB.
        w.write(URB.pack(1, 1, 0x10001, 1, 3, 0, 512, 0, -1, 0, bytes(8)))
        w.write(URB.pack(1, 2, 0x10001, 0, 3, 0, 64, 0, -1, 0, bytes(8)) + b'\x00\xff' + bytes(62))
        await w.drain()
        h, _ = await self.response(r); self.assertEqual(h[1], 2); self.assertEqual(h[6], 64)
        h, data = await self.response(r, True)
        self.assertEqual(h[1], 1); self.assertEqual(h[6], 4)
        self.assertEqual(data, b'\x00\x02\x40\x00')

    async def test_second_import_is_busy(self):
        await self.connect()
        r, w = await self.connect(False)
        w.write(OP.pack(0x111, 0x8003, 0) + ('rmlink-' + ID).encode().ljust(32, b'\0')); await w.drain()
        self.assertEqual(await r.readexactly(8), OP.pack(0x111, 3, 2))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connector = Connector(State(self.temp.name))
        self.port = await self.connector.start(0, 0)
        self.connector.background.cancel(); await asyncio.gather(self.connector.background, return_exceptions=True)
    async def asyncTearDown(self): await self.connector.close(); self.temp.cleanup()
    async def send(self, headers=''):
        r, w = await asyncio.open_connection('127.0.0.1', self.port)
        w.write(('GET /v1/status HTTP/1.1\r\nHost: localhost\r\n' + headers + '\r\n').encode()); await w.drain()
        data = await r.read(); w.close(); await w.wait_closed(); return data
    async def test_auth_required(self): self.assertIn(b'HTTP/1.1 401', await self.send())
    async def test_authenticated_status(self):
        data = await self.send('Authorization: Bearer ' + self.connector.state.data['token'] + '\r\n')
        self.assertIn(b'HTTP/1.1 200', data); self.assertNotIn(self.connector.state.data['token'].encode(), data)
    async def test_browser_origin_rejected_even_with_token(self):
        data = await self.send('Origin: http://localhost\r\nAuthorization: Bearer ' + self.connector.state.data['token'] + '\r\n')
        self.assertIn(b'HTTP/1.1 403', data)


if __name__ == '__main__': unittest.main()
