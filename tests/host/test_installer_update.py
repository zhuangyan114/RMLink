"""Tests for GitHub Windows installer discovery, SHA256 validation, CLI update commands and TUI integration."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from rmlink_connector.cli import main, shell, ui_action
from rmlink_connector.console import Terminal
from rmlink_connector.firmware import FirmwareError
from rmlink_connector.installer import (
    InstallerError,
    download_installer,
    latest_installer_release,
    launch_installer,
    parse_sha256_text,
)
from rmlink_connector.tui import ExitForUpdate


class FakeResponse:
    def __init__(self, data, url='https://github.com/zhuangyan114/RMLink/releases/tag/v0.4.0'):
        self._stream = io.BytesIO(data if isinstance(data, bytes) else data.encode('utf-8'))
        self._url = url
        self.headers = {'Content-Length': str(len(self._stream.getvalue()))}

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class InstallerUpdateTests(unittest.TestCase):
    def test_parse_sha256_text_validates_format_and_filename(self):
        digest = 'a' * 64
        self.assertEqual(parse_sha256_text(f'{digest.upper()}  RMLink-0.4.0-windows-x64-setup.exe\n',
                                           'RMLink-0.4.0-windows-x64-setup.exe'), digest)
        for bad in ('invalid', f'{digest}  other.exe', f'{digest}  a.exe\n{digest}  a.exe'):
            with self.subTest(bad=bad), self.assertRaises(InstallerError):
                parse_sha256_text(bad, 'RMLink-0.4.0-windows-x64-setup.exe')

    def test_latest_installer_release_prefers_signed_over_unsigned_and_ignores_preview(self):
        repo = 'zhuangyan114/RMLink'
        base = f'https://github.com/{repo}/releases/download/v0.9.0/'
        signed_name = 'RMLink-0.9.0-windows-x64-setup.exe'
        unsigned_name = 'RMLink-0.9.0-windows-x64-setup-unsigned.exe'
        preview_name = 'RMLink-0.9.0-windows-x64-setup-unsigned-preview.exe'
        digest = '1' * 64
        release_json = dict(
            tag_name='v0.9.0',
            draft=False,
            prerelease=False,
            published_at='2026-11-21T00:00:00Z',
            assets=[
                dict(name=preview_name, size=4096, browser_download_url=base + preview_name),
                dict(name=unsigned_name, size=4096, browser_download_url=base + unsigned_name),
                dict(name=unsigned_name[:-4] + '.sha256', size=100, browser_download_url=base + unsigned_name[:-4] + '.sha256'),
                dict(name=signed_name, size=4096, browser_download_url=base + signed_name),
                dict(name=signed_name[:-4] + '.sha256', size=100, browser_download_url=base + signed_name[:-4] + '.sha256'),
            ],
        )

        def fake_open(url):
            if url.endswith('/releases/latest'):
                return FakeResponse(json.dumps(release_json))
            if url.endswith(signed_name[:-4] + '.sha256'):
                return FakeResponse(f'{digest}  {signed_name}\n')
            raise AssertionError('Unexpected URL: ' + url)

        with patch('rmlink_connector.installer.open_github', side_effect=fake_open), patch(
                'rmlink_connector.firmware.open_github', side_effect=fake_open):
            info = latest_installer_release(repo)
        self.assertEqual(info['file'], signed_name)
        self.assertTrue(info['signed'])
        self.assertTrue(info['update_available'])
        self.assertEqual(info['sha256'], digest)

    def test_latest_installer_release_falls_back_on_rate_limit(self):
        repo = 'zhuangyan114/RMLink'
        unsigned_name = 'RMLink-0.9.1-windows-x64-setup-unsigned.exe'
        digest = '2' * 64

        def fake_open(url):
            if 'api.github.com' in url:
                raise FirmwareError('rate limit', http_status=403)
            if url == f'https://github.com/{repo}/releases/latest':
                return FakeResponse('', url=f'https://github.com/{repo}/releases/tag/v0.9.1')
            if url.endswith('setup.sha256'):
                raise FirmwareError('not found', http_status=404)
            if url.endswith('setup-unsigned.sha256'):
                return FakeResponse(f'{digest}  {unsigned_name}\n')
            if url.endswith('setup-unsigned.json'):
                return FakeResponse(json.dumps(dict(version='0.9.1', signed=False)))
            raise AssertionError('Unexpected URL: ' + url)

        with patch('rmlink_connector.installer.open_github', side_effect=fake_open), patch(
                'rmlink_connector.firmware.open_github', side_effect=fake_open):
            info = latest_installer_release(repo)
        self.assertEqual(info['version'], '0.9.1')
        self.assertEqual(info['file'], unsigned_name)
        self.assertFalse(info['signed'])
        self.assertEqual(info['sha256'], digest)

    def test_download_installer_verifies_sha256_caches_and_rejects_corruption(self):
        payload = b'MZ' + b'rmlink-installer-test' * 200
        digest = hashlib.sha256(payload).hexdigest()
        release = dict(
            repository='zhuangyan114/RMLink',
            version='0.9.0',
            tag='v0.9.0',
            prerelease=False,
            file='RMLink-0.9.0-windows-x64-setup-unsigned.exe',
            sha256=digest,
            size=len(payload),
            url='https://github.com/zhuangyan114/RMLink/releases/download/v0.9.0/RMLink-0.9.0-windows-x64-setup-unsigned.exe',
            signed=False,
            current_version='0.3.5',
            update_available=True,
            up_to_date=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            progress_events = []
            with patch('rmlink_connector.installer.open_github', return_value=FakeResponse(payload)) as opener:
                first = download_installer(release, tmp, progress=lambda r, t: progress_events.append((r, t)))
            self.assertFalse(first['cached'])
            self.assertTrue(Path(first['path']).is_file())
            self.assertEqual(progress_events[-1], (len(payload), len(payload)))
            opener.assert_called_once()

            # Second download reuses verified cache without network.
            with patch('rmlink_connector.installer.open_github', side_effect=AssertionError('Should use cache')):
                second = download_installer(release, tmp)
            self.assertTrue(second['cached'])
            self.assertEqual(second['path'], first['path'])

            # Corrupted download is rejected and cleaned up.
            bad_release = dict(release, file='bad.exe', sha256='0' * 64)
            with patch('rmlink_connector.installer.open_github', return_value=FakeResponse(payload)):
                with self.assertRaises(InstallerError):
                    download_installer(bad_release, tmp)
            self.assertFalse((Path(tmp) / 'bad.exe').exists())
            self.assertEqual(list(Path(tmp).glob('*.part')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows detached installer launch')
    def test_launch_installer_passes_path_via_env_and_respects_wizard_or_silent_mode(self):
        payload = b'MZ' + b'x' * 2048
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'RMLink-0.9.0-windows-x64-setup-unsigned.exe'
            exe.write_bytes(payload)
            with patch('rmlink_connector.installer.subprocess.Popen') as popen:
                result = launch_installer(exe, expected_sha256=digest, silent=False, wait_pid=12345)
            self.assertEqual(result['state'], 'installer_launched')
            self.assertFalse(result['silent'])
            args, kwargs = popen.call_args
            self.assertEqual(kwargs['env']['RMLINK_INSTALLER_PATH'], str(exe.resolve()))
            script = base64.b64decode(args[0][-1]).decode('utf-16-le')
            self.assertIn('Wait-Process', script)
            self.assertIn('Start-Process -FilePath $env:RMLINK_INSTALLER_PATH', script)
            self.assertIn('/LANG=chinesesimplified', script)
            self.assertNotIn('/VERYSILENT', script)

            with patch('rmlink_connector.installer.subprocess.Popen') as popen:
                result = launch_installer(exe, expected_sha256=digest, silent=True, wait_pid=12345)
            self.assertTrue(result['silent'])
            script = base64.b64decode(popen.call_args.args[0][-1]).decode('utf-16-le')
            self.assertIn('/VERYSILENT', script)

            # Tampered file before launch must be rejected.
            exe.write_bytes(b'MZ' + b'y' * 2048)
            with self.assertRaises(InstallerError):
                launch_installer(exe, expected_sha256=digest)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell handoff')
    def test_hidden_handoff_runs_powershell_and_logs_start_failure(self):
        with tempfile.TemporaryDirectory(prefix='安装包 ') as tmp:
            exe = Path(tmp) / 'setup.exe'
            exe.write_bytes(b'MZ' + b'x' * 2048)
            start_process = subprocess.Popen
            helper = None

            def remove_verified_installer_then_start(*args, **kwargs):
                nonlocal helper
                exe.unlink()
                helper = start_process(*args, **kwargs)
                return helper

            with patch('rmlink_connector.installer.subprocess.Popen', side_effect=remove_verified_installer_then_start):
                result = launch_installer(exe, expected_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                                          wait_pid=0x7fffffff)
            with helper:
                self.assertEqual(helper.wait(timeout=20), 1)
            log = Path(result['log']).read_text(encoding='utf-8')
            self.assertTrue(log.strip())
            self.assertNotIn('Installer process started', log)

    def test_cli_update_subcommands_validate_and_block_active_debug_sessions(self):
        with contextlib.redirect_stderr(io.StringIO()), patch('rmlink_connector.local_service.LocalService.start') as start:
            with self.assertRaises(SystemExit) as error:
                main(['update'])
            self.assertEqual(error.exception.code, 2)
            start.assert_not_called()

        release = dict(
            repository='zhuangyan114/RMLink', version='0.9.0', tag='v0.9.0', prerelease=False,
            file='RMLink-0.9.0-windows-x64-setup-unsigned.exe', sha256='3' * 64, size=4096,
            url='https://github.com/zhuangyan114/RMLink/releases/download/v0.9.0/RMLink-0.9.0-windows-x64-setup-unsigned.exe',
            signed=False, current_version='0.3.5', update_available=True, up_to_date=False,
        )
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start', side_effect=AssertionError('Must not start service')), patch(
                'rmlink_connector.cli.latest_installer_release', return_value=release), contextlib.redirect_stdout(out):
            self.assertEqual(main(['update', 'check', '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['version'], '0.9.0')

        # Non-interactive install requires --yes.
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.status', return_value=None), patch(
                'rmlink_connector.tui.latest_installer_release', return_value=release), contextlib.redirect_stdout(out):
            self.assertEqual(main(['update', 'install', '--json']), 2)
        self.assertIn('--yes', json.loads(out.getvalue())['error'])

        # Active debug session refuses installer launch.
        out = io.StringIO()
        busy_status = dict(devices=[dict(device_id='aaaa', alias='工作台', owner='session-1')])
        with patch('rmlink_connector.local_service.LocalService.status', return_value=busy_status), patch(
                'rmlink_connector.tui.latest_installer_release', return_value=release), contextlib.redirect_stdout(out):
            self.assertEqual(main(['update', 'install', '--yes', '--json']), 3)
        self.assertIn('活动调试会话', json.loads(out.getvalue())['error'])

    def test_tui_firmware_menu_launches_chinese_wizard_and_exits_shell_cleanly(self):
        release = dict(
            repository='zhuangyan114/RMLink', version='0.9.0', tag='v0.9.0', prerelease=False,
            file='RMLink-0.9.0-windows-x64-setup-unsigned.exe', sha256='4' * 64, size=2 * 1024 * 1024,
            url='https://github.com/zhuangyan114/RMLink/releases/download/v0.9.0/RMLink-0.9.0-windows-x64-setup-unsigned.exe',
            signed=False, current_version='0.3.5', update_available=True, up_to_date=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            controller = MagicMock()
            controller.args = MagicMock(json=False)
            controller.runtime.directory = Path(tmp)
            controller.runtime.status.return_value = None
            controller.preferences.return_value = {}
            terminal = Terminal(no_color=True, stream=io.StringIO())
            terminal.tty = True
            terminal.size = lambda: os.terminal_size((100, 28))

            # Cancel at confirmation page -> never downloads or launches.
            with patch.object(terminal, 'key', side_effect=['5', 'q', 'q']), patch(
                    'rmlink_connector.tui.latest_installer_release', return_value=release), patch(
                    'rmlink_connector.tui.download_installer', side_effect=AssertionError('Should not download')):
                ui_action(4, controller, terminal)
            self.assertIn('确认下载并启动中文安装向导', terminal.stream.getvalue())

            # Confirm with Enter -> downloads, launches wizard (silent=False), and raises ExitForUpdate.
            downloaded = dict(release, path=str(Path(tmp) / release['file']), bytes=4096, cached=False)
            with patch.object(terminal, 'key', side_effect=['5', 'enter']), patch(
                    'rmlink_connector.tui.latest_installer_release', return_value=release), patch(
                    'rmlink_connector.tui.download_installer', return_value=downloaded), patch(
                    'rmlink_connector.tui.launch_installer', return_value=dict(
                        state='installer_launched', path=downloaded['path'], silent=False,
                        message='中文安装向导将在控制台退出后自动打开')) as launch:
                with self.assertRaises(ExitForUpdate):
                    ui_action(4, controller, terminal)
            launch.assert_called_once_with(downloaded['path'], expected_sha256=downloaded['sha256'], silent=False)

            # Shell catches ExitForUpdate and exits cleanly with status 0.
            controller.runtime.status.return_value = None
            out = io.StringIO()
            with patch.object(terminal, 'screen', return_value=contextlib.nullcontext()), patch.object(
                    terminal, 'key', side_effect=['5']), patch(
                    'rmlink_connector.tui.ui_action', side_effect=ExitForUpdate('中文安装向导即将打开')), contextlib.redirect_stdout(out):
                self.assertEqual(shell(controller, terminal), 0)
            self.assertIn('中文安装向导即将打开', out.getvalue())
