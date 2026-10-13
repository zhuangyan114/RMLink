"""Physical SWD session exclusion and Wi-Fi-loss tests, without MCU memory writes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import time

from rmlink_swd import SwdSession
from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient


def expect(client, request, reply):
    actual = client.exchange(request)
    if actual != reply:
        raise AssertionError(f'Request {request.hex()}: {actual.hex()}, expected {reply.hex()}')


def assert_excluded(other, owner, owner_client):
    expect(other, b'\x02\x01', b'\x02\x00')
    expect(other, b'\x05\x00\x01\x02', b'\x05\x00\x08')
    expect(other, b'\x03', b'\x03\xff')
    current = status(owner_client)
    if (current['ownerTransport'], current['ownerSession']) != (owner['ownerTransport'], owner['ownerSession']):
        raise AssertionError('Competing session changed the target owner')


def wait_released(usb):
    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline:
        if status(usb)['ownerTransport'] == 0:
            return
        time.sleep(0.02)
    raise AssertionError('TCP close did not release its target ownership')


def uptime(usb):
    reply = usb.exchange(b'\x80\x06')
    if len(reply) != 15 or reply[:3] != b'\x80\x06\x00':
        raise AssertionError('Invalid uptime response')
    return struct.unpack('<QI', reply[3:])


def network(usb):
    reply = usb.exchange(b'\x80\x04')
    if len(reply) != 10 or reply[:3] != b'\x80\x04\x00':
        raise AssertionError('Invalid Wi-Fi response')
    return bool(reply[4]), socket.inet_ntoa(reply[6:10])


def run(host, port):
    report = {'clockRequestedHz': 100000, 'sessionExclusion': []}
    with UsbClient() as usb:
        # Repeated GPIO bundle create/delete and transport handover reveal lifecycle bugs.
        for cycle in range(3):
            with TcpClient(host, port) as tcp:
                with SwdSession(usb) as wired:
                    owner = status(usb)
                    assert_excluded(tcp, owner, usb)
                    cpuid = wired.read32(0xe000ed00)
                    if cpuid != 0x411fc231:
                        raise AssertionError('USB target read failed after TCP exclusion')
                if status(usb)['ownerTransport']:
                    raise AssertionError('DAP_Disconnect did not release USB owner')
                wireless = SwdSession(tcp).start()
                try:
                    owner = status(tcp)
                    assert_excluded(usb, owner, tcp)
                    if wireless.read32(0xe000ed00) != cpuid:
                        raise AssertionError('TCP target read failed after USB exclusion')
                    # Close a live owner without DAP_Disconnect: firmware must clean it up.
                    tcp.close()
                    wireless.connected = False
                    wait_released(usb)
                finally:
                    wireless.close()
                with SwdSession(usb) as reclaimed:
                    if reclaimed.read32(0xe000ed00) != cpuid:
                        raise AssertionError('USB failed to reclaim target after TCP close')
                report['sessionExclusion'].append({'cycle': cycle + 1,
                    'tcpDeniedWhileUsbOwns': True, 'usbDeniedWhileTcpOwns': True,
                    'foreignDisconnectDenied': True, 'tcpSocketCloseReleased': True,
                    'usbReclaimReadPassed': True})
        with SwdSession(usb) as wired:
            owner = status(usb)
            expected = wired.read32(0xe000ed00)
            before_uptime, reset_reason = uptime(usb)
            connected, _ = network(usb)
            if not connected:
                raise AssertionError('Wi-Fi must be connected before recovery test')
            started = time.perf_counter()
            expect(usb, b'\x80\x05', b'\x80\x05\x00')
            down = False
            reads = 0
            max_read_ms = 0.0
            while time.perf_counter() - started < 25:
                begin = time.perf_counter()
                if wired.read32(0xe000ed00) != expected:
                    raise AssertionError('Target SWD read failed while Wi-Fi reconnects')
                max_read_ms = max(max_read_ms, (time.perf_counter() - begin) * 1000)
                reads += 1
                current = status(usb)
                if (current['ownerTransport'], current['ownerSession']) != (owner['ownerTransport'], owner['ownerSession']):
                    raise AssertionError('Wi-Fi reconnect changed USB target session')
                connected, ip = network(usb)
                if not connected:
                    down = True
                elif down:
                    elapsed = (time.perf_counter() - started) * 1000
                    break
                time.sleep(0.01)
            else:
                raise AssertionError('Wi-Fi did not transition down then up')
            after_uptime, after_reason = uptime(usb)
            if after_uptime <= before_uptime or after_reason != reset_reason:
                raise AssertionError('ESP32 restarted during Wi-Fi recovery')
            with TcpClient(host, port) as tcp:
                assert_excluded(tcp, owner, usb)
            report['wifiRecoveryWithUsbTargetOwner'] = {
                'downThenUp': down, 'recoveryMs': round(elapsed, 2),
                'targetReads': reads, 'maxTargetReadMs': round(max_read_ms, 2),
                'usbOwnerUnchanged': True, 'esp32UptimeIncreasing': True,
                'newTcpDeniedWhileUsbStillOwns': True, 'ip': ip}
        report['ownerAfterTests'] = status(usb)
    with TcpClient(host, port) as tcp:
        with SwdSession(tcp) as wireless:
            report['tcpCpuidAfterUsbRelease'] = f'0x{wireless.read32(0xe000ed00):08X}'
    report['capturedAt'] = datetime.now().astimezone().isoformat()
    report['evidence'] = 'Physical STM32 read-only SWD ownership tests; no halt/reset/target memory writes; device-initiated STA disconnect, not hotspot power-off'
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    text = json.dumps(run(args.host, args.port), ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
