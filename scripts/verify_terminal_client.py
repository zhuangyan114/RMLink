"""Exercise the built terminal client with isolated state and no target writes."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'artifacts/build/connector/dist/rmlink.exe')
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / 'acceptance.json'
    if report_path.exists():
        raise RuntimeError('Preserve existing evidence; use a new directory')
    state = directory / 'isolated state'
    api = free_port()
    prefix = [str(args.exe.resolve()), '--state-dir', str(state), '--api-port', str(api)]
    report = dict(complete=False, scope='frozen CLI and local API; isolated unpaired state; no Wi-Fi credential writes, kernel driver changes or target writes', checks=[])
    outputs = []
    def run(*command, success=0):
        result = subprocess.run([*prefix, *command, '--json'], capture_output=True, encoding='utf-8', timeout=40)
        outputs.append(result.stdout + result.stderr)
        if result.returncode != success:
            raise RuntimeError('CLI failed: ' + ' '.join(command) + '\n' + result.stderr + result.stdout)
        value = json.loads(result.stdout)
        report['checks'].append(dict(command=list(command), exit_code=result.returncode))
        return value
    def pid():
        command = f'(Get-NetTCPConnection -LocalPort {api} -State Listen -ErrorAction Stop).OwningProcess | ConvertTo-Json -Compress'
        result = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', command], encoding='utf-8')
        return json.loads(result)
    try:
        launchers = [subprocess.Popen([*prefix, 'service', 'start', '--json'], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, encoding='utf-8') for _ in range(2)]
        for launcher in launchers:
            stdout, stderr = launcher.communicate(timeout=40)
            outputs.append(stdout + stderr)
            if launcher.returncode or not json.loads(stdout).get('running'):
                raise RuntimeError('Concurrent startup failed: ' + stderr + stdout)
        report['concurrent_start'] = True
        report['start'] = run('service', 'start')
        first_pid = pid()
        run('service', 'start')
        if pid() != first_pid:
            raise RuntimeError('A second service replaced the first process')
        report['service_pid'] = first_pid
        devices = run('devices')
        report['discovered_count'] = len(devices['devices'])
        report['hotspot'] = run('wifi', 'hotspot')
        report['diagnostics'] = run('doctor', '--output', str(directory / 'diagnostics.json'))
        run('logs', '--lines', '10')
        capabilities = run('status')
        if any(d['paired'] or d['owner'] for d in capabilities['devices']):
            raise RuntimeError('The isolated client changed pairing or target ownership')
        token = json.loads((state / 'connector.json').read_text(encoding='utf-8'))['token']
        exported = (directory / 'diagnostics.json').read_text(encoding='utf-8')
        if any(token in output for output in [*outputs, exported]):
            raise RuntimeError('Local credentials leaked into public output')
        report['stop'] = run('service', 'stop')
        report['complete'] = True
    finally:
        try:
            run('service', 'stop')
        except Exception:
            report['cleanup_failed'] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(complete=report['complete'], report=str(report_path), checks=len(report['checks'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
