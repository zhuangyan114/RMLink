"""User-visible OTA confirmation, cancellation and transfer feedback."""
import base64
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ota_fixture import application_image
from rmlink_connector.cli import upgrade_firmware, ui_action
from rmlink_connector.console import Terminal
from rmlink_connector.firmware import inspect_image


class OtaUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'rmlink-0.3.1.bin'
        self.data = application_image(payload=b'experience' * 1000)
        self.path.write_bytes(self.data)
        self.image = inspect_image(self.path)
        self.output = io.StringIO()
        self.terminal = Terminal(no_color=True, stream=self.output)
        self.terminal.tty = True
        self.terminal.size = lambda: os.terminal_size((100, 28))
        self.calls, self.uploaded = [], bytearray()
        self.finished, self.boot_polls, self.fail_write = False, 0, False
        self.controller = SimpleNamespace(args=SimpleNamespace(json=False), call=self.call,
                                          select=lambda: dict(device_id='probe'))

    def call(self, method, path, body=None):
        self.calls.append((method, body))
        if method == 'GET':
            if self.finished:
                self.boot_polls += 1
            return dict(supported=True, version='0.3.0' if not self.finished else '0.3.1',
                        profile=self.image['profile'], max_bytes=3*1024*1024,
                        elf_sha256=self.image['elf_sha256'] if self.finished else '0'*64,
                        boot_state=2 if self.boot_polls >= 2 else 1)
        action = body['action']
        if action == 'begin':
            self.assertIn('0 %', self.output.getvalue())
            return dict(upload_id='upload')
        if action == 'write':
            if self.fail_write:
                raise ConnectionError('transport lost')
            self.assertEqual(body['offset'], len(self.uploaded))
            self.uploaded.extend(base64.b64decode(body['data']))
            return dict(received=len(self.uploaded))
        if action == 'finish':
            self.assertIn('正在校验固件', self.output.getvalue())
            self.finished = True
            return dict(state='rebooting')
        return dict(state='aborted')

    def test_information_page_enter_installs_and_shows_every_stage(self):
        with patch.object(self.terminal, 'key', return_value='enter'), patch('rmlink_connector.cli.time.sleep'):
            result = upgrade_firmware(self.controller, self.terminal, file=self.path)
        text = self.output.getvalue()
        self.assertIn('Enter 确认安装并重启 RMLink', text)
        self.assertIn('q / Esc 取消', text)
        self.assertNotIn('Enter 返回', text)
        self.assertIn('0.3.0 → 0.3.1', text)
        for value in ('0 %', '100 %', '正在上传固件', '正在校验固件', '等待设备重连并确认新固件'):
            self.assertIn(value, text)
        self.assertEqual(bytes(self.uploaded), self.data)
        self.assertEqual(result['state'], 'boot_confirmed')
        self.assertEqual(self.boot_polls, 2)  # Matching ELF without VALID must keep waiting.

    def test_q_and_escape_cancel_without_starting_upload(self):
        for key in ('q', '\x1b'):
            with self.subTest(key=key):
                self.calls.clear()
                with patch.object(self.terminal, 'key', return_value=key):
                    result = upgrade_firmware(self.controller, self.terminal, file=self.path)
                self.assertEqual(result['state'], 'cancelled')
                self.assertTrue(all(method == 'GET' for method, _ in self.calls))

    def test_import_route_keeps_information_pause_and_needs_one_install_enter(self):
        with patch.object(self.terminal, 'menu', side_effect=['i', None]) as menu, patch.object(
                self.terminal, 'prompt', return_value=str(self.path)), patch.object(
                self.terminal, 'key', side_effect=['enter', 'enter']) as key, patch('rmlink_connector.cli.time.sleep'):
            ui_action(5, self.controller, self.terminal)
        self.assertEqual(key.call_count, 2)  # Install confirmation, then success-page return.
        self.assertEqual(menu.call_count, 2)  # No second hidden install menu.
        self.assertIn('安装成功', self.output.getvalue())
        self.assertIn('新固件启动确认通过', self.output.getvalue())

    def test_transfer_error_aborts_once_and_never_finishes(self):
        self.fail_write = True
        with patch.object(self.terminal, 'key', return_value='enter'):
            with self.assertRaisesRegex(ConnectionError, 'transport lost'):
                upgrade_firmware(self.controller, self.terminal, file=self.path)
        actions = [body['action'] for method, body in self.calls if method == 'POST']
        self.assertEqual(actions, ['begin', 'write', 'abort'])

    def test_json_update_does_not_emit_interactive_progress(self):
        self.controller.args.json = True
        self.call = lambda *args: None
        # Keep the same API behavior, but omit UI assertions for noninteractive calls.
        def api(method, path, body=None):
            if method == 'GET':
                return dict(supported=True, version='0.3.0', profile=self.image['profile'],
                            max_bytes=3*1024*1024, elf_sha256='0'*64)
            if body['action'] == 'begin':
                return dict(upload_id='upload')
            if body['action'] == 'write':
                return dict(received=body['offset'] + len(base64.b64decode(body['data'])))
            return dict(state='rebooting')
        self.controller.call = api
        result = upgrade_firmware(self.controller, self.terminal, file=self.path, yes=True, wait=False)
        self.assertEqual(result['state'], 'rebooting')
        self.assertEqual(self.output.getvalue(), '')
