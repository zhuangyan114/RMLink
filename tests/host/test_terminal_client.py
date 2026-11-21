import contextlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from ota_fixture import application_image

from rmlink_connector.cli import main, parser, Controller, SignalReader, shell, ui_action
from rmlink_connector.console import MENU, dashboard, cell_width, fit, safe_text, Terminal
from rmlink_connector.local_service import ClientError, LocalService, inspect_firmware


class TerminalClientTests(unittest.TestCase):
    def test_signal_reader_refreshes_without_blocking_and_clears_old_devices(self):
        started, release = threading.Event(), threading.Event()
        controller = MagicMock()

        def read(*args):
            started.set()
            release.wait(2)
            return dict(connected=True, rssi_dbm=-65)

        controller.call.side_effect = read
        signal = SignalReader(controller)
        wifi = dict(device_id='a', connected=True, transport='wifi')
        self.assertIsNone(signal.sample(wifi))
        self.assertTrue(started.wait(1))
        self.assertIsNone(signal.sample(wifi))  # A slow query does not block rendering.
        signal.sample(dict(device_id='a', connected=False, transport='wifi'))
        release.set()
        deadline = time.monotonic() + 2
        while signal.pending and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNone(signal.value)  # The late reply belongs to the old connection.
        self.assertIsNone(signal.sample(dict(device_id='b', connected=True, transport='usb')))
        controller.call.assert_called_once_with('GET', '/v1/devices/a/wifi')

    def test_dashboard_displays_rssi_and_wraps_on_narrow_terminals(self):
        device = dict(device_id='a', connected=True, transport='wifi')
        metrics = dict(rssi_dbm=-65)
        for width in (32, 100):
            rows = dashboard(dict(version='1'), device, metrics, width=width)
            texts = [row[0] for row in rows if isinstance(row, tuple)]
            self.assertTrue(any('信号  -65 dBm' in row for row in texts))
            if width == 100:
                self.assertTrue(any('接收' in row and '发送' in row and '信号' in row for row in texts))
        rows = dashboard(dict(version='1'), device, {}, width=100)
        self.assertTrue(any('信号  --' in row[0] for row in rows if isinstance(row, tuple)))

    def test_signal_reader_uses_fresh_valid_readings_only(self):
        controller = MagicMock()
        signal = SignalReader(controller)
        signal.device_id = 'a'
        signal.generation = 1
        wifi = dict(device_id='a', connected=True, transport='wifi')
        for reading, expected in ((-65, -65), (0, None), (None, None)):
            controller.call.return_value = dict(connected=True, rssi_dbm=reading)
            signal._read('a', 1)
            with patch('rmlink_connector.controller.threading.Thread'):
                self.assertEqual(signal.sample(wifi), expected)
            signal.pending = False
        signal.value, signal.observed = -65, time.monotonic() - 4
        with patch('rmlink_connector.controller.threading.Thread'):
            self.assertIsNone(signal.sample(wifi))

    def test_removed_commands_are_rejected_before_starting_the_service(self):
        for command in (['mount'], ['unmount'], ['driver', 'status'], ['--usbip-port', '3240', 'status']):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), patch(
                    'rmlink_connector.local_service.LocalService.start') as start:
                with self.assertRaises(SystemExit) as error:
                    main(command)
                self.assertEqual(error.exception.code, 2)
                start.assert_not_called()

    def test_nested_commands_require_explicit_subcommand_before_starting_service(self):
        for command in (['wifi'], ['firmware'], ['service'], ['config']):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), patch(
                    'rmlink_connector.local_service.LocalService.start') as start:
                with self.assertRaises(SystemExit) as error:
                    main(command)
                self.assertEqual(error.exception.code, 2)
                start.assert_not_called()

    def test_conflicting_device_arguments_and_preview_are_rejected_without_starting_service(self):
        cases = (
            ['--device', 'aaaa', 'connect', 'bbbb', '--json'],
            ['--preview', 'devices', '--json'],
            ['--json'],
            ['config', 'default', '--json'],
            ['firmware', 'update', '--prerelease', '--json'],
        )
        for command in cases:
            with self.subTest(command=command), patch('rmlink_connector.local_service.LocalService.start') as start:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(command), 2)
                start.assert_not_called()
                self.assertEqual(json.loads(out.getvalue())['exit_code'], 2)

    def test_config_commands_support_show_default_and_repo(self):
        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(
                Controller, 'call', return_value=dict(default_device='50787d15a924')) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['config', 'show', '--json']), 0)
        call.assert_called_once_with('GET', '/v1/preferences')

        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(
                Controller, 'call', return_value=dict(default_device='50787d15a924')) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['config', 'default', '--device', '50787D15A924', '--json']), 0)
        call.assert_called_once_with('POST', '/v1/preferences', dict(default_device='50787d15a924'))

        out = io.StringIO()
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(
                Controller, 'call', return_value=dict(firmware_repository='owner/repo')) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['config', 'repo', 'owner/repo', '--json']), 0)
        call.assert_called_once_with('POST', '/v1/preferences', dict(firmware_repository='owner/repo'))

    def test_six_item_symmetric_navigation_wraps_and_switches_columns_cleanly(self):
        for keys, expected in ((['up', 'enter', 'q'], 5),
                               (['down', 'down', 'right', 'enter', 'q'], 5),
                               (['right', 'left', 'enter', 'q'], 0)):
            controller, terminal = MagicMock(), MagicMock()
            controller.runtime.status.return_value = None
            terminal.size.return_value = os.terminal_size((80, 32))
            terminal.screen.return_value = contextlib.nullcontext()
            terminal.key.side_effect = keys
            with patch('rmlink_connector.tui.dashboard', return_value=[]), patch('rmlink_connector.tui.ui_action') as action:
                self.assertEqual(shell(controller, terminal), 0)
            action.assert_called_once_with(expected, controller, terminal)

    def test_dashboard_clears_refresh_errors_after_reconnecting(self):
        controller, terminal = MagicMock(), MagicMock()
        controller.runtime.status.return_value = dict(version='0.3.4', devices=[dict(device_id='a', connected=True)])
        controller.default_device.return_value = None
        controller.try_select.return_value = (dict(device_id='a', connected=True), [dict(device_id='a', connected=True)], 'ok')
        error = ClientError('设备连接中断')
        ok_metrics = dict(rates={})
        controller.metrics.side_effect = [error, error, ok_metrics, error, ok_metrics]
        terminal.size.return_value = os.terminal_size((80, 32))
        terminal.screen.return_value = contextlib.nullcontext()
        terminal.vt = True
        terminal.key.side_effect = ['', '', '', '', 'q']
        with patch('rmlink_connector.tui.dashboard', return_value=[]) as draw:
            self.assertEqual(shell(controller, terminal), 0)
        self.assertEqual([call.args[6] for call in draw.call_args_list],
                         [str(error), str(error), '', str(error), ''])

    def test_log_location_is_shared_when_a_packaged_host_redirects_localappdata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, USERPROFILE=directory, LOCALAPPDATA=str(Path(directory) / 'package cache')):
                packaged = LocalService()
            with patch.dict(os.environ, USERPROFILE=directory, LOCALAPPDATA=str(Path(directory) / 'ordinary appdata')):
                terminal = LocalService()
            self.assertEqual(packaged.logs, terminal.logs)
            self.assertEqual(packaged.logs, Path(directory) / '.rmlink' / 'logs')

    def test_script_options_work_at_each_command_level(self):
        for args in [['--json', '--device', 'abc', 'wifi', 'status'],
                     ['wifi', '--json', '--device', 'abc', 'status'],
                     ['wifi', 'status', '--json', '--device', 'abc']]:
            parsed = parser().parse_args(args)
            self.assertTrue(parsed.json)
            self.assertEqual(parsed.device, 'abc')

    def test_device_option_is_omitted_from_non_device_subcommands(self):
        for args in (['logs', '--device', 'abc'], ['service', 'status', '--device', 'abc'],
                     ['wifi', 'hotspot', '--device', 'abc']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parser().parse_args(args)
                self.assertEqual(error.exception.code, 2)

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
        with patch('rmlink_connector.local_service.LocalService.start') as start, patch.object(
                Controller, 'select', return_value=device), patch.object(
                Controller, 'call', return_value=dict(supported=False, message='升级接口已预留')) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['firmware', 'update', '--json']), 2)
        start.assert_not_called()
        call.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['exit_code'], 2)

    def test_narrow_dashboard_has_no_oversized_rows_and_removes_remote_control_characters(self):
        device = dict(device_id='50787d15a924', hostname='工作台\x1b[2J', firmware='0.2.40', connected=True)
        for width in (32, 60, 80, 100):
            rows = dashboard(dict(version='0.2.15'), device, width=width)
            output = io.StringIO()
            terminal = Terminal(no_color=True, stream=output)
            with patch.object(terminal, 'size', return_value=os.terminal_size((width, 28))):
                terminal.draw(rows)
            self.assertTrue(all(cell_width(line) <= width - 2 for line in output.getvalue().splitlines()))
            self.assertNotIn('\x1b', output.getvalue())
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
        for width, target in ((80, 4), (60, 2)):
            with self.subTest(width=width):
                controller, terminal = MagicMock(), MagicMock()
                controller.runtime.status.return_value = None
                terminal.size.return_value = os.terminal_size((width, 32))
                terminal.screen.return_value = contextlib.nullcontext()
                terminal.key.side_effect = ['down', 'right', 'enter', 'left', 'q']
                with patch('rmlink_connector.tui.dashboard', return_value=[]) as draw, patch('rmlink_connector.tui.ui_action') as action:
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
        device = dict(device_id='probe', hostname='rmlink', connected=True, paired=True)
        controller = MagicMock()
        controller.try_select.return_value = (device, [device], 'ok')
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['enter', 'q']), patch.object(
                terminal, 'prompt', side_effect=AssertionError('Menu requested text input')), patch('rmlink_connector.tui.view') as view:
            ui_action(2, controller, terminal)
        controller.call.assert_called_once_with('GET', '/v1/devices/probe/wifi')
        view.assert_called_once_with(terminal, '网络状态', controller.call.return_value)
        self.assertEqual(terminal.stream.getvalue().count('› 1  查看设备网络状态'), 2)

    def test_wifi_keyboard_selection_enters_form_and_preserves_hidden_password(self):
        device = dict(device_id='probe', hostname='rmlink', connected=True, paired=True)
        controller = MagicMock()
        controller.try_select.return_value = (device, [device], 'ok')
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['down', 'enter', 'q']), patch.object(
                terminal, 'prompt', side_effect=['测试网络', 'secret123']) as prompt, patch('rmlink_connector.tui.view'):
            ui_action(2, controller, terminal)
        controller.call.assert_called_once_with('POST', '/v1/devices/probe/wifi', dict(ssid='测试网络', password='secret123'))
        self.assertTrue(prompt.call_args_list[1].kwargs['secret'])
        self.assertNotIn('secret123', terminal.stream.getvalue())

    def test_wifi_form_blank_ssid_or_interrupt_cancels_cleanly(self):
        device = dict(device_id='probe', hostname='rmlink', connected=True, paired=True)
        for prompt_effect in ([''], [KeyboardInterrupt()]):
            with self.subTest(prompt_effect=prompt_effect):
                controller = MagicMock()
                controller.try_select.return_value = (device, [device], 'ok')
                terminal = Terminal(no_color=True, stream=io.StringIO())
                with patch.object(terminal, 'key', side_effect=['2', 'q']), patch.object(
                        terminal, 'prompt', side_effect=prompt_effect):
                    ui_action(2, controller, terminal)
                controller.call.assert_not_called()
                self.assertIn('已取消 Wi-Fi 配网', terminal.stream.getvalue())

    def test_hotspot_menu_works_without_discovering_or_selecting_a_device(self):
        controller = MagicMock()
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['5', 'q']), patch('rmlink_connector.tui.view') as view:
            ui_action(2, controller, terminal)
        controller.hotspot.assert_called_once_with()
        controller.scan.assert_not_called()
        controller.try_select.assert_not_called()
        controller.call.assert_not_called()
        view.assert_called_once_with(terminal, '电脑热点', controller.hotspot.return_value)
        self.assertEqual(terminal.stream.getvalue().count('› 5  检查电脑 2.4 GHz 热点'), 1)

    def test_multi_device_connection_menu_prompts_picker_and_updates_mode_in_place(self):
        dev_a = dict(device_id='aaaa', hostname='板A', paired=True, connected=True,
                     preferred_transport='wifi', active_transport='wifi', transport='wifi')
        dev_b = dict(device_id='bbbb', hostname='板B', paired=True, connected=True,
                     preferred_transport='usb', active_transport='usb', transport='usb')
        controller = MagicMock()
        controller.device_id = None
        controller.default_device.return_value = None
        controller.try_select.return_value = (None, [dev_a, dev_b], 'ambiguous')
        controller.runtime.status.return_value = dict(devices=[dev_a, dev_b])
        controller.device_action.return_value = dict(connected=True, active_transport='usb', transport='usb')
        terminal = Terminal(no_color=True, stream=io.StringIO())
        # Pick device 2 (dev_b), then press '3' (Auto mode), then 'q' to leave connection menu.
        with patch.object(terminal, 'key', side_effect=['2', '3', 'q']):
            ui_action(1, controller, terminal)
        self.assertEqual(controller.device_id, 'bbbb')
        controller.device_action.assert_called_once_with('connect', dev_b, 'auto')
        text = terminal.stream.getvalue()
        self.assertIn('● 固定为 USB', text)
        self.assertIn('已切换为 自动（当前通道：USB）', text)

    def test_unpair_in_device_menu_requires_explicit_confirmation(self):
        device = dict(device_id='aaaa', hostname='工作台', paired=True, connected=True,
                      preferred_transport='auto', active_transport='usb', transport='usb')
        controller = MagicMock()
        controller.device_id = 'aaaa'
        controller.default_device.return_value = 'aaaa'
        controller.scan.return_value = dict(devices=[device])
        controller.runtime.status.return_value = dict(devices=[device])
        terminal = Terminal(no_color=True, stream=io.StringIO())
        # Open device 1 -> press '4' (unpair) -> cancel with 'q' -> back twice.
        with patch.object(terminal, 'key', side_effect=['1', '4', 'q', 'q', 'q']):
            ui_action(0, controller, terminal)
        controller.device_action.assert_not_called()

        # Now confirm unpair with 'enter'.
        controller.device_action.return_value = dict(device_id='aaaa', connected=False, paired=False)
        terminal = Terminal(no_color=True, stream=io.StringIO())
        with patch.object(terminal, 'key', side_effect=['1', '4', 'enter', 'q']):
            ui_action(0, controller, terminal)
        controller.device_action.assert_called_once_with('unpair', device)

    def test_canceling_submenus_does_not_execute_an_operation(self):
        device = dict(device_id='probe', hostname='rmlink', paired=True, connected=True,
                      preferred_transport='auto', active_transport='usb', transport='usb')
        for index in (1, 2, 4, 5):
            for keys in (['q'], ['\x1b'], ['up', 'enter']):
                with self.subTest(index=index, keys=keys):
                    controller = MagicMock()
                    controller.device_id = 'probe'
                    controller.default_device.return_value = None
                    controller.try_select.return_value = (device, [device], 'ok')
                    controller.runtime.status.return_value = dict(devices=[device])
                    controller.runtime.autostart.return_value = dict(start_at_logon=False)
                    terminal = Terminal(no_color=True, stream=io.StringIO())
                    with patch.object(terminal, 'key', side_effect=keys), patch.object(
                            terminal, 'prompt', side_effect=AssertionError('Menu requested text input')):
                        ui_action(index, controller, terminal)
                    controller.call.assert_not_called()
                    controller.device_action.assert_not_called()
                    controller.diagnose.assert_not_called()
                    if index == 5:
                        controller.runtime.autostart.assert_called_once_with(None)
                    else:
                        controller.runtime.autostart.assert_not_called()

    def test_controller_caches_default_device_until_updated(self):
        args = SimpleNamespace(state_dir=None, api_port=3243, device=None)
        controller = Controller(args)
        with patch.object(controller, 'call', return_value=dict(default_device='aaaa')) as call:
            self.assertEqual(controller.default_device(), 'aaaa')
            self.assertEqual(controller.default_device(), 'aaaa')
            call.assert_called_once_with('GET', '/v1/preferences')
            call.return_value = dict(default_device='bbbb')
            controller.set_default('bbbb')
            self.assertEqual(controller.default_device(), 'bbbb')
            self.assertEqual(call.call_count, 2)

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

    def test_connect_with_transport_calls_transport_route_once(self):
        out = io.StringIO()
        device = dict(device_id='50787d15a924')
        with patch('rmlink_connector.local_service.LocalService.start'), patch.object(
                Controller, 'select', return_value=device), patch.object(
                Controller, 'call', return_value=dict(device_id='50787d15a924', transport='usb', connected=True)) as call, contextlib.redirect_stdout(out):
            self.assertEqual(main(['connect', '--transport', 'auto', '--json']), 0)
        call.assert_called_once_with('POST', '/v1/transport', dict(device_id='50787d15a924', transport='auto', connect=True))

    def test_wifi_on_and_off_commands(self):
        device = dict(device_id='50787d15a924')
        for action, enabled in (('on', True), ('off', False)):
            with self.subTest(action=action):
                out = io.StringIO()
                with patch('rmlink_connector.local_service.LocalService.start'), patch.object(Controller, 'scan'), patch.object(
                        Controller, 'select', return_value=device), patch.object(
                        Controller, 'call', return_value=dict(wifi_enabled=enabled)) as call, contextlib.redirect_stdout(out):
                    self.assertEqual(main(['wifi', action, '--json']), 0)
                call.assert_called_once_with('POST', '/v1/devices/50787d15a924/wifi', dict(enabled=enabled))

    def test_dashboard_displays_auto_mode_active_and_offline_labels_and_default_badge(self):
        for device, expected in (
            (dict(device_id='a', connected=True, preferred_transport='auto', active_transport='usb', transport='usb',
                  board_link=dict(wifi_enabled=True)), '自动 · USB'),
            (dict(device_id='a', connected=True, preferred_transport='auto', active_transport='wifi', transport='wifi',
                  board_link=dict(wifi_enabled=False)), '自动 · Wi-Fi'),
            (dict(device_id='a', connected=False, preferred_transport='auto', active_transport=None, transport='auto'), '自动'),
        ):
            with self.subTest(expected=expected):
                rows = dashboard(dict(version='0.3.5'), device, width=88, default_device='a')
                texts = [row[0] for row in rows if isinstance(row, tuple)]
                self.assertTrue(any(expected in row for row in texts))
                self.assertTrue(any('默认' in row for row in texts))
        rows = dashboard(dict(version='0.3.5', devices=[dict(device_id='a'), dict(device_id='b')]), None, width=88)
        texts = [row[0] for row in rows if isinstance(row, tuple)]
        self.assertTrue(any('已发现 2 台设备，尚未选定' in row for row in texts))
