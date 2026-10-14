"""Real API/virtual USB clock-before-Connect session arbitration."""
from datetime import datetime
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector.client import ApiClient
from rmlink_swd import SwdSession
from rmlink_usb import UsbClient


def main():
    control = ApiClient()
    snapshot = control.request('GET', '/v1/status')
    devices = [d for d in snapshot['devices'] if d['device_id'] == '50787d15a924' and d['connected']]
    assert len(devices) == 1 and devices[0]['owner'] is None, 'Expected online, unoccupied RMLink'
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  connectorVersion=snapshot['version'], firmwareVersion=devices[0]['firmware'])
    clock = b'\x11' + struct.pack('<I', 2000000)
    with ApiClient().open(devices[0]['device_id']) as a, ApiClient().open(devices[0]['device_id']) as b:
        assert a.exchange(b'\x11') == b'\x11\xff'
        assert control.request('GET', '/v1/status')['devices'][0]['owner'] is None
        assert a.exchange(clock) == b'\x11\x00'
        assert b.exchange_many([clock, b'\x02\x01', b'\x03']) == [b'\x11\xff', b'\x02\x00', b'\x03\xff']
        with UsbClient(bcd_device=0x100, serial='RM50787D15A924') as virtual:
            assert virtual.exchange_many([clock, b'\x02\x01', b'\x03']) == [b'\x11\xff', b'\x02\x00', b'\x03\xff']
            with SwdSession(a, clock=2000000) as target:
                assert target.read32(0xe000ed00) == 0x411fc231
            assert virtual.exchange(clock) == b'\x11\x00'
            try:
                assert a.exchange_many([clock, b'\x02\x01', b'\x03']) == [b'\x11\xff', b'\x02\x00', b'\x03\xff']
                with SwdSession(virtual, clock=2000000) as target:
                    assert target.read32(0xe000ed00) == 0x411fc231
            finally:
                assert virtual.exchange(b'\x03') == b'\x03\x00'
        # Closing an API lease releases a pre-Connect clock reservation too.
        assert b.exchange(clock) == b'\x11\x00'
    assert control.request('GET', '/v1/status')['devices'][0]['owner'] is None
    report.update(complete=True, apiClockBeforeConnect=True, invalidClockDoesNotClaim=True,
                  apiForeignDenied=True, apiVirtualMutualExclusion=True,
                  apiAndVirtualCpuid='0x411fc231', apiCloseBeforeConnectReleases=True,
                  ownerAfterTests=None,
                  evidence='Real connector API, installed virtual WinUSB and STM32 SWD; no Flash writes or target reset')
    path = ROOT / 'artifacts/logs/post-reboot-20261014/connector-preconnect-011.json'
    path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
