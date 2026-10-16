"""Read the current 64 KiB fixture through native and virtual USB CMSIS-DAP.

Does not halt, reset or program the STM32. The SWD connection configures DP/AP
registers as usual; target Flash and RAM are only read. Run after subscriptions
have released their session. Physical NRST is outside this check.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from rmlink_usb import UsbClient
from rmlink_swd import SwdSession
from rmlink_connector.client import ApiClient

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve existing evidence')
    image = (ROOT / 'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes()
    if len(image) != 65536:
        raise ValueError('Build the full fixture image first')
    report = dict(at=datetime.now().astimezone().isoformat(), complete=False,
                  requestedClockHz=4000000, fixtureSha256=hashlib.sha256(image).hexdigest(), rows=[])
    try:
        devices = ApiClient().request('GET', '/v1/status')['devices']
        device = next(d for d in devices if d['device_id'] == '50787d15a924')
        if device['owner'] is not None:
            raise RuntimeError('A service session still owns the target')
        for serial in ('50787D15A924', 'RM50787D15A924'):
            with UsbClient(serial=serial) as usb:
                with SwdSession(usb, clock=4000000) as swd:
                    cpuid = swd.read32(0xe000ed00)
                    cfgr = swd.read32(0x40021004)
                    before = swd.read32(0xe000edf0)
                    began = time.perf_counter()
                    actual = struct.pack('<16384I', *swd.read_words(0x08000000, 16384, pipelined=True))
                    elapsed = time.perf_counter() - began
                    after = swd.read32(0xe000edf0)
                    row = dict(serial=serial, packetSize=usb.packet_size, cpuid=cpuid,
                               dpId=swd.idcode, rccCfgr=cfgr, dhcsrBefore=before, dhcsrAfter=after,
                               readSeconds=elapsed, sha256=hashlib.sha256(actual).hexdigest(),
                               flashMatches=actual == image, targetRunning=not ((before | after) & 0x20000))
                    report['rows'].append(row)
                    if cpuid != 0x411fc231 or cfgr != 0x001d840a or not row['flashMatches'] or not row['targetRunning']:
                        raise AssertionError(f'USB read regression: {row}')
        report['complete'] = True
    except BaseException as error:
        report['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
