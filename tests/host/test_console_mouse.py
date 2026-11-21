"""Mouse operations share the existing key actions and the rendered cell geometry."""
import contextlib
import ctypes as C
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from rmlink_connector.cli import shell, view
from rmlink_connector.console import Control, MENU, MOUSE_ON, MOUSE_OFF, Terminal, dashboard
from rmlink_connector.console_input import InputRecord, KeyRecord, MouseClick, ScreenInfo, WindowsInput


class ConsoleMouseTests(unittest.TestCase):
    def setUp(self):
        self.terminal = Terminal(no_color=True, stream=io.StringIO())
        self.terminal.size = lambda: os.terminal_size((80, 32))

    def click(self, x, y):
        with patch.object(self.terminal, '_read_key', return_value=MouseClick(x, y)):
            return self.terminal.key()

    def click_action(self, key):
        left, _, y, _ = next(t for t in self.terminal.targets if t[3] == key)
        return self.click(left, y)

    def mouse_action(self, key):
        left, _, y, _ = next(t for t in self.terminal.targets if t[3] == key)
        return MouseClick(left, y)

    def test_every_dashboard_item_is_clickable_in_both_layouts_and_compact_logo(self):
        for width, height in ((80, 32), (80, 24), (60, 24), (32, 24)):
            with self.subTest(width=width, height=height):
                self.terminal.size = lambda: os.terminal_size((width, height))
                self.terminal.draw(dashboard(None, width=width, height=height))
                for index in range(len(MENU)):
                    self.assertEqual(self.click_action(str(index + 1)), str(index + 1))
                self.assertEqual(self.click_action('q'), 'q')

    def test_clicks_open_the_correct_menu_and_preserve_its_selection(self):
        controller = MagicMock()
        controller.runtime.status.return_value = None
        with patch.object(self.terminal, 'screen', return_value=contextlib.nullcontext()), patch(
                'rmlink_connector.tui.ui_action') as action:
            with patch.object(self.terminal, '_read_key', side_effect=lambda timeout:
                              self.mouse_action('6') if action.call_count == 0 else 'q'):
                self.assertEqual(shell(controller, self.terminal), 0)
        action.assert_called_once_with(5, controller, self.terminal)
        self.assertIn('› 6  服务与诊断', self.terminal.stream.getvalue())

    def test_geometry_uses_cells_and_does_not_activate_background_or_hidden_rows(self):
        self.terminal.size = lambda: os.terminal_size((14, 4))
        self.terminal.draw([[("设备 ", None), Control('网络配置', None, 'n')],
                            ('', None), Control('  返回  ', None, 'q'), Control('hidden', None, 'x')])
        self.assertEqual(self.click(5, 0), 'n')
        self.assertEqual(self.click(10, 0), 'n')  # Includes a clipped CJK cell.
        for x, y in ((0, 0), (4, 0), (12, 0), (0, 1), (0, 2), (6, 2), (0, 3)):
            self.assertEqual(self.click(x, y), '')
        self.assertEqual(self.click(2, 2), 'q')
        self.terminal.draw([('No controls', None)])
        self.assertEqual(self.click(5, 0), '')

    def test_resize_rejects_old_targets_until_redrawn(self):
        self.terminal.draw([Control('open', None, '1')])
        self.terminal.size = lambda: os.terminal_size((60, 24))
        self.assertEqual(self.click(0, 0), '')
        self.terminal.draw([Control('open', None, '1')])
        self.assertEqual(self.click(0, 0), '1')

    def test_long_menu_click_uses_visible_option_after_wheel_scroll(self):
        self.terminal.size = lambda: os.terminal_size((32, 12))
        options = [(str(i), f'设备 {i}') for i in range(30)]
        inputs = iter(['down'] * 26)
        def raw(timeout):
            try:
                return next(inputs)
            except StopIteration:
                left, _, y, _ = next(t for t in self.terminal.targets if t[3] == '26')
                return MouseClick(left, y)
        with patch.object(self.terminal, '_read_key', side_effect=raw):
            self.assertEqual(self.terminal.menu('设备管理', options), '26')

    def test_click_return_item_cancels_submenu(self):
        with patch.object(self.terminal, '_read_key', side_effect=lambda timeout: self.mouse_action('\x1b')):
            self.assertIsNone(self.terminal.menu('操作', [('s', '状态')]))

    def test_details_wheel_scroll_and_click_return(self):
        inputs = iter(['down', 'down', 'up'])
        def raw(timeout):
            try:
                return next(inputs)
            except StopIteration:
                left, _, y, _ = self.terminal.targets[0]
                return MouseClick(left, y)
        with patch.object(self.terminal, '_read_key', side_effect=raw):
            self.assertIsNone(view(self.terminal, '日志', {'日志': '\n'.join(f'行{i}' for i in range(50))}))
        self.assertIn('  行1\n', self.terminal.stream.getvalue())

    def test_confirmation_click_is_explicit_and_background_click_is_ignored(self):
        inputs = iter([MouseClick(4, 2), 'down'])
        def raw(timeout):
            try:
                return next(inputs)
            except StopIteration:
                left, _, y, _ = next(t for t in self.terminal.targets if t[3] == 'enter')
                return MouseClick(left, y)
        with patch.object(self.terminal, '_read_key', side_effect=raw):
            self.assertTrue(view(self.terminal, '安装确认', {'version': '0.3.4'}, confirm=True))
        with patch.object(self.terminal, '_read_key', return_value='q'):
            self.assertFalse(view(self.terminal, '安装确认', {}, confirm=True))

    def test_sgr_parser_consumes_complete_reports_and_preserves_arrows(self):
        cases = {'[<0;35;20M': MouseClick(34, 19), '[<0;35;20m': '',
                 '[<2;5;8M': '\x1b', '[<1;5;8M': '', '[<32;5;8M': '',
                 '[<64;5;8M': 'up', '[<65;5;8M': 'down', '[<66;5;8M': '',
                 '[<128;5;8M': '', '[A': 'up', 'OD': 'left', '[1;5A': ''}
        for sequence, expected in cases.items():
            with self.subTest(sequence=sequence):
                chars = iter(sequence)
                self.assertEqual(self.terminal._escape_key(lambda wait: next(chars, '')), expected)
                self.assertEqual(next(chars, 'end'), 'end')
        self.assertEqual(self.terminal._escape_key(lambda wait: ''), '\x1b')

    def test_prompt_restores_input_mode_and_reenables_mouse_even_on_error(self):
        native = MagicMock()
        self.terminal.input = native
        self.terminal.vt = self.terminal.mouse_vt = True
        for secret in (False, True):
            with self.subTest(secret=secret), patch('builtins.input', side_effect=EOFError), patch(
                    'getpass.getpass', side_effect=EOFError):
                with self.assertRaises(EOFError):
                    self.terminal.prompt('input: ', secret=secret)
        self.assertEqual(native.method_calls, [unittest.mock.call.restore(), unittest.mock.call.enable()] * 2)
        self.assertIn(MOUSE_OFF, self.terminal.stream.getvalue())
        self.assertIn(MOUSE_ON, self.terminal.stream.getvalue())

    def test_redirected_output_does_not_capture_mouse_or_change_input_modes(self):
        with patch('rmlink_connector.console.WindowsInput') as native:
            with self.terminal.screen():
                self.terminal.draw([Control('open', None, '1')])
        native.assert_not_called()
        self.assertNotIn('\x1b', self.terminal.stream.getvalue())

    @unittest.skipUnless(os.name == 'nt', 'Windows console API')
    def test_failed_mouse_setup_restores_mode_and_keeps_keyboard_fallback(self):
        self.terminal.vt = self.terminal.tty = True
        native = MagicMock()
        native.enable.side_effect = OSError('Console input unavailable')
        with patch('rmlink_connector.console.WindowsInput', return_value=native), patch(
                'sys.stdin', SimpleNamespace(isatty=lambda: True)):
            with self.terminal.screen():
                self.assertIsNone(self.terminal.input)
                with patch('msvcrt.kbhit', return_value=True), patch('msvcrt.getwch', return_value='q'):
                    self.assertEqual(self.terminal.key(), 'q')
        native.restore.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows console API')
    def test_screen_restores_native_input_and_output_modes_after_exception(self):
        self.terminal.vt = self.terminal.tty = True
        self.terminal.original_mode = 7
        self.terminal.handle = 99
        self.terminal.kernel = MagicMock()
        native = MagicMock()
        with patch('rmlink_connector.console.WindowsInput', return_value=native), patch(
                'sys.stdin', SimpleNamespace(isatty=lambda: True)):
            with self.assertRaisesRegex(RuntimeError, 'exit'):
                with self.terminal.screen():
                    raise RuntimeError('exit')
        native.enable.assert_called_once()
        native.restore.assert_called_once()
        self.terminal.kernel.SetConsoleMode.assert_called_once_with(99, 7)
        self.assertIsNone(self.terminal.input)
        self.assertFalse(self.terminal.mouse_vt)


