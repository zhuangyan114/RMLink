"""MEM-AP model wraps TAR at 1 KiB, independent of the host's chunk planner."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from rmlink_swd import SwdSession


class MemAp:
    def __init__(self, packet_size=64):
        self.packet_size = packet_size
        self.tar = 0
        self.memory = {}
        self.requests = []
        self.batches = []
        self.drop_reply = False
        self.fault_next_block = False
        self.partial_words = None
        self.tar_writes = []

    @staticmethod
    def initial_word(address):
        return (address ^ 0xa5c3195d) & 0xffffffff

    def _execute(self, packet):
        self.requests.append(packet)
        if packet[0] == 5:
            position, completed, values, data_words = 3, 0, [], 0
            for _ in range(packet[2]):
                flag = packet[position]; position += 1
                if flag == 5:
                    self.tar = int.from_bytes(packet[position:position + 4], 'little'); position += 4
                    self.tar_writes.append(self.tar)
                elif flag in (0x0d, 0x0f):
                    if self.fault_next_block:
                        self.fault_next_block = False
                        return bytes([5, completed, 4])
                    if self.partial_words is not None and data_words == self.partial_words:
                        self.partial_words = None
                        return bytes([5, completed, 1])  # Interrupted after actual completed operations.
                    if flag == 0x0f:
                        values.append(self.memory.get(self.tar, self.initial_word(self.tar)))
                    else:
                        self.memory[self.tar] = int.from_bytes(packet[position:position + 4], 'little'); position += 4
                    self.tar = (self.tar & ~0x3ff) | ((self.tar + 4) & 0x3ff)
                    data_words += 1
                else:
                    raise AssertionError('Unexpected Transfer operation')
                completed += 1
            return bytes([5, completed, 1]) + struct.pack('<' + 'I'*len(values), *values)
        if packet[0] != 6:
            raise AssertionError('Unexpected model command')
        count = int.from_bytes(packet[2:4], 'little')
        if self.fault_next_block:
            self.fault_next_block = False
            return b'\x06\x00\x00\x04'
        values = []
        for i in range(count):
            if packet[4] == 0x0f:
                values.append(self.memory.get(self.tar, self.initial_word(self.tar)))
            elif packet[4] == 0x0d:
                self.memory[self.tar] = int.from_bytes(packet[5 + 4*i:9 + 4*i], 'little')
            else:
                raise AssertionError('Unexpected model transfer flags')
            self.tar = (self.tar & ~0x3ff) | ((self.tar + 4) & 0x3ff)
        data = struct.pack('<' + 'I'*len(values), *values)
        return bytes([6]) + count.to_bytes(2, 'little') + b'\x01' + data

    def exchange(self, packet):
        self.batches.append([packet])
        return self._execute(packet)

    def exchange_many(self, packets):
        self.batches.append(list(packets))
        replies = [self._execute(p) for p in packets]
        return replies[:-1] if self.drop_reply else replies


class MemoryTests(unittest.TestCase):
    def test_large_packets_cover_wrap_and_partial_completion(self):
        for capacity in (128, 256, 512):
            for pipeline in (False, True):
                with self.subTest(capacity=capacity, pipeline=pipeline):
                    client = MemAp(capacity)
                    swd = SwdSession(client)
                    address = 0x200003e8
                    words = [0xab000000 + i for i in range(1025)]
                    swd.write_words(address, words, pipeline)
                    self.assertEqual(swd.read_words(address, len(words), pipeline), words)
                    self.assertLessEqual(max(map(len, client.requests)), capacity)
                    self.assertLessEqual(max(map(len, client.batches)), 8 if pipeline else 1)
            client = MemAp(capacity)
            client.partial_words = 3
            address, words = 0x20000000, [0x10000000 + i for i in range(2048)]
            with self.assertRaises(RuntimeError): SwdSession(client).write_words(address, words, True)
            self.assertEqual(len(client.batches), 1)
            for position, value in client.memory.items():
                self.assertEqual(value, words[(position - address)//4])
            self.assertTrue(all(address + i*4 not in client.memory for i in range(3, (capacity - 8)//5)))

    def test_read_crosses_wrap_with_correct_values_and_fewer_tar_writes(self):
        for pipelined in (False, True):
            with self.subTest(pipelined=pipelined):
                client = MemAp()
                address, count = 0x080003f0, 600
                actual = SwdSession(client).read_words(address, count, pipelined)
                self.assertEqual(actual, [client.initial_word(address + 4*i) for i in range(count)])
                if not pipelined:
                    self.assertEqual(client.tar_writes, [address, 0x08000400, 0x08000800, 0x08000c00])
                else:
                    self.assertEqual(len(client.tar_writes), len(client.requests))
                self.assertLessEqual(max(map(len, client.batches)), 8 if pipelined else 1)

    def test_full_64k_read_removes_separate_address_packets(self):
        client = MemAp()
        words = SwdSession(client).read_words(0x08000000, 16384, True)
        self.assertEqual(len(words), 16384)
        self.assertEqual(words, [client.initial_word(0x08000000 + 4*i) for i in range(16384)])
        self.assertEqual(len(client.tar_writes), 1152)
        self.assertEqual(len(client.requests), 1152)

    def test_writes_and_readback_cross_multiple_boundaries(self):
        for pipelined in (False, True):
            with self.subTest(pipelined=pipelined):
                client = MemAp()
                swd = SwdSession(client)
                address = 0x200003e8
                values = [((i * 0x13579) ^ 0xface1234) & 0xffffffff for i in range(769)]
                swd.write_words(address, values, pipelined)
                self.assertEqual([client.memory[address + 4*i] for i in range(len(values))], values)
                self.assertEqual(swd.read_words(address, len(values), pipelined), values)

    def test_every_new_call_reinitializes_address(self):
        client = MemAp()
        swd = SwdSession(client)
        for address in (0x08000004, 0x20000400, 0x08000004):
            self.assertEqual(swd.read_words(address, 1, True), [client.initial_word(address)])
        self.assertEqual(sum(p[0] == 5 for p in client.requests), 3)

    def test_invalid_and_empty_calls_send_nothing(self):
        client = MemAp()
        swd = SwdSession(client)
        self.assertEqual(swd.read_words(0x08000000, 0, True), [])
        swd.write_words(0x20000000, [], True)
        for address, count in ((0x08000001, 1), (0x08000000, -1)):
            with self.assertRaises(ValueError): swd.read_words(address, count, True)
        self.assertEqual(client.requests, [])

    def test_fault_and_missing_responses_fail_without_replay(self):
        for mode in ('fault', 'missing'):
            with self.subTest(mode=mode):
                client = MemAp()
                client.fault_next_block = mode == 'fault'
                client.drop_reply = mode == 'missing'
                with self.assertRaises(RuntimeError):
                    SwdSession(client).read_words(0x08000000, 1000, True)
                self.assertEqual(len(client.batches), 1)
                self.assertLessEqual(len(client.requests), 8)

    def test_partial_pipeline_write_cannot_shift_later_packet_addresses(self):
        client = MemAp()
        client.partial_words = 3
        address = 0x20000000
        words = [0x10000000 + i for i in range(200)]
        with self.assertRaises(RuntimeError):
            SwdSession(client).write_words(address, words, True)
        self.assertEqual(len(client.batches), 1)
        self.assertEqual(len(client.requests), 8)
        self.assertTrue(all(address <= p < address + 4*len(words) for p in client.memory))
        for position, value in client.memory.items():
            self.assertEqual(value, words[(position - address)//4])
        self.assertTrue(all(address + i*4 not in client.memory for i in range(3, 11)))


if __name__ == '__main__': unittest.main()
