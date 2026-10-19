"""Independent host tests: broker ownership, DNS parsing and API bounds."""
import asyncio
import json
import tempfile
import unittest

from rmlink_connector.broker import Broker
from rmlink_connector.discovery import dns_name, parse_dns
from rmlink_connector.service import Connector
from rmlink_connector.state import State

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
            if p[:2] == b'\x00\xff': result.append(b'\x00\x02' + getattr(self, 'packet_size', 64).to_bytes(2, 'little'))
            elif p[:2] == b'\x80\x07': result.append(bytes(range(256)) * (getattr(self, 'packet_size', 64)//256))
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

    async def test_trim_only_after_capacity_check_and_keep_write_values(self):
        self.transport.packet_size = 512
        packet = b'\x05\x00\x01\x0d' + bytes(4)
        await self.broker.submit('a', [b'\x02\x01'])
        await self.broker.submit('a', [packet + bytes(504)])
        self.assertEqual(self.transport.requests[-1], packet)
        before = len(self.transport.requests)
        with self.assertRaises(ValueError): await self.broker.submit('a', [b'\x00\xff' + bytes(511)])
        self.assertEqual(len(self.transport.requests), before)

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


class DnsTests(unittest.TestCase):

    def test_dns_pointer_cycle_and_truncation(self):
        with self.assertRaises(ValueError): dns_name(b'\xc0\x00', 0)
        with self.assertRaises(ValueError): dns_name(b'\x05abc', 0)
        with self.assertRaises(ValueError): parse_dns(bytes(12))




class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_reports_device_connections_without_virtual_usb_metadata(self):
        status = await self.connector.route('GET', '/v1/status', {})
        self.assertEqual(status['devices'], [])
        for key in ('driver', 'usbip_port', 'mount_errors'):
            self.assertNotIn(key, status)

    async def test_retired_mount_routes_cannot_claim_a_target_or_send_commands(self):
        transport = FakeTransport()
        broker = Broker(transport, ID)
        await broker.start()
        self.connector.brokers[ID] = broker
        before = list(transport.requests)
        for path in ('/v1/mount', '/v1/unmount'):
            with self.assertRaisesRegex(ValueError, 'Unsupported local API route'):
                await self.connector.route('POST', path, {'device_id': ID})
        self.assertIsNone(broker.owner)
        self.assertEqual(transport.requests, before)

    async def test_full_eight_packet_512_batch_fits_http_body_limit(self):
        transport = FakeTransport(); transport.packet_size = 512
        broker = Broker(transport, ID); await broker.start()
        self.connector.brokers[ID] = broker
        opened = await self.connector.route('POST', '/v1/sessions', {'device_id': ID})
        self.assertEqual(opened['packet_size'], 512)
        body = json.dumps({'packets': [(b'\x00\xff' + bytes(510)).hex()]*8}).encode()
        self.assertGreater(len(body), 8192)
        r, w = await asyncio.open_connection('127.0.0.1', self.port)
        headers = (f"POST /v1/sessions/{opened['session_id']}/dap HTTP/1.1\r\nHost: localhost\r\n"
                   f"Authorization: Bearer {self.connector.state.data['token']}\r\nContent-Length: {len(body)}\r\n\r\n").encode()
        w.write(headers + body); await w.drain()
        reply = await r.read(); w.close(); await w.wait_closed()
        self.assertIn(b'HTTP/1.1 200', reply)
        self.assertEqual(json.loads(reply.split(b'\r\n\r\n', 1)[1])['responses'], ['00020002']*8)
        before = len(transport.requests)
        with self.assertRaises(ValueError):
            await self.connector.route('POST', f"/v1/sessions/{opened['session_id']}/dap", {'packets': ['00'*513]})
        self.assertEqual(len(transport.requests), before)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connector = Connector(State(self.temp.name))
        self.port = await self.connector.start(0)
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
