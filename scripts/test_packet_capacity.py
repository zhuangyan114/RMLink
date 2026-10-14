"""Real negotiated capacities, full replies, short replies, and target read bounds."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from rmlink_swd import SwdSession
from rmlink_tool import status
from rmlink_usb import UsbClient


def check(client, capacity, backup):
    assert client.packet_size == capacity
    info = status(client)
    assert info['packetSize'] == capacity and not info['ownerTransport']
    seeds = [17 + i for i in range(8)]
    packets = [(b'\x80\x07' + struct.pack('<I', seed)).ljust(capacity, b'\0') for seed in seeds]
    expected = [b'\x80\x07\x00' + struct.pack('<I', seed) + bytes(
        (seed + i*31) & 255 for i in range(capacity - 7)) for seed in seeds]
    assert client.exchange_many(packets) == expected
    assert client.exchange(b'\x00\xff') == b'\x00\x02' + capacity.to_bytes(2, 'little')
    with SwdSession(client, 2000000) as swd:
        assert swd.read32(0xe000ed00) == 0x411fc231
        # Invalid output budgets are rejected before entering the DAP core.
        for command, budget, flags in ((6, (capacity - 4)//4 + 1, 0x0f),):
            assert client.exchange(bytes([command, 0]) + struct.pack('<H', budget) + bytes([flags])) == b'\x06\x00\x00\x08'
        reads = (capacity - 3)//4 + 1
        assert client.exchange(bytes([5, 0, reads]) + b'\x0f'*reads) == b'\x05\x00\x08'
        for pipeline in (False, True):
            values = swd.read_words(0x080003e8, 1025, pipeline)
            assert struct.pack('<' + 'I'*len(values), *values) == backup[0x3e8:0x3e8 + 4100]
    assert not status(client)['ownerTransport']
    return dict(packetSize=capacity, fullResponses=8, shortInfoAfterFullResponses=True,
                oversizedReadRejected=True, crossBoundaryReads=4100, modes=['sequential','pipeline'], ownerReleased=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tcp-size', type=int, choices=[64,128,256,512], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  scope='Read-only target Flash; native USB, virtual WiFi USB and local API')
    try:
        backup = (ROOT / 'artifacts/hardware/stm32f103c8-before-rmlink.bin').read_bytes()
        api = ApiClient()
        snapshot = api.request('GET', '/v1/status')
        assert all(d['owner'] is None for d in snapshot['devices'])
        report['connectorVersion'] = snapshot['version']
        for name, serial, capacity in (('native', '50787D15A924', 64), ('virtual', 'RM50787D15A924', args.tcp_size)):
            with UsbClient(serial=serial) as client: report[name] = check(client, capacity, backup)
        with api.open('50787d15a924'): report['api'] = check(api, args.tcp_size, backup)
        assert all(d['owner'] is None for d in api.request('GET', '/v1/status')['devices'])
        report['complete'] = True
    except BaseException as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__': main()