@unittest.skipUnless(os.name == 'nt', 'Windows console input records')
class WindowsMouseTests(unittest.TestCase):
    def setUp(self):
        self.native = object.__new__(WindowsInput)
        self.native.buttons = self.native.repeats = 0
        self.native.kernel = MagicMock()
        self.native.output = 99
        def info(handle, pointer):
            value = C.cast(pointer, C.POINTER(ScreenInfo)).contents
            value.window.Left, value.window.Top = 3, 10
            return 1
        self.native.kernel.GetConsoleScreenBufferInfo.side_effect = info

    def mouse(self, buttons, flags=0):
        record = InputRecord()
        record.kind = 2
        record.mouse.buttons, record.mouse.flags = buttons, flags
        record.mouse.position.X, record.mouse.position.Y = 20, 30
        return self.native.decode(record)

    def test_records_match_windows_abi(self):
        self.assertEqual(C.sizeof(KeyRecord), 16)
        self.assertEqual(C.sizeof(InputRecord), 20)
        self.assertEqual(InputRecord.event.offset, 4)

    def test_left_press_translates_viewport_and_ignores_release_motion_and_double_click(self):
        self.assertEqual(self.mouse(1), MouseClick(17, 20))
        self.assertEqual(self.mouse(1), '')
        self.assertEqual(self.mouse(0), '')
        self.assertEqual(self.mouse(1, 1), '')
        self.assertEqual(self.mouse(1, 2), '')
        self.assertEqual(self.mouse(0), '')
        self.assertEqual(self.mouse(1), MouseClick(17, 20))

    def test_right_click_and_signed_wheel(self):
        self.assertEqual(self.mouse(2), 'q')
        self.assertEqual(self.mouse(0), '')
        self.assertEqual(self.mouse(120 << 16, 4), 'up')
        self.assertEqual(self.mouse((-120 & 0xffff) << 16, 4), 'down')
        self.assertEqual(self.mouse(120 << 16, 8), '')

    def test_key_down_repeat_arrows_unicode_enter_and_ctrl_c(self):
        for code, char, expected in ((0x25, '\x00', 'left'), (0x26, '\x00', 'up'),
                                     (0x27, '\x00', 'right'), (0x28, '\x00', 'down'),
                                     (0x0d, '\r', 'enter'), (0, 'Q', 'q'), (0, '中', '中')):
            record = InputRecord()
            record.kind = 1
            record.key.down, record.key.repeat = 1, 2
            record.key.code, record.key.char = code, char
            self.assertEqual(self.native.decode(record), expected)
            self.assertEqual(self.native.read(0), expected)
            record.key.down = 0
            self.assertEqual(self.native.decode(record), '')
        record.key.down, record.key.char = 1, '\x03'
        with self.assertRaises(KeyboardInterrupt):
            self.native.decode(record)
