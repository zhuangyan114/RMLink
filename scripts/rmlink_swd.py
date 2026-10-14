"""CMSIS-DAP SWD helpers. The CLI remains a read-only identity check.

Explicit write helpers are used only by the separately authorized acceptance
runner. No automatic retry/reset after an uncertain write is performed.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from rmlink_tool import TcpClient, status


class SwdSession:
    def __init__(self, client, clock=100000):
        self.client = client
        self.clock = clock
        self.connected = False

    def command(self, request, expected):
        reply = self.client.exchange(request)
        if reply != expected:
            raise RuntimeError(f'DAP command {request[0]:#04x}: {reply.hex()}, expected {expected.hex()}')

    def transfer(self, operations):
        request = bytearray([5, 0, len(operations)])
        reads = 0
        for flags, value in operations:
            request.append(flags)
            if flags & 2:
                reads += 1
            else:
                request.extend(struct.pack('<I', value))
        if len(request) > 64 or 3 + 4 * reads > 64:
            raise ValueError('Transfer exceeds 64-byte DAP packet')
        reply = self.client.exchange(request)
        if len(reply) < 3 or reply[0] != 5 or reply[1] != len(operations) or reply[2] != 1:
            raise RuntimeError(f'SWD transfer failed: {reply.hex()} (ACK 1=OK, 2=WAIT, 4=FAULT, 7=NO_ACK, 8=protocol error)')
        if len(reply) != 3 + reads * 4:
            raise RuntimeError('SWD transfer returned unexpected data length')
        return list(struct.unpack('<' + 'I' * reads, reply[3:]))

    def dp_read(self, address):
        return self.transfer([(2 | (address & 12), None)])[0]

    def dp_write(self, address, value):
        self.transfer([(address & 12, value)])

    def start(self):
        self.command(b'\x02\x01', b'\x02\x01')
        self.connected = True
        try:
            self.command(b'\x11' + struct.pack('<I', self.clock), b'\x11\x00')
            self.command(b'\x04\x02\x64\x00\x00\x00', b'\x04\x00')
            self.command(b'\x13\x00', b'\x13\x00')
            # At least 50 high clocks, JTAG-to-SWD 0xE79E LSB first, line reset + idle.
            for data, bits in ((b'\xff' * 7, 56), (b'\x9e\xe7', 16),
                               (b'\xff' * 7, 56), (b'\x00', 8)):
                self.command(bytes([0x12, bits]) + data, b'\x12\x00')
            self.idcode = self.dp_read(0)
            self.dp_write(0, 0x1e)  # Clear sticky error flags; not a target reset.
            self.dp_write(8, 0)
            self.dp_write(4, 0x50000000)  # Debug/system power requests.
            deadline = time.perf_counter() + 2
            while True:
                self.ctrl_stat = self.dp_read(4)
                if self.ctrl_stat & 0xa0000000 == 0xa0000000:
                    break
                if time.perf_counter() >= deadline:
                    raise RuntimeError(f'Debug power ACK timed out: {self.ctrl_stat:#010x}')
                time.sleep(0.01)
            self.dp_write(8, 0xf0)
            self.ap_idr = self.transfer([(0xf, None)])[0]
            self.dp_write(8, 0)
            self.transfer([(1, 0x23000052)])  # AHB-AP, 32-bit, single increment.
            return self
        except BaseException:
            self.close()
            raise

    def read32(self, address):
        if address & 3:
            raise ValueError('32-bit memory address must be aligned')
        return self.transfer([(5, address), (0xf, None)])[0]

    def write32(self, address, value):
        if address & 3:
            raise ValueError('32-bit memory address must be aligned')
        self.transfer([(5, address), (0xd, value)])

    def write_words(self, address, words, pipelined=False):
        if address & 3:
            raise ValueError('32-bit memory address must be aligned')
        words = list(words)
        def request(offset, size):
            return bytes([6, 0]) + struct.pack('<H', size) + b'\x0d' + struct.pack(
                '<' + 'I' * size, *words[offset:offset + size])
        for requests, sizes in self._memory_batches(address, len(words), 14, pipelined, request):
            replies = self.client.exchange_many(requests) if pipelined else [self.client.exchange(r) for r in requests]
            if len(replies) != len(requests):
                raise RuntimeError('SWD write batch returned an unexpected response count')
            for size, reply in zip(sizes, replies):
                expected = b'\x05\x01\x01' if size is None else bytes([6, size, 0, 1])
                if reply != expected:
                    raise RuntimeError(f'SWD block write failed: {reply.hex()}')

    def read_words(self, address, count, pipelined=False):
        if address & 3 or count < 0:
            raise ValueError('Invalid memory read')
        words = []
        def request(offset, size):
            return bytes([6, 0]) + struct.pack('<H', size) + b'\x0f'
        for requests, sizes in self._memory_batches(address, count, 15, pipelined, request):
            if pipelined:
                replies = self.client.exchange_many(requests)
            else:
                replies = [self.client.exchange(request) for request in requests]
            if len(replies) != len(requests):
                raise RuntimeError('SWD read batch returned an unexpected response count')
            for size, reply in zip(sizes, replies):
                if size is None:
                    if reply != b'\x05\x01\x01':
                        raise RuntimeError(f'SWD TAR setup failed: {reply.hex()}')
                    continue
                if len(reply) != 4 + size * 4 or reply[:4] != bytes([6, size, 0, 1]):
                    raise RuntimeError(f'SWD TransferBlock failed: {reply.hex()}')
                words.extend(struct.unpack('<' + 'I' * size, reply[4:]))
        return words

    @staticmethod
    def _memory_batches(address, count, block_words, pipelined, block_request):
        # Only this call owns the planned TAR state. Reinitialize each new call;
        # the MEM-AP wraps its lower 10 bits at a Cortex-M3 1 KiB boundary.
        offset, tar_needed = 0, True
        limit = 8 if pipelined else 1
        while offset < count:
            requests, sizes = [], []
            while offset < count and len(requests) < limit:
                if tar_needed:
                    requests.append(b'\x05\x00\x01\x05' + struct.pack('<I', address))
                    sizes.append(None)
                    tar_needed = False
                    if len(requests) == limit:
                        break
                size = min(count - offset, block_words, (0x400 - (address & 0x3ff)) // 4)
                requests.append(block_request(offset, size))
                sizes.append(size)
                address += size * 4
                offset += size
                tar_needed = not (address & 0x3ff)
            yield requests, sizes

    def close(self):
        if self.connected:
            try:
                self.command(b'\x03', b'\x03\x00')
            finally:
                self.connected = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()


def read_target(client, clock):
    before = status(client)
    if not before['targetPinsConfirmed']:
        raise RuntimeError('Target GPIO profile is disabled')
    with SwdSession(client, clock) as swd:
        cpuid = swd.read32(0xe000ed00)
        dbg_id = swd.read32(0xe0042000)
        flash_kib = swd.read32(0x1ffff7e0) & 0xffff
        uid = swd.read_words(0x1ffff7e8, 3)
        vectors = swd.read_words(0x08000000, 8)
        dhcsr = swd.read32(0xe000edf0)
        pins = client.exchange(b'\x10\x00\x00\x00\x00\x00\x00')
        if len(pins) != 2 or pins[0] != 0x10:
            raise RuntimeError('SWJ pin read failed')
        begin = time.perf_counter()
        for _ in range(100):
            if swd.read32(0xe000ed00) != cpuid:
                raise RuntimeError('CPUID changed during repeated target reads')
        elapsed = (time.perf_counter() - begin) * 1000
        report = dict(device=before, swdClockRequestedHz=clock,
                      dpIdcode=f'0x{swd.idcode:08X}', dpCtrlStat=f'0x{swd.ctrl_stat:08X}',
                      ahbApIdr=f'0x{swd.ap_idr:08X}', cpuid=f'0x{cpuid:08X}',
                      dbgMcuIdcode=f'0x{dbg_id:08X}', deviceId=f'0x{dbg_id & 0xfff:03X}',
                      flashSizeRegisterKiB=flash_kib, uniqueIdWords=[f'0x{word:08X}' for word in uid],
                      flashVectorWords=[f'0x{word:08X}' for word in vectors],
                      dhcsr=f'0x{dhcsr:08X}', coreHaltedObserved=bool(dhcsr & (1 << 17)),
                      nrstHighObserved=bool(pins[1] & 0x80),
                      repeatedCpuidReads=100, repeatedReadElapsedMs=round(elapsed, 2),
                      ownerDuringRead=status(client))
        if (cpuid >> 4) & 0xfff != 0xc23 or dbg_id & 0xfff != 0x410:
            raise RuntimeError(f'Target identity differs from expected Cortex-M3 / medium-density F1: {report}')
    report['ownerAfterDisconnect'] = status(client)
    report['evidence'] = 'Physical target DP/AP and read-only identity/memory; no target halt/reset/RAM write/Flash programming; requested SWD clock not instrument-measured'
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', help='Omit for USB')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--clock', type=int, default=100000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.host:
        client = TcpClient(args.host, args.port)
    else:
        from rmlink_usb import UsbClient
        client = UsbClient()
    with client:
        report = read_target(client, args.clock)
    report['capturedAt'] = datetime.now().astimezone().isoformat()
    report['transport'] = 'TCP' if args.host else 'USB'
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
