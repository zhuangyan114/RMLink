"""Physical ESP32-to-PC goodput and RTT; no STM32/DAP target commands.

Measures current 64-byte DAP transport separately from an opt-in bulk TCP socket.
All timing uses the PC performance counter; one-way delay is not inferred.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import statistics
import struct
import time

from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient

PATTERNS = [bytes((seed + i * 31) & 255 for i in range(57)) for seed in range(256)]


def percentile(values, percent):
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def latency_stats(samples):
    return dict(samples=len(samples), meanMs=round(statistics.mean(samples), 4),
                medianMs=round(statistics.median(samples), 4),
                p95Ms=round(percentile(samples, 95), 4), p99Ms=round(percentile(samples, 99), 4),
                minMs=round(min(samples), 4), maxMs=round(max(samples), 4))


def bench_request(sequence):
    return b'\x80\x07' + struct.pack('<I', sequence)


def bench_response(sequence):
    return b'\x80\x07\x00' + struct.pack('<I', sequence) + PATTERNS[sequence & 255]


def measure_dap_latency(client, samples, seed):
    values = []
    for index in range(samples + 30):
        sequence = seed + index
        request = bench_request(sequence)
        expected = bench_response(sequence)
        begin = time.perf_counter_ns()
        actual = client.exchange(request)
        elapsed = (time.perf_counter_ns() - begin) / 1e6
        if actual != expected:
            raise AssertionError('Diagnostic reply length, sequence, or data mismatch')
        if index >= 30:
            values.append(elapsed)
    return latency_stats(values), values


def measure_dap_goodput(client, seconds, seed):
    count = 0
    begin = time.perf_counter()
    while time.perf_counter() - begin < seconds:
        sequences = range(seed + count, seed + count + 8)
        actual = client.exchange_many([bench_request(sequence) for sequence in sequences])
        if actual != [bench_response(sequence) for sequence in sequences]:
            raise AssertionError('Pipelined diagnostic data/order mismatch')
        count += 8
    elapsed = time.perf_counter() - begin
    payload = count * 57
    return dict(seconds=round(elapsed, 4), packets=count, payloadBytes=payload,
                responseBytes=count * 64, payloadKiBPerSecond=round(payload / elapsed / 1024, 2),
                payloadMbps=round(payload * 8 / elapsed / 1e6, 3),
                responseKiBPerSecond=round(count * 64 / elapsed / 1024, 2),
                requestsPerSecond=round(count / elapsed, 2), dataMismatchCount=0)


def measure_dap(client, samples, seconds):
    rounds, all_samples = [], []
    for trial in range(3):
        latency, raw = measure_dap_latency(client, samples, trial * 1000000)
        throughput = measure_dap_goodput(client, seconds, (trial + 10) * 1000000)
        rounds.append(dict(trial=trial + 1, latency=latency, goodput=throughput))
        all_samples.extend(raw)
        print(f'DAP trial {trial + 1}: RTT median={latency["medianMs"]} ms, payload={throughput["payloadKiBPerSecond"]} KiB/s', flush=True)
    return dict(rounds=rounds, combinedRtt=latency_stats(all_samples),
                medianPayloadKiBPerSecond=round(statistics.median(row['goodput']['payloadKiBPerSecond'] for row in rounds), 2),
                payloadBytesPerReply=57, dapReplyBytes=64, pipelineDepth=8,
                sampleRttMs=all_samples, timeoutCount=0, dataMismatchCount=0)


def raw_connect(host, port):
    deadline = time.perf_counter() + 3
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError:
            if time.perf_counter() >= deadline:
                raise
            time.sleep(0.05)


def read_exact(sock, count):
    parts = []
    remaining = count
    while remaining:
        part = sock.recv(min(remaining, 65536))
        if not part:
            raise ConnectionError('Raw benchmark socket disconnected')
        parts.append(part)
        remaining -= len(part)
    return b''.join(parts)


def measure_raw(host, port, samples, stream_bytes):
    rtt_rounds, all_samples = [], []
    for trial in range(3):
        values = []
        with raw_connect(host, port) as sock:
            for index in range(samples + 30):
                payload = struct.pack('<I', trial * 1000000 + index) + PATTERNS[index & 255] + b'abc'
                frame = struct.pack('<4sIII', b'RMB1', 2, len(payload), 0) + payload
                begin = time.perf_counter_ns()
                sock.sendall(frame)
                actual = read_exact(sock, len(payload))
                elapsed = (time.perf_counter_ns() - begin) / 1e6
                if actual != payload:
                    raise AssertionError('Raw echo mismatch')
                if index >= 30:
                    values.append(elapsed)
        rtt_rounds.append(latency_stats(values))
        all_samples.extend(values)
        print(f'Raw TCP RTT trial {trial + 1}: median={rtt_rounds[-1]["medianMs"]} ms', flush=True)
    downloads = []
    for trial in range(5):
        seed = 41 + trial
        period = bytes((i + seed) & 255 for i in range(256))
        expected = (period * ((stream_bytes + 255) // 256))[:stream_bytes]
        with raw_connect(host, port) as sock:
            begin = time.perf_counter()
            sock.sendall(struct.pack('<4sIII', b'RMB1', 1, stream_bytes, seed))
            actual = read_exact(sock, stream_bytes)
            elapsed = time.perf_counter() - begin
        if actual != expected:
            raise AssertionError('Continuous TCP download content/length mismatch')
        row = dict(trial=trial + 1, bytes=stream_bytes, elapsedMs=round(elapsed * 1000, 2),
                   payloadMiBPerSecond=round(stream_bytes / elapsed / 1048576, 3),
                   payloadMbps=round(stream_bytes * 8 / elapsed / 1e6, 3), dataMismatchCount=0)
        downloads.append(row)
        print(f'Raw TCP download {trial + 1}/5: {row["payloadMbps"]} Mbps', flush=True)
    return dict(rounds=rtt_rounds, combinedRtt=latency_stats(all_samples), sampleRttMs=all_samples,
                downloadRounds=downloads,
                medianDownloadMbps=round(statistics.median(row['payloadMbps'] for row in downloads), 3),
                medianDownloadMiBPerSecond=round(statistics.median(row['payloadMiBPerSecond'] for row in downloads), 3),
                echoPayloadBytes=64, timeoutCount=0, dataMismatchCount=0,
                timingExcludesConnectionAndDns=True, bypassesDapExecutor=True)


def uptime(client):
    reply = client.exchange(b'\x80\x06')
    if len(reply) != 15 or reply[:3] != b'\x80\x06\x00':
        raise AssertionError('Invalid uptime reply')
    return struct.unpack('<QI', reply[3:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--port', type=int, default=3241)
    parser.add_argument('--samples', type=int, default=500)
    parser.add_argument('--goodput-seconds', type=float, default=2)
    parser.add_argument('--stream-mib', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 5000 or not 0 < args.goodput_seconds <= 20 or not 1 <= args.stream_mib <= 16:
        parser.error('Invalid benchmark duration or sample count')
    report = dict(startedAt=datetime.now().astimezone().isoformat(), completed=False,
                  host=args.host, timing='PC perf_counter; RTT is not one-way delay',
                  evidence='ESP32/PC physical application goodput; no STM32 access, not PHY link rate or SWD/Flash throughput')
    try:
        with UsbClient() as usb:
            report['deviceBefore'] = status(usb)
            if report['deviceBefore']['ownerTransport']:
                raise RuntimeError('A target session is active; stop it before transport-only testing')
            before_uptime, reset_reason = uptime(usb)
            network = usb.exchange(b'\x80\x04')
            if len(network) != 10 or network[:3] != b'\x80\x04\x00' or not network[4]:
                raise RuntimeError('Wi-Fi must already be connected')
            report['networkBefore'] = dict(ip=socket.inet_ntoa(network[6:10]), rssiDbm=struct.unpack('b', network[5:6])[0])
            print('Measuring USB DAP transport...', flush=True)
            report['usbDap'] = measure_dap(usb, args.samples, args.goodput_seconds)
            print('Measuring Wi-Fi DAP transport...', flush=True)
            with TcpClient(args.host, args.port) as tcp:
                report['tcpDap'] = measure_dap(tcp, args.samples, args.goodput_seconds)
            if usb.exchange(b'\x80\x08' + struct.pack('<H', 300)) != b'\x80\x08\x00':
                raise RuntimeError('Raw transport benchmark could not be enabled')
            try:
                print('Measuring direct Wi-Fi TCP...', flush=True)
                report['rawTcp'] = measure_raw(args.host, args.port + 1, args.samples, args.stream_mib * 1048576)
            finally:
                if usb.exchange(b'\x80\x08\x00\x00') != b'\x80\x08\x00':
                    raise RuntimeError('Could not disable raw benchmark')
            after_uptime, after_reason = uptime(usb)
            report['deviceAfter'] = status(usb)
            report['uptimeBeforeUs'] = before_uptime
            report['uptimeAfterUs'] = after_uptime
            report['esp32NoReboot'] = after_uptime > before_uptime and after_reason == reset_reason
            if not report['esp32NoReboot'] or report['deviceAfter']['ownerTransport']:
                raise AssertionError('Unexpected reboot or target ownership')
        report['completed'] = True
    except BaseException as error:
        report['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('completed', 'networkBefore', 'esp32NoReboot')}, indent=2))


if __name__ == '__main__':
    main()
