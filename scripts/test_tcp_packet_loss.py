"""Exercise the real TCP transport with Linux netem in a private network namespace.

Run under WSL as root. The peer models a probe and records each write operation.
The kernel drops IP packets in both directions on the isolated loopback device.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import suppress
from datetime import datetime
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.broker import Broker
from rmlink_connector.transport import TcpTransport


def netem(loss, duplicate=0, reorder=0):
    command = ['tc', 'qdisc', 'replace', 'dev', 'lo', 'root', 'netem',
               'limit', '10000', 'delay', '5ms', 'loss', f'{loss}%']
    if duplicate:
        command.extend(['duplicate', f'{duplicate}%'])
    if reorder:
        command.extend(['reorder', f'{reorder}%'])
    subprocess.run(command, check=True, capture_output=True, text=True)


def network_stats():
    data = subprocess.check_output(['tc', '-s', '-j', 'qdisc', 'show', 'dev', 'lo'], text=True)
    return json.loads(data)[0]


def large_reply(seed):
    return b'\x80\x07\x00' + seed.to_bytes(4, 'little') + bytes((seed + i) % 256 for i in range(505))


class ProbeModel:
    def __init__(self):
        self.writes = []
        self.peers = set()

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.peers.add(task)
        try:
            while True:
                header = await reader.readexactly(8)
                magic, size, kind, reserved = struct.unpack('<4sHBB', header)
                assert magic == b'DAP\0' and kind == 1 and reserved == 0
                packet = await reader.readexactly(size)
                if packet == b'\x00\xff':
                    reply = b'\x00\x02\x00\x02'
                elif packet == b'\x02\x01':
                    reply = b'\x02\x01'
                elif packet == b'\x03':
                    reply = b'\x03\x00'
                elif packet[:4] == b'\x05\x00\x01\x0d':
                    assert len(packet) == 8
                    self.writes.append(int.from_bytes(packet[4:], 'little'))
                    reply = b'\x05\x01\x01'
                elif packet[:2] == b'\x80\x07':
                    reply = large_reply(int.from_bytes(packet[2:6], 'little'))
                else:
                    raise AssertionError(f'Unexpected request {packet.hex()}')
                writer.write(struct.pack('<4sHBB', b'DAP\0', len(reply), 2, 0) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.peers.discard(task)


async def run_case(loss=0, blackout_ms=0, permanent=False, duplicate=0, reorder=0):
    model = ProbeModel()
    netem(0)
    server = await asyncio.start_server(model.serve, '127.0.0.1', 0)
    broker = Broker(TcpTransport('127.0.0.1', server.sockets[0].getsockname()[1]), 'netem-model')
    restore = None
    row = dict(loss_percent=loss, blackout_ms=blackout_ms, permanent=permanent,
               duplicate_percent=duplicate, reorder_percent=reorder)
    try:
        await broker.start()
        await broker.submit('owner', [b'\x02\x01'])
        netem(100 if blackout_ms or permanent else loss, duplicate, reorder)
        network_blocked_at = time.monotonic()
        baseline = network_stats()
        if blackout_ms:
            async def restore_link():
                await asyncio.sleep(blackout_ms / 1000)
                subprocess.run(['tc', 'qdisc', 'change', 'dev', 'lo', 'root', 'netem',
                                'delay', '5ms', 'loss', '0%'], check=True, capture_output=True)
                row['blackout_applied_ms'] = round((time.monotonic() - network_blocked_at) * 1000, 3)
            restore = asyncio.create_task(restore_link())
        started = time.monotonic()
        expected = []
        if permanent:
            try:
                await broker.submit('owner', [b'\x05\x00\x01\x0d' + (123).to_bytes(4, 'little')])
            except ConnectionError:
                assert broker.failed and broker.owner is None
                assert not model.writes
                row['result'] = 'bounded_failure'
            else:
                raise AssertionError('A permanently lost request completed')
        else:
            for batch in range(64):
                ids = list(range(batch * 8, batch * 8 + 8))
                packets = [b'\x05\x00\x01\x0d' + value.to_bytes(4, 'little') for value in ids]
                assert await broker.submit('owner', packets) == [b'\x05\x01\x01'] * 8
                if batch == 0:
                    row['first_batch_ms'] = round((time.monotonic() - started) * 1000, 3)
                expected.extend(ids)
                if batch >= 15 and (not loss or network_stats()['drops'] > baseline['drops']):
                    break
            seeds = list(range(8))
            packets = [b'\x80\x07' + seed.to_bytes(4, 'little') for seed in seeds]
            replies = await broker.submit('owner', packets)
            assert replies == [large_reply(seed) for seed in seeds]
            assert model.writes == expected
            assert all(count == 1 for count in Counter(model.writes).values())
            assert broker.owner == 'owner' and broker.failed is None
            row.update(result='recovered', verified_payload_bytes=sum(map(len, replies)),
                       write_operations=len(expected), duplicate_write_operations=0)
        row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        stats = network_stats()
        row.update(ip_packets_dropped=stats['drops'] - baseline['drops'],
                   ip_packets_delivered=stats['packets'] - baseline['packets'])
        if loss or blackout_ms or permanent:
            assert row['ip_packets_dropped'] > 0
    except Exception as error:
        row.update(result='failed', error=f'{type(error).__name__}: {error}',
                   observed_write_operations=len(model.writes), network=network_stats())
    finally:
        if restore:
            await restore
        # Close the old application session before subsequent test traffic.
        await broker.close()
        netem(0)
        server.close()
        await server.wait_closed()
        await asyncio.gather(*model.peers)
    print(json.dumps(row), flush=True)
    return row


async def run():
    rows = []
    for loss in (0, 5, 10, 30):
        rows.append(await run_case(loss=loss))
    rows.append(await run_case(loss=10, duplicate=5, reorder=25))
    rows.append(await run_case(blackout_ms=500))
    rows.append(await run_case(permanent=True))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/tests/tcp-packet-loss.json')
    parser.add_argument('--parent-netns', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run this script under Linux/WSL with tc, ip and unshare installed')
    namespace = os.stat('/proc/self/ns/net').st_ino
    if args.parent_netns is None:
        return subprocess.run(['unshare', '--net', sys.executable, str(Path(__file__).resolve()),
                               '--parent-netns', str(namespace), '--output', str(args.output.resolve())]).returncode
    if namespace == args.parent_netns:
        parser.error('The test requires a newly created private network namespace')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    report = dict(captured_at=datetime.now().astimezone().isoformat(),
                  scope='real Linux TCP/IP packet loss; modeled probe; isolated loopback; no physical device',
                  cases=asyncio.run(run()))
    report['complete'] = all(row['result'] != 'failed' for row in report['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
