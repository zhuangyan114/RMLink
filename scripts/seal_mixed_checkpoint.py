"""Seal the 72 MHz mixed-variable acceptance; never accesses a physical target."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'docs/mixed-subscription-checkpoint-0228.json'
    if output.exists():
        raise RuntimeError('Preserve the existing checkpoint')

    def read(name):
        return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))

    def identity(name):
        path = ROOT / name
        return dict(path=name, bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    short_name = 'artifacts/logs/mixed-focus-0228-72.json'
    soak_name = 'artifacts/logs/mixed-72-0228-900.json'
    usb_name = 'artifacts/logs/standard-usb-0228.json'
    accept_name = 'artifacts/logs/accept-virtual-usb-0228.json'
    short, soak, usb, accept = (read(name) for name in (short_name, soak_name, usb_name, accept_name))
    if not all(item.get('complete') is True for item in (short, soak, usb, accept)):
        raise RuntimeError('A required acceptance did not complete')
    if not accept['finalStateVerified'] or not accept['optionBytesUnchanged'] or accept['coreHaltedAtEnd']:
        raise RuntimeError('Standard Flash/debug acceptance left an invalid target state')
    if accept['finalFlashSha256'] != usb['fixtureSha256'] or accept['openocdProgram']['errorLines']:
        raise RuntimeError('Standard Flash/debug regression failed')
    if (soak['pointerFields'], soak['pointerDepth'], soak['directFields'], soak['shared'],
            soak['requestedPeriodUs'], soak['requestedClockHz']) != (5, 3, 5, False, 200, 4000000):
        raise RuntimeError('Different variable layout or requested timing')
    if soak['targetClock']['systemClockHz'] != 72000000 or soak['elapsedSeconds'] < 900:
        raise RuntimeError('Wrong target clock or insufficient duration')
    if short['medianHz'] < 1000 or soak['live']['kinds']['1']['effectiveHz'] < 1000:
        raise RuntimeError('The 1000 Hz throughput goal was not reached')
    if soak['consumerGap'] or soak['live']['firstInvalidSample'] is not None or soak['disk']['firstInvalidSample'] is not None:
        raise RuntimeError('A live or recorded sample failed validation')
    if soak['live']['rttProducerSkipped'] or soak['disk']['rttProducerSkipped']:
        raise RuntimeError('The fixture producer skipped RTT data')
    if any(s['failures'] or s['ring_dropped'] for s in soak['finalStatuses'].values()):
        raise RuntimeError('Board-side data loss')
    if not all(soak['liveDynamicFieldChanges']['1']) or not all(soak['diskDynamicFieldChanges']['1']):
        raise RuntimeError('A field remained frozen')
    recording = soak['recording']
    if not recording['finalized'] or recording['phase'] != 'complete' or recording['invalid_data_samples']:
        raise RuntimeError('Recording was not finalized successfully')
    if recording['sha256'] != soak['independentFileValidation']['sha256']:
        raise RuntimeError('Independent recording hash mismatch')
    timing_name = 'artifacts/logs/mixed-72-0228-900-timing.json'
    timing = read(timing_name)
    if timing['recordingSha256'] != recording['sha256'] or timing['frames'] != recording['frames']:
        raise RuntimeError('Timing audit refers to a different recording')

    receipt = read('artifacts/hardware/rmlink-last-flash.json')
    firmware = 'artifacts/build/' + receipt['profile']
    binary, config = identity(firmware + '/rmlink.bin'), identity(firmware + '/sdkconfig')
    if binary['sha256'] != receipt['binarySha256'].lower() or config['sha256'] != receipt['sdkconfigSha256'].lower():
        raise RuntimeError('Build artifacts differ from the physically flashed image')
    description = read(firmware + '/project_description.json')
    if description['project_version'] != '0.2.28':
        raise RuntimeError('This checkpoint qualifies firmware 0.2.28 only')
    source_base = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    changed = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', 'HEAD']).decode().splitlines()
    changed += subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--others', '--exclude-standard']).decode().splitlines()
    overlay = [identity(name) for name in sorted(set(changed)) if name.startswith(('firmware/', 'host/', 'scripts/', 'tests/'))]
    report = dict(capturedAt=datetime.now().astimezone().isoformat(), firmwareVersion='0.2.28',
        connectorVersion='0.2.8', sourceBaseCommit=source_base, sourceOverlay=overlay,
        targetClock=soak['targetClock'], requestedSwjClockHz=4000000,
        fixtureSha256=usb['fixtureSha256'], shortMedianHz=short['medianHz'],
        sustainedSeconds=soak['elapsedSeconds'], sustainedHz=soak['live']['kinds']['1']['effectiveHz'],
        recordingTiming=timing,
        recording=recording, independentRecordingValidation=soak['independentFileValidation'],
        healthAfter=soak['healthAfter'], finalStatuses=soak['finalStatuses'],
        artifacts=[binary, config, identity(firmware + '/rmlink.elf'),
                   identity('artifacts/hardware/rmlink-last-flash.json'),
                   identity('artifacts/build/connector/dist/RMLinkConnector.exe')],
        evidence=[identity(name) for name in (short_name, soak_name, usb_name, accept_name, timing_name,
                  'artifacts/logs/mixed-72-0228-short-uart.log',
                  'artifacts/logs/mixed-72-0228-900-uart.log', 'artifacts/logs/host-tests-0228-final.log',
                  'artifacts/logs/capture-model-0228.log', 'artifacts/logs/core512-model-0228.log',
                  'artifacts/logs/flash-model-0228.log', 'artifacts/logs/build-0228-tcp64.log')],
        boundaries=['Five independent depth-three chains plus five direct uint32 fields only',
                    'Three nodes within each chain are adjacent; chains and leaves are 64 B apart',
                    'Pointer locations fixed; all ten leaf values change',
                    '5000 Hz is requested; missed deadlines and timing jitter remain',
                    'Network send stalls remain observable; 15 minutes is not an indefinite guarantee',
                    'Physical NRST, installed VS Code UI, Keil/IAR and formal driver signing not qualified here'])
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
