import contextlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from ota_fixture import application_image

from rmlink_connector.cli import main, parser, Controller, shell, ui_action
from rmlink_connector.console import MENU, dashboard, cell_width, fit, safe_text, Terminal
from rmlink_connector.local_service import ClientError, LocalService, inspect_firmware


class TerminalClientTests(unittest.TestCase):
    def test_removed_commands_are_rejected_before_starting_the_service(self):
        for command in (['mount'], ['unmount'], ['driver', 'status'], ['--usbip-port', '3240', 'status']):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), patch(
                    'rmlink_connector.local_service.LocalService.start') as start:
                with self.assertRaises(SystemExit) as error:
                    main(command)
                self.assertEqual(error.exception.code, 2)
                start.assert_not_called()

    def test_seven_item_navigation_wraps_to_settings_and_skips_the_empty_column(self):
        for keys, expected in ((['up', 'enter', 'q'], 6),
                               (['down', 'down', 'down', 'right', 'enter', 'q'], 3),
                               (['right', 'left', 'enter', 'q'], 0)):
            controller, terminal = MagicMock(), MagicMock()
            controller.runtime.status.return_value = None
            terminal.size.return_value = os.terminal_size((80, 32))
            terminal.screen.return_value = contextlib.nullcontext()
            terminal.key.side_effect = keys
            with patch('rmlink_connector.cli.dashboard', return_value=[]), patch('rmlink_connector.cli.ui_action') as action:
                self.assertEqual(shell(controller, terminal), 0)
            action.assert_called_once_with(expected, controller, terminal)

    def test_log_location_is_shared_when_a_packaged_host_redirects_localappdata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, USERPROFILE=directory, LOCALAPPDATA=str(Path(directory)/'package cache')):
                packaged = LocalService()
            with patch.dict(os.environ, USERPROFILE=directory, LOCALAPPDATA=str(Path(directory)/'ordinary appdata')):
                terminal = LocalService()
            self.assertEqual(packaged.logs, terminal.logs)
            self.assertEqual(packaged.logs, Path(directory)/'.rmlink'/'logs')

    def test_script_options_work_at_each_command_level(self):
        for args in [['--json', '--device', 'abc', 'wifi', 'status'],
                     ['wifi', '--json', '--device', 'abc', 'status'],
                     ['wifi', 'status', '--json', '--device', 'abc']]:
            parsed = parser().parse_args(args)
            self.assertTrue(parsed.json)
            self.assertEqual(parsed.device, 'abc')

    def test_offline_firmware_check_never_initializes_service_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.bin'
            data = bytearray(application_image())
            path.write_bytes(data)
            out = io.StringIO()
            with patch('rmlink_connector.local_service.LocalService.start', side_effect=AssertionError('Started service')), contextlib.redirect_stdout(out):
                code = main(['firmware', 'inspect', str(path), '--json'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())['bytes'], len(data))
            data[12] = 0
            path.write_bytes(data)
            with self.assertRaises(ClientError):
                inspect_firmware(path)

    def test_firmware_update_requires_an_explicit_source_before_accessing_device(self):
        out = io.StringIO()
        device = dict(device_id='50787d15a924')
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(Controller, 'select', return_value=device), patch.object(
                Controller, 'call', return_value=dict(supported=False, message='升级接口已预留')) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['firmware', 'update', '--json']), 2)
        call.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['exit_code'], 2)

    def test_narrow_dashboard_has_no_oversized_rows_and_removes_remote_control_characters(self):
        device = dict(device_id='50787d15a924', hostname='工作台\x1b[2J', firmware='0.2.40', connected=True)
        for width in (32, 60, 80, 100):
            rows = dashboard(dict(version='0.2.15'), device, width=width)
            output=io.StringIO()
            terminal=Terminal(no_color=True,stream=output)
            with patch.object(terminal,'size',return_value=os.terminal_size((width,28))):
                terminal.draw(rows)
            self.assertTrue(all(cell_width(line)<=width-2 for line in output.getvalue().splitlines()))
            self.assertNotIn('\x1b',output.getvalue())
        self.assertNotIn('\x1b', safe_text(device['hostname']))
        self.assertEqual(cell_width('设备 A'), 6)

    def test_preview_and_no_color_work_without_terminal_or_service(self):
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.status', return_value=None), contextlib.redirect_stdout(out):
            self.assertEqual(main(['--preview', '--no-color']), 0)
        self.assertIn('████████', out.getvalue())
        self.assertNotIn('\x1b', out.getvalue())

    def test_only_selected_menu_item_is_blue_in_both_layouts(self):
        for width in (60, 80):
            for selected, label in enumerate(MENU):
                with self.subTest(width=width, selected=selected):
                    output = io.StringIO()
                    terminal = Terminal(stream=output)
                    terminal.color = True
                    with patch.object(terminal, 'size', return_value=os.terminal_size((width, 32))):
                        terminal.draw(dashboard(None, selected=selected, width=width))
                    blue = '\x1b[' + Terminal.COLORS['selected'] + 'm'
                    highlighted = re.findall(re.escape(blue) + '(.*?)\x1b\\[0m', output.getvalue())
                    self.assertEqual(len(highlighted), 1)
                    self.assertIn(label, highlighted[0])
                    self.assertTrue(all(other not in highlighted[0] for other in MENU if other != label))

    def test_horizontal_navigation_changes_columns_and_enter_opens_selected_item(self):
        for width, target in ((80, 5), (60, 2)):
            with self.subTest(width=width):
                controller, terminal = MagicMock(), MagicMock()
                controller.runtime.status.return_value = None
                terminal.size.return_value = os.terminal_size((width, 32))
                terminal.screen.return_value = contextlib.nullcontext()
                terminal.key.side_effect = ['down', 'right', 'enter', 'left', 'q']
                with patch('rmlink_connector.cli.dashboard', return_value=[]) as draw, patch('rmlink_connector.cli.ui_action') as action:
                    self.assertEqual(shell(controller, terminal), 0)
                self.assertEqual([call.args[4] for call in draw.call_args_list], [0, 1, target, target, 1])
                action.assert_called_once_with(target, controller, terminal)

    @unittest.skipUnless(os.name == 'nt', 'Windows console key codes')
    def test_windows_horizontal_keys_accept_native_and_vt_sequences(self):
        terminal = Terminal(stream=io.StringIO())
        for sequence, expected in (('\xe0K', 'left'), ('\x00M', 'right'),
                                   ('\x1b[D', 'left'), ('\x1b[C', 'right'),
                                   ('\x1bOD', 'left'), ('\x1bOC', 'right')):
            with self.subTest(sequence=sequence), patch('msvcrt.kbhit', return_value=True), patch('msvcrt.getwch', side_effect=sequence):
                self.assertEqual(terminal.key(), expected)

    def test_wordmark_preserves_menu_and_help_in_an_80_by_24_terminal(self):
        for width, height, large in ((80, 24, True), (60, 24, False), (60, 28, False)):
            with self.subTest(width=width, height=height):
                output = io.StringIO()
                terminal = Terminal(no_color=True, stream=output)
                with patch.object(terminal, 'size', return_value=os.terminal_size((width, height))):
                    terminal.draw(dashboard(None, width=width, height=height, message='已就绪'))
                text = output.getvalue()
                self.assertEqual('████████' in text, large)
                self.assertTrue(all(label in text for label in MENU))
                self.assertIn('↑↓←→', text)
                self.assertIn('q 退出', text)
                self.assertIn('已就绪', text)

    def test_wifi_keyboard_selection_opens_status_and_keeps_focus_after_return(self):
        controller = MagicMock()
        controller.select.return_value = dict(device_id='probe')
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['enter', 'q']), patch.object(
                terminal, 'prompt', side_effect=AssertionError('Menu requested text input')), patch('rmlink_connector.cli.view') as view:
            ui_action(1, controller, terminal)
        controller.call.assert_called_once_with('GET', '/v1/devices/probe/wifi')
        view.assert_called_once_with(terminal, '网络状态', controller.call.return_value)
        self.assertEqual(terminal.stream.getvalue().count('› 设备网络状态'), 2)

    def test_wifi_keyboard_selection_enters_form_and_preserves_hidden_password(self):
        controller = MagicMock()
        controller.select.return_value = dict(device_id='probe')
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['down', 'enter', 'q']), patch.object(
                terminal, 'prompt', side_effect=['测试网络', 'secret123']) as prompt, patch('rmlink_connector.cli.view'):
            ui_action(1, controller, terminal)
        controller.call.assert_called_once_with('POST', '/v1/devices/probe/wifi', dict(ssid='测试网络', password='secret123'))
        self.assertTrue(prompt.call_args_list[1].kwargs['secret'])
        self.assertNotIn('secret123', terminal.stream.getvalue())

    def test_hotspot_menu_works_without_discovering_or_selecting_a_device(self):
        controller = MagicMock()
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['down', 'down', 'enter', 'q']), patch('rmlink_connector.cli.view') as view:
            ui_action(1, controller, terminal)
        controller.hotspot.assert_called_once_with()
        controller.scan.assert_not_called()
        controller.select.assert_not_called()
        controller.call.assert_not_called()
        view.assert_called_once_with(terminal, '电脑热点', controller.hotspot.return_value)
        self.assertEqual(terminal.stream.getvalue().count('› 电脑热点状态'), 2)

    def test_canceling_submenus_does_not_execute_an_operation(self):
        for index in (1, 2, 5, 6):
            for keys in (['q'], ['\x1b'], ['up', 'enter']):
                with self.subTest(index=index, keys=keys):
                    controller = MagicMock()
                    terminal = Terminal(no_color=True, stream=io.StringIO())
                    with patch.object(terminal, 'key', side_effect=keys), patch.object(
                            terminal, 'prompt', side_effect=AssertionError('Menu requested text input')):
                        ui_action(index, controller, terminal)
                    controller.call.assert_not_called()
                    controller.device_action.assert_not_called()
                    controller.diagnose.assert_not_called()
                    controller.runtime.autostart.assert_not_called()

    def test_long_menu_scrolls_to_selected_item_and_preserves_help(self):
        output = io.StringIO()
        terminal = Terminal(no_color=True, stream=output)
        options = [(str(i), f'设备 {i}') for i in range(30)]
        with patch.object(terminal, 'size', return_value=os.terminal_size((32, 12))), patch.object(terminal, 'key', return_value='enter'):
            self.assertEqual(terminal.menu('设备管理', options, selected=25), '25')
        self.assertIn('› 设备 25', output.getvalue())
        self.assertIn('Enter', output.getvalue())
        self.assertTrue(all(cell_width(line) <= 30 for line in output.getvalue().splitlines()))

    def test_noninteractive_wifi_requires_explicit_password_input(self):
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(Controller, 'scan'), patch.object(
                Controller, 'select', return_value=dict(device_id='50787d15a924')), patch('sys.stdin', io.StringIO()), contextlib.redirect_stdout(out):
            self.assertEqual(main(['wifi', 'configure', '--ssid', '网络', '--json']), 2)
        self.assertIn('--password-stdin', json.loads(out.getvalue())['error'])

    def test_diagnostic_export_refuses_to_overwrite_existing_evidence(self):
        from rmlink_connector.cli import export_report
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text('keep')
            with self.assertRaises(FileExistsError):
                export_report(dict(client_version='x'), path)
            self.assertEqual(path.read_text(), 'keep')

    def test_password_stdin_is_utf8_even_under_a_legacy_windows_text_codepage(self):
        secret = '测试用密码123'
        stdin = io.TextIOWrapper(io.BytesIO((secret + '\n').encode('utf-8')), encoding='gbk')
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(Controller, 'scan'), patch.object(
                Controller, 'select', return_value=dict(device_id='50787d15a924')), patch.object(
                Controller, 'call', return_value=dict(saved=True)) as call, patch('sys.stdin', stdin), contextlib.redirect_stdout(out):
            self.assertEqual(main(['wifi', 'configure', '--ssid', '测试网络', '--password-stdin', '--json']), 0)
        self.assertEqual(call.call_args.args[2]['password'], secret)
        self.assertNotIn(secret, out.getvalue())
