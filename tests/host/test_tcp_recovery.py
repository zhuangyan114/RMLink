"""Short outages retain the TCP owner and never replay an outstanding request."""
import asyncio
import struct
import unittest
from unittest.mock import patch

from rmlink_connector.broker import Broker
from rmlink_connector.transport import TcpTransport


class TcpRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.peers = set()
        self.pause_at = None
        self.pause_seconds = .5
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0)
        port = self.server.sockets[0].getsockname()[1]
        self.broker = Broker(TcpTransport('127.0.0.1', port), 'test-probe')
        await self.broker.start()
        await self.broker.submit('owner', [b'\x02\x01'])

    async def asyncTearDown(self):
        await self.broker.close()
        self.server.close()
        await self.server.wait_closed()
        await asyncio.gather(*self.peers)

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.peers.add(task)
        try:
            while True:
                header = await reader.readexactly(8)
                size = struct.unpack('<4sHBB', header)[1]
                packet = await reader.readexactly(size)
                self.requests.append(packet)
                reply = b'\x00\x02\x40\x00' if packet == b'\x00\xff' else packet
                frame = struct.pack('<4sHBB', b'DAP\0', len(reply), 2, 0) + reply
                if self.pause_at is not None and packet[0] == 5:
                    writer.write(frame[:self.pause_at])
                    await writer.drain()
                    await asyncio.sleep(self.pause_seconds)
                    writer.write(frame[self.pause_at:])
                else:
                    writer.write(frame)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.peers.discard(task)

    async def check_recovery(self, pause_at):
        self.pause_at = pause_at
        request = b'\x05\x00\x01\x0d\x12\x34\x56\x78'
        self.assertEqual(await self.broker.submit('owner', [request]), [request])
        self.assertIsNone(self.broker.failed)
        self.assertEqual(self.broker.owner, 'owner')
        self.assertEqual(self.requests.count(request), 1)
        self.assertEqual(await self.broker.submit('owner', [b'\x00\xfe']), [b'\x00\xfe'])

    async def test_half_second_silence_before_response(self):
        await self.check_recovery(0)

    async def test_half_second_outage_in_partial_header(self):
        await self.check_recovery(3)

    async def test_half_second_outage_in_partial_payload(self):
        await self.check_recovery(10)

    async def test_expired_response_invalidates_owner_without_write_replay(self):
        self.pause_at = 10
        request = b'\x05\x00\x01\x0d\x12\x34\x56\x78'
        with patch('rmlink_connector.transport.TCP_IO_TIMEOUT', .05):
            with self.assertRaises(ConnectionError):
                await self.broker.submit('owner', [request])
        self.assertIsNotNone(self.broker.failed)
        self.assertIsNone(self.broker.owner)
        self.assertEqual(self.requests.count(request), 1)
