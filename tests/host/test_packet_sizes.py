"""Independent fragmented TCP peer and negotiated capacity rejection."""
import asyncio
import struct
import unittest

from rmlink_protocol import packet_size_from_info, without_usb_padding
from rmlink_connector.transport import TcpTransport
from rmlink_connector.discovery import verify_probe
from rmlink_tool import TcpClient


class PacketSizeTests(unittest.TestCase):
    def test_padding_decoder_keeps_zero_values_and_match_parameters(self):
        packets = [b'\x00\xff', b'\x11\x80\x84\x1e\x00',
                   b'\x05\x00\x03\x05\x00\x00\x00\x20\x0d' + bytes(4) + b'\x0f',
                   b'\x05\x00\x01\x12' + bytes(4),
                   b'\x06\x00\x02\x00\x0d' + bytes(8), b'\x06\x00\x7f\x00\x0f',
                   b'\x12\x10' + bytes(2), b'\x1d\x02\x10' + bytes(2) + b'\x88',
                   b'\x80\x07' + bytes(4)]
        for packet in packets:
            for capacity in (64,128,256,512):
                self.assertEqual(without_usb_padding(packet.ljust(capacity, b'\x00')), packet)
    def test_unknown_nonzero_trailing_or_incomplete_packets_stay_unchanged(self):
        for packet in (b'', b'\x80\x7f' + bytes(62), b'\x7f' + bytes(63),
                       b'\x00\xff\x01' + bytes(61), b'\x05\x00\x01\x05',
                       b'\x06\x00\xff\xff\x0d' + bytes(59),
                       b'\x05\x00\x01\xc2' + bytes(60)):
            self.assertEqual(without_usb_padding(packet), packet)

    def test_info_requires_exact_known_capacity(self):
        for size in (64, 128, 256, 512):
            self.assertEqual(packet_size_from_info(b'\x00\x02' + size.to_bytes(2, 'little')), size)
        for packet in (b'', b'\x00\x01\x40', b'\x00\x02\x40\x00\x00',
                       b'\xff\x02\x40\x00', b'\x00\x02\x00\x00', b'\x00\x02\x00\x04'):
            with self.assertRaises(ValueError): packet_size_from_info(packet)


class NegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_accepts_status_matching_each_negotiated_capacity(self):
        for capacity in (64, 128, 256, 512):
            async def peer(reader, writer):
                try:
                    for _ in range(3):
                        header = await reader.readexactly(8)
                        packet = await reader.readexactly(int.from_bytes(header[4:6], 'little'))
                        if packet == b'\x00\xff': response = b'\x00\x02' + capacity.to_bytes(2, 'little')
                        elif packet == b'\x80\x00': response = b'\x80\x00\x00\x01\x00\x01\x06' + bytes(8) + capacity.to_bytes(2, 'little')
                        else: response = b'\x80\x09\x00' + bytes.fromhex('50787d15a924')
                        writer.write(b'DAP\0' + len(response).to_bytes(2, 'little') + b'\x02\x00' + response); await writer.drain()
                finally: writer.close(); await writer.wait_closed()
            async with await asyncio.start_server(peer, '127.0.0.1', 0) as server:
                result = await verify_probe('rmlink-test.local', '127.0.0.1', server.sockets[0].getsockname()[1])
                self.assertEqual((result.device_id, result.firmware), ('50787d15a924', '0.1.6'))

    async def test_sync_and_async_clients_negotiate_then_read_fragmented_full_packet(self):
        for capacity in (64, 128, 256, 512):
            requests, failures = [], []
            async def peer(reader, writer):
                try:
                    for _ in range(2):
                        header = await reader.readexactly(8)
                        self.assertEqual(header[:4], b'DAP\0'); self.assertEqual(header[6:], b'\x01\x00')
                        length = int.from_bytes(header[4:6], 'little')
                        request = await reader.readexactly(length); requests.append(request)
                        if request == b'\x00\xff': response = b'\x00\x02' + capacity.to_bytes(2, 'little')
                        else:
                            self.assertEqual(request, b'\x80\x07' + bytes(capacity - 2))
                            response = bytes(i % 256 for i in range(capacity))
                        frame = b'DAP\0' + len(response).to_bytes(2, 'little') + b'\x02\x00' + response
                        for offset in range(0, len(frame), 3):
                            writer.write(frame[offset:offset + 3]); await writer.drain(); await asyncio.sleep(0)
                except BaseException as error: failures.append(error)
                finally: writer.close(); await writer.wait_closed()
            async with await asyncio.start_server(peer, '127.0.0.1', 0) as server:
                port = server.sockets[0].getsockname()[1]
                def sync_check():
                    with TcpClient('127.0.0.1', port) as client:
                        self.assertEqual(client.packet_size, capacity)
                        with self.assertRaises(ValueError): client.exchange_many([b'\x00\xff', bytes(capacity + 1)])
                        return client.exchange(b'\x80\x07' + bytes(capacity - 2))
                self.assertEqual(await asyncio.wait_for(asyncio.to_thread(sync_check), 4), bytes(i % 256 for i in range(capacity)))
                client = TcpTransport('127.0.0.1', port)
                try:
                    await client.connect(); self.assertEqual(client.packet_size, capacity)
                    with self.assertRaises(ValueError): await client.exchange_many([b'\x00\xff', bytes(capacity + 1)])
                    response = (await client.exchange_many([b'\x80\x07' + bytes(capacity - 2)]))[0]
                    self.assertEqual(response, bytes(i % 256 for i in range(capacity)))
                finally: await client.close()
            self.assertEqual(len(requests), 4); self.assertEqual(failures, [])


if __name__ == '__main__': unittest.main()
