"""Exercise the real installer, upgrades and uninstall with isolated application state.

Temporarily registers the test install in HKCU. Restores PATH/startup in finally,
refuses to replace an existing installer-managed installation, and never accesses
the user's normal connector state or targets. Run on an idle desktop/CI account.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
import winreg

APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E9B5FC27-7D26-4683-90D3-5387C315DFB0}_is1'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def registry_value(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None


def restore_value(key, name, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        if value is None:
            try:
                winreg.DeleteValue(handle, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(handle, name, 0, value[1], value[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    installer = args.installer.resolve()
    directory = args.directory.resolve()
    if directory.exists():
        parser.error('Preserve previous evidence: choose a new test directory')
    if registry_value(APP_KEY, 'InstallLocation'):
        parser.error('An installer-managed RMLink already exists; use a separate Windows test account')
    directory.mkdir(parents=True)
    application = directory / '安装目录 with spaces'
    profile = directory / 'isolated profile'
    profile.mkdir()
    state = profile / '.rmlink'
    state.mkdir()
    sentinel = state / 'preserved-user-data.txt'
    sentinel.write_text('Keep pairing and recordings', encoding='utf-8')
    environment = dict(os.environ, USERPROFILE=str(profile))
    # Use the OS-owned PowerShell module paths, not PowerShell 7's inherited path.
    environment.pop('PSModulePath', None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    old_path = registry_value('Environment', 'Path')
    old_startup = registry_value(RUN_KEY, 'RMLinkConnector')
    report = dict(complete=False, checks=[], installer=str(installer),
                  scope='real silent installer; isolated unpaired state and API port; no target or driver changes')
    prefix = [str(application / 'bin/rmlink.exe'), '--state-dir', str(state), '--api-port', str(port), '--json']

    def execute(command, timeout=90):
        result = subprocess.run(command, env=environment, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {command[0]}\n{result.stdout}{result.stderr}')
        return result.stdout

    def install(label, tasks):
        command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS',
                   '/LANG=chinesesimplified', '/DIR=' + str(application),
                   '/LOG=' + str(directory / (label + '.log'))]
        if tasks is not None:
            command.append('/TASKS=' + tasks)
        execute(command)
        assert registry_value(APP_KEY, 'InstallLocation')[0].rstrip('\\') == str(application)
        report['checks'].append(label)

    def validate_payload():
        manifest = json.loads((application / 'manifest.json').read_text(encoding='utf-8'))
        assert not manifest['kernel_driver_included']
        assert not list(application.rglob('*.sys'))
        for file in manifest['files']:
            assert hashlib.sha256((application / file['path']).read_bytes()).hexdigest() == file['sha256']
        report['version'] = manifest['version']
        report['file_count'] = len(manifest['files'])

    def service_running():
        return json.loads(execute([*prefix, 'service', 'status']))['running']

    def blocked_upgrade(service=False):
        # A local fixture represents an open console or a failed graceful stop.
        # It exits when stdin closes, so the test never kills a process.
        csc = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        source = directory / 'blocked-process.cs'
        source.write_text('class Fixture { static int Main(string[] args) { '
                          'if (args.Length > 0 && args[0] == "--block") { System.Console.ReadLine(); return 0; } '
                          'return 23; } }', encoding='utf-8')
        fixture = directory / 'blocked-process.exe'
        execute([str(csc), '/nologo', '/out:' + str(fixture), str(source)])
        names = ('rmlink.exe', 'RMLinkConnector.exe') if service else ('rmlink.exe',)
        for name in names:
            shutil.copyfile(application / 'bin' / name, directory / (name + '.backup'))
            shutil.copyfile(fixture, application / 'bin' / name)
        name = names[-1]
        process = subprocess.Popen([str(application / 'bin' / name), '--block', '--state-dir', str(state),
                                    '--api-port', str(port)], env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(.5)
            assert process.poll() is None
            result = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                     '/NOICONS', '/DIR=' + str(application),
                                     '/LOG=' + str(directory / ('blocked-service.log' if service else 'blocked-console.log'))],
                                    env=environment, capture_output=True, timeout=45,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode != 0, 'Upgrade ignored a running console or failed service shutdown'
            log = (directory / ('blocked-service.log' if service else 'blocked-console.log')).read_text(encoding='utf-8-sig')
            expected = 'Cannot stop RMLink' if service else 'Close the RMLink console'
            assert 'RMLink installation helper: ' + expected in log, 'Upgrade failed outside its process-safety check'
            assert process.poll() is None, 'Upgrade terminated the running process'
            assert (application / 'bin' / name).read_bytes() == fixture.read_bytes(), 'Upgrade replaced files before preflight passed'
            report['checks'].append('upgrade_refuses_failed_service_stop' if service else 'upgrade_refuses_open_console')
        finally:
            process.communicate(b'\n', timeout=15)
            for name in names:
                shutil.copyfile(directory / (name + '.backup'), application / 'bin' / name)

    try:
        install('fresh_install_defaults', None)
        validate_payload()
        assert not service_running(), 'Silent install unexpectedly started a service'
        report['checks'].append('default_install_does_not_start_service')
        install('enable_optional_tasks', 'addtopath,startlogon')
        validate_payload()
        assert str(application / 'bin') in registry_value('Environment', 'Path')[0].split(';')
        assert str(application / 'Start-Service.ps1') in registry_value(RUN_KEY, 'RMLinkConnector')[0]
        report['checks'].append('path_and_startup_registered')
        assert not service_running(), 'Silent install unexpectedly started a service'
        json.loads(execute([*prefix, 'service', 'start']))
        assert service_running()
        report['checks'].append('installed_client_starts_isolated_service')
        # Upgrade must stop this directory's service at its actual custom port.
        install('upgrade_running_idle_service', 'addtopath,startlogon')
        assert not service_running()
        assert sentinel.read_text(encoding='utf-8') == 'Keep pairing and recordings'
        validate_payload()
        report['checks'].append('upgrade_preserves_data_and_payload')
        blocked_upgrade()
        blocked_upgrade(service=True)
        validate_payload()
        install('repair_without_optional_tasks', '')
        assert str(application / 'bin') not in (registry_value('Environment', 'Path') or ('',))[0].split(';')
        assert registry_value(RUN_KEY, 'RMLinkConnector') is None
        report['checks'].append('optional_registration_removed')
        # Other software's registration must survive RMLink's uninstaller.
        unrelated = '"C:\\unrelated application\\Start-Service.ps1"'
        restore_value(RUN_KEY, 'RMLinkConnector', (unrelated, winreg.REG_SZ))
        extra = application / 'my-extra-file.txt'
        extra.write_text('User-owned extra file', encoding='utf-8')
        json.loads(execute([*prefix, 'service', 'start']))
        execute([str(application / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                 '/LOG=' + str(directory / 'uninstall.log')])
        deadline = time.monotonic() + 20
        while (application / 'bin/rmlink.exe').exists() and time.monotonic() < deadline:
            time.sleep(.2)
        assert not (application / 'bin/rmlink.exe').exists()
        assert not registry_value(APP_KEY, 'InstallLocation')
        assert sentinel.exists() and extra.exists()
        assert registry_value(RUN_KEY, 'RMLinkConnector')[0] == unrelated
        report['checks'].append('uninstall_stops_service_preserves_user_data_and_unrelated_registration')
        report['complete'] = True
    finally:
        # Cleanup only this test's service and install. Never kill a process.
        if (application / 'bin/rmlink.exe').is_file():
            subprocess.run([*prefix, 'service', 'stop'], env=environment, capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if (application / 'unins000.exe').is_file():
            subprocess.run([str(application / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                           env=environment, capture_output=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        restore_value('Environment', 'Path', old_path)
        restore_value(RUN_KEY, 'RMLinkConnector', old_startup)
        notify = ctypes.WinDLL('user32').SendNotifyMessageW
        notify.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p]
        notify(0xffff, 0x1a, 0, 'Environment')
        (directory / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
