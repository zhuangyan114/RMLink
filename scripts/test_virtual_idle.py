"""Verify native WinUSB idle-read cancellation and subsequent DAP responses."""
import argparse
import ctypes as C
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys
import time


PIPE_TRANSFER_TIMEOUT = 3
ERROR_SEM_TIMEOUT = 121
NORMAL_TIMEOUT_MS = 3000


def set_timeout(client, endpoint, milliseconds):
    timeout = C.c_uint32(milliseconds)
    client._check(client.usb.WinUsb_SetPipePolicy(
        client.handle, endpoint, PIPE_TRANSFER_TIMEOUT,
        C.sizeof(timeout), C.byref(timeout)))


def idle_read(client, timeout_ms, index):
    sentinel = b'\xa5' * 64
    buffer = C.create_string_buffer(sentinel, 64)
    count = C.c_uint32(0)
    C.set_last_error(0)
    start = time.perf_counter()
    ok = bool(client.usb.WinUsb_ReadPipe(
        client.handle, client.ep_in, buffer, 64, C.byref(count), None))
    error = C.get_last_error()
    elapsed_ms = (time.perf_counter() - start) * 1000
    lower_ms = timeout_ms * 0.5
    upper_ms = max(timeout_ms * 4, timeout_ms + 300)
    changed = buffer.raw != sentinel
    return {
        'timeoutMs': timeout_ms, 'index': index, 'ok': ok,
        'error': error, 'count': count.value,
        'elapsedMs': round(elapsed_ms, 2), 'bufferChanged': changed,
        'elapsedBoundsMs': [lower_ms, upper_ms],
        'passed': (not ok and error == ERROR_SEM_TIMEOUT and count.value == 0
                   and not changed and lower_ms <= elapsed_ms <= upper_ms),
    }


def query_packet_size(client, after_timeout_ms):
    result = {'afterTimeoutMs': after_timeout_ms,
              'readTimeoutMs': NORMAL_TIMEOUT_MS, 'request': '00ff'}
    try:
        response = client.exchange(b'\x00\xff')
        valid_header = len(response) >= 4 and response[:2] == b'\x00\x02'
        packet_size = int.from_bytes(response[2:4], 'little') if valid_header else None
        result.update(responseLength=len(response), packetSize=packet_size,
                      passed=valid_header and packet_size == 64)
    except Exception as exc:
        result.update(passed=False, error=f'{type(exc).__name__}: {exc}')
    return result


def run(args):
    report = {
        'capturedAt': datetime.now().astimezone().isoformat(),
        'complete': False, 'requestedSerial': args.serial,
        'requestedBcdDevice': args.bcd_device,
        'timeoutSeconds': args.timeout_seconds,
        'idleReads': [], 'normalResponses': [], 'cleanupErrors': [],
        'evidence': 'Native WinUSB IN timeouts without preceding OUT; no target operations',
    }
    client = None
    try:
        from rmlink_usb import UsbClient
        if args.driver_metadata is not None:
            report['driverMetadata'] = json.loads(args.driver_metadata.read_text(encoding='utf-8-sig'))
        client = UsbClient(serial=args.serial, bcd_device=args.bcd_device)
        report.update(serial=client.serial, bcdDevice=client.bcd_device)
        for timeout_ms in (50, 100, 500):
            print(f'Idle reads at {timeout_ms} ms', flush=True)
            set_timeout(client, client.ep_in, timeout_ms)
            for index in (1, 2):
                report['idleReads'].append(idle_read(client, timeout_ms, index))
            set_timeout(client, client.ep_in, NORMAL_TIMEOUT_MS)
            report['normalResponses'].append(query_packet_size(client, timeout_ms))
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if client is not None:
            for endpoint in (client.ep_in, client.ep_out):
                try:
                    set_timeout(client, endpoint, NORMAL_TIMEOUT_MS)
                except Exception as exc:
                    report['cleanupErrors'].append(f'{type(exc).__name__}: {exc}')
            try:
                client.close()
            except Exception as exc:
                report['cleanupErrors'].append(f'{type(exc).__name__}: {exc}')
        report['complete'] = (
            'error' not in report and not report['cleanupErrors']
            and len(report['idleReads']) == 6
            and all(item['passed'] for item in report['idleReads'])
            and len(report['normalResponses']) == 3
            and all(item['passed'] for item in report['normalResponses']))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def stop_process_tree(process):
    """Bound termination too, including a process stuck in a native call."""
    errors = []
    try:
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    if process.poll() is None:
        try:
            process.kill()
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
    try:
        process.wait(timeout=5)
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'processTerminated': process.poll() is not None,
            'terminationErrors': errors}


def parent(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix('.log')
    if log_path == args.output:
        log_path = args.output.with_name(args.output.name + '.child.log')
    command = [sys.executable, '-B', '-u', str(Path(__file__).resolve()), '--child',
               '--serial', args.serial, '--bcd-device', hex(args.bcd_device),
               '--timeout-seconds', str(args.timeout_seconds),
               '--output', str(args.output)]
    if args.driver_metadata is not None:
        command.extend(['--driver-metadata', str(args.driver_metadata)])
    report = {
        'capturedAt': datetime.now().astimezone().isoformat(),
        'complete': False, 'requestedSerial': args.serial,
        'requestedBcdDevice': args.bcd_device,
        'timeoutSeconds': args.timeout_seconds, 'childLog': str(log_path),
    }
    process = None
    try:
        with log_path.open('wb') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                return_code = process.wait(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                report['error'] = 'Native WinUSB test exceeded its process time limit'
                report.update(stop_process_tree(process))
                args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                return 1
        child_report = json.loads(args.output.read_text(encoding='utf-8'))
        return 0 if return_code == 0 and child_report.get('complete') is True else 1
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        if process is not None and process.poll() is None:
            report.update(stop_process_tree(process))
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='RM50787D15A924')
    parser.add_argument('--bcd-device', type=lambda value: int(value, 0), default=0x0100)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--driver-metadata', type=Path,
                        help='Optional existing JSON driver receipt; no driver querying')
    parser.add_argument('--timeout-seconds', type=float, default=20,
                        help='Parent process deadline, including native calls (default: 20)')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be a finite positive number')
    if not args.child:
        return_code = parent(args)
        print(json.dumps({'complete': return_code == 0, 'output': str(args.output)}))
        return return_code
    report = run(args)
    print(json.dumps({'complete': report['complete'], 'output': str(args.output)}))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
