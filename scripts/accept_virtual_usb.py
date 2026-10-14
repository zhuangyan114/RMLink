"""Windows UDE/WinUSB + standard OpenOCD acceptance over physical Wi-Fi.

Explicitly select virtual bcdDevice 0x0100; never substitute the wired probe.
Erase/program is opt-in, and the original validated 64 KiB image is restored.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time

from accept_stm32 import debug_checks, main_flash, require
from rmlink_swd import SwdSession
from rmlink_tool import status
from rmlink_usb import UsbClient

ROOT = Path(__file__).resolve().parents[1]


def program(openocd, serial, image, label, log_directory):
    command = [str(openocd), '-c', 'adapter driver cmsis-dap', '-c', 'cmsis-dap backend usb_bulk',
               '-c', 'adapter usb vid_pid 0x303a 0x1002', '-c', f'adapter serial {serial}',
               '-c', 'transport select swd', '-f', 'target/stm32f1x.cfg', '-c', 'adapter speed 2000',
               '-c', f'program {image.as_posix()} 0x08000000 verify reset exit']
    begin = time.perf_counter()
    done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    text = done.stdout.decode('utf-8', errors='replace')
    log = log_directory / f'accept-virtual-openocd-{label}.log'
    log.write_text(text, encoding='utf-8')
    result = dict(exitCode=done.returncode, elapsedMs=round((time.perf_counter()-begin)*1000, 2),
                  verified='** Verified OK **' in text, log=str(log),
                  errorLines=[line for line in text.splitlines() if line.startswith('Error:')])
    require(done.returncode == 0 and result['verified'], f'OpenOCD {label} failed: {log}')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-target-write', action='store_true')
    parser.add_argument('--openocd', type=Path, default=ROOT / 'artifacts/tools/openocd-20260831/openocd-esp32/bin/openocd.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/logs/accept-virtual-usb.json')
    args = parser.parse_args()
    if not args.allow_target_write: parser.error('--allow-target-write required for target Flash acceptance')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    backup_path = ROOT / 'artifacts/hardware/stm32f103c8-before-rmlink.bin'
    fixture_path = ROOT / 'artifacts/build/stm32-fixture/fixture-64k.bin'
    backup, fixture = backup_path.read_bytes(), fixture_path.read_bytes()
    require(len(backup) == len(fixture) == 65536, 'Expected full 64 KiB images')
    require(hashlib.sha256(backup).hexdigest() == '75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff', 'Original backup mismatch')
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False,
                  transport='Windows virtual USB -> loopback USB/IP -> WiFi TCP DAP',
                  virtualBcdDevice='0x0100', physicalNrstTested=False)
    modified = False
    try:
        with UsbClient(bcd_device=0x0100) as c, SwdSession(c, 2000000) as s:
            report['virtualSerial'] = serial = c.serial
            require(serial != '50787D15A924', 'Virtual serial must distinguish the physical USB device')
            report['device'] = status(c)
            report['cpuid'] = hex(s.read32(0xe000ed00))
            require(report['cpuid'] == '0x411fc231', 'Unexpected target')
            require(main_flash(s) == backup, 'Target changed since original backup; do not erase')
            report['optionBytesBefore'] = s.read_words(0x1ffff800, 4)
        print('Virtual USB: OpenOCD erase/program/verify/reset 64 KiB', flush=True)
        modified = True
        report['openocdProgram'] = program(args.openocd, serial, fixture_path, 'fixture', args.output.parent)
        print('Virtual USB: independent Flash readback, debugging and abort checks', flush=True)
        report['debug'] = {}
        with UsbClient(bcd_device=0x0100, serial=serial) as c:
            debug_checks(c, fixture, 'VirtualUSB', report['debug'], without_nrst=True)
        report['complete'] = True
    except BaseException as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if modified:
            try:
                print('Restoring original target Flash through virtual USB', flush=True)
                report['restoreProgram'] = program(args.openocd, serial, backup_path, 'restore', args.output.parent)
                with UsbClient(bcd_device=0x0100, serial=serial) as c, SwdSession(c, 2000000) as s:
                    data = main_flash(s)
                    require(data == backup, 'Virtual USB restore differs from original')
                    require(s.read_words(0x1ffff800, 4) == report['optionBytesBefore'], 'Option bytes changed')
                    report['restoredSha256'] = hashlib.sha256(data).hexdigest()
                    report['optionBytesUnchanged'] = True
                    report['coreHaltedAtEnd'] = bool(s.read32(0xe000edf0) & 0x20000)
                report['restored'] = True
            except BaseException as exc:
                report.update(complete=False, restored=False, restoreFailure=f'{type(exc).__name__}: {exc}')
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)
        if modified and not report.get('restored'):
            raise RuntimeError('Target restore failed; inspect the saved report before further operations')


if __name__ == '__main__': main()
