"""Physical-device checks, with target access disabled. No STM32 operations.

Checks USB packet pipelines, real TCP framing/recovery, and three radio-only
disconnects while retaining the same WinUSB handle and increasing MCU uptime.
"""
import argparse
import json
import socket
import struct
import time

from rmlink_tool import TcpClient, status, verify
from rmlink_usb import UsbClient


def network(client):
    reply = client.exchange(b'\x80\x04')
    if len(reply) != 10 or reply[:3] != b'\x80\x04\x00':
        raise AssertionError('Invalid network status')
    return {'configured': bool(reply[3]), 'connected': bool(reply[4]),
            'ip': socket.inet_ntoa(reply[6:10])}


def uptime(client):
    reply = client.exchange(b'\x80\x06')
    if len(reply) != 15 or reply[:3] != b'\x80\x06\x00':
        raise AssertionError('Invalid uptime status')
    return struct.unpack('<QI', reply[3:])


def usb_pipeline(usb):
    requests = [b'\x00\xff', b'\x00\xfe', b'\x80\x00', b'\x00\xf0',
                b'\x00\xf1', b'\x00\x02', b'\x00\x01', b'\x00\x04']
    # Different lengths/content detect reordering, overwritten DMA and packet merges.
    expected = [usb.exchange(request) for request in requests]
    count = 0
    begin = time.perf_counter()
    for _ in range(100):
        for request in requests:
            usb.send(request)
        time.sleep(0.001)
        actual = [usb.receive() for _ in requests]
        if actual != expected:
            raise AssertionError('USB pipeline content/order changed')
        count += len(requests)
    return {'packets': count, 'pipelineDepth': 8,
            'elapsedMs': round((time.perf_counter() - begin) * 1000, 2)}


def tcp_framing(host, port, usb):
    golden = b'DAP\0\x02\0\x01\0\x00\xff'
    expected = b'\x00\x02\x40\x00'
    with TcpClient(host, port) as peer:
        for value in golden:
            peer.sock.sendall(bytes([value]))
            time.sleep(0.001)
        if peer.read(8) != b'DAP\0\x04\0\x02\0' or peer.read(4) != expected:
            raise AssertionError('Fragmented TCP frame failed')
        begin = time.perf_counter()
        for _ in range(100):
            peer.sock.sendall(golden * 8)
            for _ in range(8):
                if peer.read(8) != b'DAP\0\x04\0\x02\0' or peer.read(4) != expected:
                    raise AssertionError('Coalesced TCP frames failed')
        pipeline_ms = (time.perf_counter() - begin) * 1000
        if peer.exchange(b'\x80\x02\x00\x00\x01\x01x') != b'\x80\x02\x02':
            raise AssertionError('TCP unexpectedly accepted USB provisioning')
    bad_headers = [b'BAD\0\x02\0\x01\0', b'DAP\0\x41\0\x01\0',
                   b'DAP\0\x02\0\x02\0', b'DAP\0\x02\0\x01\x01']
    for header in bad_headers:
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.sendall(header)
            try:
                data = sock.recv(64)
            except ConnectionResetError:
                data = b''
            if data:
                raise AssertionError('Invalid TCP header was processed')
        if usb.exchange(b'\x00\xff') != expected:
            raise AssertionError('Malformed TCP disrupted USB')
    with socket.create_connection((host, port), timeout=4) as sock:
        sock.sendall(golden[:5])  # Incomplete header must time out without a reboot.
        try:
            data = sock.recv(64)
        except ConnectionResetError:
            data = b''
        if data:
            raise AssertionError('Incomplete header produced data')
    with TcpClient(host, port) as peer:
        if peer.exchange(b'\x00\xff') != expected:
            raise AssertionError('TCP did not recover after incomplete frame')
    return {'fragmentedFrame': True, 'coalescedFrames': 800, 'pipelineDepth': 8,
            'pipelineElapsedMs': round(pipeline_ms, 2),
            'rejectedHeaders': len(bad_headers), 'partialHeaderTimeout': True,
            'tcpProvisioningRejected': True}


def radio_recovery(host, port, usb):
    cycles = []
    previous, reason = uptime(usb)
    for index in range(3):
        if not network(usb)['connected']:
            raise AssertionError('Radio test requires initial Wi-Fi connection')
        old = TcpClient(host, port)
        try:
            if old.exchange(b'\x00\xff') != b'\x00\x02\x40\x00':
                raise AssertionError('Initial TCP query failed')
            started = time.perf_counter()
            if usb.exchange(b'\x80\x05') != b'\x80\x05\x00':
                raise AssertionError('Radio reconnect command failed')
            down = False
            queries = 0
            max_query_ms = 0.0
            while time.perf_counter() - started < 20:
                begin = time.perf_counter()
                if usb.exchange(b'\x00\xff') != b'\x00\x02\x40\x00':
                    raise AssertionError('USB query failed during radio recovery')
                max_query_ms = max(max_query_ms, (time.perf_counter() - begin) * 1000)
                queries += 1
                current = network(usb)
                elapsed = time.perf_counter() - started
                if not current['connected']:
                    down = True
                elif down:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError('Did not observe Wi-Fi down then up within 20 seconds')
            # No automatic replay: the old stream's in-flight completion is unknown.
            old_stream_state = 'usable'
            try:
                old.exchange(b'\x00\xff')
            except TimeoutError:
                old_stream_state = 'timeout'
            except (OSError, ConnectionError):
                old_stream_state = 'closed_or_reset'
            if old_stream_state == 'usable':
                raise AssertionError('Old TCP stream survived a radio disconnect')
        finally:
            old.close()
        with TcpClient(host, port) as new:
            if new.exchange(b'\x00\xff') != b'\x00\x02\x40\x00':
                raise AssertionError('New TCP stream did not recover')
        now, now_reason = uptime(usb)
        if now <= previous or now_reason != reason:
            raise AssertionError('MCU uptime/reset evidence indicates a restart')
        cycles.append({'cycle': index + 1, 'downThenUp': down,
                       'recoveryMs': round(elapsed * 1000, 2), 'usbQueries': queries,
                       'usbMaxQueryMs': round(max_query_ms, 2), 'oldTcpEndState': old_stream_state,
                       'uptimeUs': now, 'resetReason': now_reason})
        previous = now
    return cycles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    args = parser.parse_args()
    with UsbClient() as usb:
        initial = status(usb)
        if initial['targetPinsConfirmed']:
            raise RuntimeError('Transport-only acceptance requires target GPIO disabled')
        report = {'device': initial, 'usbPipeline': usb_pipeline(usb)}
        report['tcpFraming'] = tcp_framing(args.host, args.port, usb)
        report['radioRecovery'] = radio_recovery(args.host, args.port, usb)
        report['usbAfterRecovery'] = verify(usb)
    with TcpClient(args.host, args.port) as tcp:
        report['tcpAfterRecovery'] = verify(tcp)
    report['evidence'] = 'physical ESP32 + USB/Wi-Fi transport; STM32 SWD remains untested'
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
