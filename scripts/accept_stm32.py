"""Destructive, explicitly authorized STM32F103 main-Flash/debug acceptance.

Runs the same image over USB and TCP with upstream OpenOCD, then independently
checks actual Flash bytes, core controls, RAM, FPB/DWT, NRST and error recovery.
Always attempts to restore the original 64 KiB Flash in finally. Option bytes
are read/compared, never written. Requires --allow-target-write.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time

from rmlink_usb import UsbClient
from rmlink_tool import TcpClient, status
from rmlink_swd import SwdSession

ROOT = Path(__file__).resolve().parents[1]
DHCSR, DCRSR, DCRDR, DEMCR, DFSR = 0xe000edf0, 0xe000edf4, 0xe000edf8, 0xe000edfc, 0xe000ed30
STATE, SCRATCH = 0x20000000, 0x20004000


def require(ok, message):
    if not ok: raise AssertionError(message)


def poll_read(s, address, predicate, seconds=3):
    deadline = time.perf_counter() + seconds
    while True:
        value = s.read32(address)
        if predicate(value): return value
        if time.perf_counter() >= deadline:
            raise TimeoutError(f'Poll {address:#x} timed out, last {value:#x}')
        time.sleep(0.005)


def halt(s):
    s.write32(DHCSR, 0xa05f0003)
    poll_read(s, DHCSR, lambda x: x & 0x20000)


def resume(s): s.write32(DHCSR, 0xa05f0001)


def core_read(s, reg):
    s.write32(DCRSR, reg)
    poll_read(s, DHCSR, lambda x: x & 0x10000)
    return s.read32(DCRDR)


def core_write(s, reg, value):
    s.write32(DCRDR, value)
    s.write32(DCRSR, reg | 0x10000)
    poll_read(s, DHCSR, lambda x: x & 0x10000)


def step(s):
    s.write32(DHCSR, 0xa05f000b)  # Change MASKINTS while halted.
    s.write32(DHCSR, 0xa05f000d)
    poll_read(s, DHCSR, lambda x: x & 0x20000)


def main_flash(s):
    return struct.pack('<16384I', *s.read_words(0x08000000, 16384, pipelined=True))


def openocd(args, transport, image, label):
    command = [str(args.openocd), '-c', 'adapter driver cmsis-dap', '-c', f'cmsis-dap backend {transport}']
    if transport == 'usb_bulk':
        command += ['-c', 'adapter usb vid_pid 0x303a 0x1002', '-c', 'adapter serial 50787D15A924']
    else:
        command += ['-c', f'cmsis-dap tcp host {args.host}', '-c', 'cmsis-dap tcp port 3241',
                    '-c', 'cmsis-dap tcp min_timeout 500', '-c', 'cmsis-dap quirk enable']
    command += ['-c', 'transport select swd', '-f', 'target/stm32f1x.cfg', '-c', 'adapter speed 2000',
                '-c', f'program {image.as_posix()} 0x08000000 verify reset exit']
    begin = time.perf_counter()
    done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    text = done.stdout.decode('utf-8', errors='replace')
    log = ROOT / 'artifacts' / 'logs' / f'accept-openocd-{label}.log'
    log.write_text(text, encoding='utf-8')
    result = dict(exitCode=done.returncode, elapsedMs=round((time.perf_counter() - begin) * 1000, 2),
                  log=str(log), verified='** Verified OK **' in text,
                  tcpQuirkEnabled=transport == 'tcp',
                  errorLines=[line for line in text.splitlines() if line.startswith('Error:')])
    require(done.returncode == 0 and result['verified'], f'OpenOCD {label} failed, see {log}')
    return result


def debug_checks(client, image, label, result, without_nrst=False):
    with SwdSession(client, 2000000) as s:
        require(s.read32(0xe0042000) & 0xfff == 0x410, 'Expected STM32F103 medium-density')
        begin = time.perf_counter()
        readback = main_flash(s)
        require(readback == image, 'Independent full Flash byte comparison failed')
        result['flashReadback'] = dict(bytes=len(readback), sha256=hashlib.sha256(readback).hexdigest(),
                                      elapsedMs=round((time.perf_counter() - begin) * 1000, 2))
        require(s.read32(STATE) == 0x524d4c4b, 'Fixture did not boot')
        before = s.read32(STATE + 8); time.sleep(.05); after = s.read32(STATE + 8)
        require(after > before, 'Running counter did not advance')
        halt(s)
        stopped = s.read32(STATE + 8); time.sleep(.03)
        require(s.read32(STATE + 8) == stopped, 'Halt did not stop counter')
        registers = [core_read(s, i) for i in range(17)]
        require(registers[16] & 0x1000000, 'Thumb xPSR missing')
        core_write(s, 7, 0x7351acdf)
        require(core_read(s, 7) == 0x7351acdf, 'Core register write/read failed')
        core_write(s, 7, registers[7])
        result['haltRegisterRun'] = dict(counterBefore=before, counterRunning=after, counterHalted=stopped,
                                             registersBefore=registers)
        # RAM scratch spans MEM-AP 1 KiB auto-increment boundaries.
        words = [((i * 0x1020307) ^ 0xa5f03c9b) & 0xffffffff for i in range(768)]
        s.write_words(SCRATCH, words, pipelined=True)
        require(s.read_words(SCRATCH, len(words), pipelined=True) == words, 'RAM block comparison failed')
        result['ramWriteRead'] = dict(address=hex(SCRATCH), bytes=len(words) * 4, crosses1KiBBoundaries=True)
        # FPB comparator 0: Flash Thumb function at 0x080000c2 (upper halfword).
        probe = 0x080000c2
        s.write32(DFSR, 0x1f)
        s.write32(0xe0002008, (probe & 0x1ffffffc) | 0x80000001)
        s.write32(0xe0002000, 3)
        resume(s); poll_read(s, DHCSR, lambda x: x & 0x20000)
        pc = core_read(s, 15); reason = s.read32(DFSR)
        require(pc == probe and reason & 2, f'FPB breakpoint mismatch PC={pc:#x}, DFSR={reason:#x}')
        s.write32(0xe0002008, 0)
        s.write32(0xe0002000, 2)
        step(s); stepped = core_read(s, 15)
        require(stepped != pc, 'Single-step PC did not move')
        result['hardwareBreakpointAndStep'] = dict(pc=hex(pc), dfsr=hex(reason), pcAfterStep=hex(stepped))
        # SRAM software breakpoint, then restore MOVS R0,#17 and single-step it.
        ram_pc = SCRATCH
        s.write_words(ram_pc, [0x3001be00, 0xe7fe4600])  # BKPT, ADD R0,#1, NOP, B .
        core_write(s, 15, ram_pc); core_write(s, 16, 0x01000000); core_write(s, 0, 0)
        s.write32(DFSR, 0x1f)
        resume(s); poll_read(s, DHCSR, lambda x: x & 0x20000)
        require(core_read(s, 15) == ram_pc and s.read32(DFSR) & 2, 'Software breakpoint failed')
        s.write32(ram_pc, 0x30012011)  # MOVS R0,#17; ADD R0,#1
        core_write(s, 15, ram_pc)
        step(s)
        require(core_read(s, 0) == 17 and core_read(s, 15) == ram_pc + 2, 'MOVS single-step mismatch')
        step(s)
        require(core_read(s, 0) == 18 and core_read(s, 15) == ram_pc + 4, 'ADD single-step mismatch')
        result['softwareBreakpointAndStep'] = dict(pc=hex(ram_pc), register0AfterTwoSteps=18)
        if without_nrst:
            result['nrstAndResetTarget'] = dict(tested=False, passed=None,
                    reason='User confirmed no spare NRST jumper; three-wire SWD acceptance uses SYSRESETREQ')
        else:
            # Actual NRST low/high, observing pin state and subsequent startup.
            halt(s); s.write32(STATE + 12, 0xdeadc0de)
            low = client.exchange(b'\x10\x00\x80' + struct.pack('<I', 1000))
            require(len(low) == 2 and not low[1] & 0x80, f'NRST low not observed: {low.hex()}')
            time.sleep(.02)
            high = client.exchange(b'\x10\x80\x80' + struct.pack('<I', 1000))
            require(len(high) == 2 and high[1] & 0x80, f'NRST high not observed: {high.hex()}')
            resume(s); time.sleep(.05)
            pin_reset = s.read32(STATE) == 0x524d4c4b and s.read32(STATE + 12) == 0x13579bdf
            halt(s); s.write32(STATE + 12, 0xdeadc0de)
            s.command(b'\x0a', b'\x0a\x00\x01')
            resume(s); time.sleep(.05)
            command_reset = s.read32(STATE + 12) == 0x13579bdf
            result['nrstAndResetTarget'] = dict(tested=True, lowReply=low.hex(), highReply=high.hex(),
                    pinResetPassed=pin_reset, commandResetPassed=command_reset,
                    passed=pin_reset and command_reset, guardAfterReset=hex(s.read32(STATE + 12)))
        # A missing NRST wire must not prevent testing the remaining functions.
        # It remains a failed required check in the overall report.
        halt(s); s.write32(STATE + 12, 0xdeadc0de)
        s.write32(0xe000ed0c, 0x05fa0004); resume(s); time.sleep(.05)
        require(s.read32(STATE + 12) == 0x13579bdf, 'SYSRESETREQ did not reset fixture')
        result['softwareSystemReset'] = dict(passed=True)
        # DWT write watchpoint on actual counter in the running fixture.
        s.write32(DEMCR, 1 << 24)
        s.write32(DFSR, 0x1f)
        s.write32(0xe0001020, STATE + 8); s.write32(0xe0001024, 0); s.write32(0xe0001028, 6)
        resume(s); poll_read(s, DHCSR, lambda x: x & 0x20000)
        reason = s.read32(DFSR)
        require(reason & 4, f'DWT watchpoint did not fire: {reason:#x}')
        s.write32(0xe0001028, 0); s.write32(DEMCR, 0)
        result['dataWatchpoint'] = dict(dfsr=hex(reason), pc=hex(core_read(s, 15)))
        resume(s)
        time.sleep(.03); require(not s.read32(DHCSR) & 0x20000, 'Resume did not run')
        # Test an unmapped AHB read, preserve FAULT then explicitly clear sticky.
        reply = client.exchange(b'\x05\x00\x02\x05' + struct.pack('<I', 0x08100000) + b'\x0f')
        require(len(reply) >= 3 and reply[0] == 5 and reply[2] == 4, f'Expected physical AHB FAULT: {reply.hex()}')
        s.command(b'\x08\x00' + struct.pack('<I', 0x1e), b'\x08\x00')
        s.dp_write(8, 0)
        require(s.read32(0xe000ed00) == 0x411fc231, 'FAULT clear/recovery failed')
        result['faultRecovery'] = dict(faultResponse=reply.hex(), recoveryCpuid='0x411FC231')
        # Exercise SWD sequence output/input followed by a valid DP transaction.
        seq = client.exchange(b'\x1d\x02\x38' + b'\xff' * 7 + b'\x88')
        require(len(seq) == 3 and seq[:2] == b'\x1d\x00', f'SWD_Sequence failed: {seq.hex()}')
        s.command(b'\x12\x08\x00', b'\x12\x00')
        require(s.dp_read(0) == s.idcode, 'SWD sequence left DP inaccessible')
        result['swdSequence'] = seq.hex()
        # SWCLK monitor must reflect the real output/input level, not a stub.
        for level in (1, 0):
            pins = client.exchange(bytes([0x10, level, 1]) + struct.pack('<I', 1000))
            require(len(pins) == 2 and pins[1] & 1 == level, f'SWCLK level query failed: {pins.hex()}')
        s.command(b'\x12\x38' + b'\xff' * 7, b'\x12\x00')
        s.command(b'\x12\x08\x00', b'\x12\x00')
        require(s.dp_read(0) == s.idcode, 'Pin controls left DP inaccessible')
        result['swclkPinMonitor'] = dict(highAndLow=True)
        for request in (b'\x06\x00\xff\xff\x02', b'\x05\x00\x01\x42',
                        b'\x11\x01\x00\x00\x00', b'\x13\xf8'):
            require(client.exchange(request)[-1] in (8, 0xff), 'Malformed packet was accepted')
        for unsupported in (0x0b, 0x14, 0x1a, 0x7e, 0x7f):
            require(client.exchange(bytes([unsupported])) == b'\xff', 'Unsupported response is not DAP_Invalid')
        # No-transfer abort must be silent; subsequent query is the only reply.
        client.abort_transfer()
        require(client.exchange(b'\x00\xff') == b'\x00\x02\x40\x00', 'Idle abort produced a reply')
        # Impossible physical DP value-match demonstrates concurrent cancellation.
        s.command(b'\x11' + struct.pack('<I', 100000), b'\x11\x00')
        s.command(b'\x04\x02\x64\x00\xff\xff', b'\x04\x00')
        s.transfer([(0x20, 0xffffffff)])
        match = b'\x05\x00\x01\x12' + struct.pack('<I', 0)
        begin = time.perf_counter(); client.send(match); time.sleep(.08); client.abort_transfer()
        reply = client.receive(); elapsed = (time.perf_counter() - begin) * 1000
        require(reply == b'\x05\x00\x11' and elapsed < 700, f'Async abort failed: {reply.hex()} {elapsed} ms')
        require(client.exchange(b'\x00\xff') == b'\x00\x02\x40\x00', 'Abort shifted response ordering')
        result['asynchronousAbort'] = dict(actualResponse=reply.hex(), elapsedMs=round(elapsed, 2), hostDelayMs=80)
        begin = time.perf_counter(); reply = client.exchange(match); elapsed = (time.perf_counter() - begin) * 1000
        require(reply == b'\x05\x00\x11' and 900 < elapsed < 1800, f'Transfer deadline failed {elapsed} ms')
        result['boundedTransferDeadline'] = dict(elapsedMs=round(elapsed, 2), response=reply.hex())
        # A slow, successful DP block must stop between words as well as on WAIT.
        s.command(b'\x11' + struct.pack('<I', 1000), b'\x11\x00')
        begin = time.perf_counter(); client.send(b'\x06\x00\x0f\x00\x02')
        time.sleep(.08); client.abort_transfer(); reply = client.receive()
        elapsed = (time.perf_counter() - begin) * 1000
        count = int.from_bytes(reply[1:3], 'little') if len(reply) >= 4 else 0
        require(reply[0] == 6 and reply[3] == 1 and 0 < count < 15 and len(reply) == 4 + count * 4,
                f'Block cancellation/count failed: {reply.hex()}')
        require(all(v == s.idcode for v in struct.unpack('<' + 'I' * count, reply[4:])), 'Cancelled block data mismatch')
        result['blockAbort'] = dict(requestedWords=15, actualWords=count, elapsedMs=round(elapsed, 2), response=reply.hex())
        s.command(b'\x11' + struct.pack('<I', 2000000), b'\x11\x00')
        s.command(b'\x04\x02\x64\x00\x00\x00', b'\x04\x00')
        require(s.read32(0xe000ed00) == 0x411fc231, 'Post-abort/deadline recovery failed')
        result['coreRunningAtEnd'] = not bool(s.read32(DHCSR) & 0x20000)
        result['ownerDuring'] = status(client)
    result['ownerAfter'] = status(client)
    require(result['ownerAfter']['ownerTransport'] == 0, f'{label} owner leaked')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-target-write', action='store_true')
    parser.add_argument('--without-nrst', action='store_true', help='Explicit three-wire SWD scope; physical NRST remains unaccepted')
    parser.add_argument('--host', default='rmlink-15a924.local')
    parser.add_argument('--openocd', type=Path, default=ROOT / 'artifacts/tools/openocd-20260831/openocd-esp32/bin/openocd.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/logs/accept-stm32.json')
    args = parser.parse_args()
    if not args.allow_target_write: parser.error('Explicit --allow-target-write required; erases/programs target main Flash')
    image_path = ROOT / 'artifacts/build/stm32-fixture/fixture-64k.bin'
    backup_path = ROOT / 'artifacts/hardware/stm32f103c8-before-rmlink.bin'
    image, backup = image_path.read_bytes(), backup_path.read_bytes()
    require(len(image) == len(backup) == 65536, 'Expected exactly 64 KiB main Flash')
    require(hashlib.sha256(backup).hexdigest() == '75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff', 'Original backup hash differs')
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False, transports={},
                  requestedClockHz=2000000, fixtureSha256=hashlib.sha256(image).hexdigest(),
                  originalFlashSha256=hashlib.sha256(backup).hexdigest(),
                  resetScope='three-wire SWD, software SYSRESETREQ; physical NRST excluded' if args.without_nrst else 'software and physical NRST')
    modified = False
    try:
        with UsbClient() as c, SwdSession(c, 2000000) as s:
            require(s.read32(0x1ffff7e0) & 0xffff == 64, 'Expected 64 KiB target')
            report['optionBytesBefore'] = s.read_words(0x1ffff800, 4)
            report['uniqueId'] = s.read_words(0x1ffff7e8, 3)
        for label, backend in (('USB', 'usb_bulk'), ('WiFi', 'tcp')):
            print(f'{label}: OpenOCD erase/program/verify/reset 64 KiB', flush=True)
            result = report['transports'][label] = {}
            modified = True  # Set before programming; uncertain failures also require recovery.
            result['openocdProgram'] = openocd(args, backend, image_path, label.lower())
            print(f'{label}: full readback and debug checks', flush=True)
            with (UsbClient() if label == 'USB' else TcpClient(args.host, 3241)) as c:
                result['device'] = status(c)
                debug_checks(c, image, label, result, args.without_nrst)
            print(f'{label}: PASS', flush=True)
        report['complete'] = args.without_nrst or all(r['nrstAndResetTarget']['passed'] for r in report['transports'].values())
        if not report['complete']: report['pending'] = 'NRST target reset has not passed; see per-transport pin/command results'
    except BaseException as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if modified:
            print('Restoring original 64 KiB main Flash through USB', flush=True)
            try:
                report['restoreOpenocd'] = openocd(args, 'usb_bulk', backup_path, 'restore')
                report['restoreChecks'] = {}
                for label in ('USB', 'WiFi'):
                    with (UsbClient() if label == 'USB' else TcpClient(args.host, 3241)) as c, SwdSession(c, 2000000) as s:
                        restored = main_flash(s)
                        require(restored == backup, f'{label}: original Flash restore mismatch')
                        options = s.read_words(0x1ffff800, 4)
                        require(options == report['optionBytesBefore'], 'Option bytes changed')
                        report['restoreChecks'][label] = dict(sha256=hashlib.sha256(restored).hexdigest(),
                                                            optionBytesUnchanged=True,
                                                            coreHalted=bool(s.read32(DHCSR) & 0x20000))
                report['restored'] = True
            except BaseException as exc:
                report['complete'] = False; report['restored'] = False
                report['restoreFailure'] = f'{type(exc).__name__}: {exc}'
                print(report['restoreFailure'], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Report: {args.output}', flush=True)
    require(report['complete'] and report.get('restored'), 'Acceptance/restore incomplete')


if __name__ == '__main__': main()
