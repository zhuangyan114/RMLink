"""A small, dependency-free terminal surface with a quiet instrument-panel style."""
import contextlib
import os
import re
import shutil
import sys
import time
import unicodedata
from typing import NamedTuple

from .console_input import MouseClick, WindowsInput

WORDMARK = (
    '████████    ██      ██  ██          ██████  ██      ██  ██      ██',
    '██      ██  ████  ████  ██            ██    ████    ██  ██    ██  ',
    '██      ██  ██  ██  ██  ██            ██    ████    ██  ██  ██    ',
    '████████    ██      ██  ██            ██    ██  ██  ██  ████      ',
    '██  ██      ██      ██  ██            ██    ██    ████  ██  ██    ',
    '██    ██    ██      ██  ██            ██    ██    ████  ██    ██  ',
    '██      ██  ██      ██  ██████████  ██████  ██      ██  ██      ██',
)
COMPACT_WORDMARK = 'R M L I N K'
MENU = ('设备管理', 'Wi-Fi 配置', '连接管理', '实时监控',
        '固件升级', '诊断与日志', '设置')
ESCAPE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
MOUSE_ON = '\x1b[?1000h\x1b[?1006h'
MOUSE_OFF = '\x1b[?1000l\x1b[?1006l'


class Control(NamedTuple):
    text: str
    role: str | None
    key: str


def safe_text(value):
    # Device aliases, hostnames and remote error strings are untrusted display data.
    return ''.join(ch for ch in str(value) if unicodedata.category(ch) not in ('Cc', 'Cf', 'Cs'))


def cell_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 0 if unicodedata.combining(ch) else 1
               for ch in text)


def fit(text, width):
    text = safe_text(text)
    if cell_width(text) <= width:
        return text
    result, used = '', 0
    for ch in text:
        size = cell_width(ch)
        if used + size > max(0, width - 1):
            break
        result += ch; used += size
    return result + '…' if width else ''


def pad(text, width):
    text = fit(text, width)
    return text + ' ' * max(0, width - cell_width(text))


def rate(value):
    if value is None:
        return '--'
    return f'{value / 1024:.2f} KiB/s' if value < 1024 * 1024 else f'{value / 1024 / 1024:.2f} MiB/s'


def duration(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


class Terminal:
    COLORS = dict(accent='38;2;110;176;194', good='38;2;133;183;152',
                  brand='38;2;159;198;245', brand_dim='38;2;74;115;174', selected='38;2;93;165;245',
                  warning='38;2;204;174;116', muted='2', strong='1')

    def __init__(self, no_color=False, stream=None):
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty()
        self.vt = self.tty and os.environ.get('TERM') != 'dumb'
        self.original_mode = None
        self.input = None
        self.mouse_vt = False
        self.posix_mode = None
        self.targets = []
        self.draw_size = None
        if self.vt and os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.handle = kernel.GetStdHandle(-11)
            mode = wintypes.DWORD()
            self.vt = bool(kernel.GetConsoleMode(self.handle, ctypes.byref(mode)))
            if self.vt:
                self.original_mode = mode.value
                self.vt = bool(kernel.SetConsoleMode(self.handle, mode.value | 4))
            self.kernel = kernel
        self.color = self.vt and not no_color and 'NO_COLOR' not in os.environ

    def paint(self, text, role):
        return f'\x1b[{self.COLORS[role]}m{text}\x1b[0m' if self.color and role else text

    def size(self):
        return shutil.get_terminal_size((88, 28))

    @contextlib.contextmanager
    def screen(self):
        try:
            if self.tty and sys.stdin.isatty():
                if os.name == 'nt' and self.vt:
                    try:
                        self.input = WindowsInput()
                        self.input.enable()
                    except OSError:
                        if self.input is not None:
                            self.input.restore()
                        self.input = None  # Keep the existing keyboard fallback.
                elif os.name != 'nt':
                    import termios
                    import tty
                    self.posix_mode = termios.tcgetattr(sys.stdin)
                    tty.setcbreak(sys.stdin.fileno())
                    self.mouse_vt = self.vt
            if self.vt:
                self.stream.write('\x1b[?1049h\x1b[?25l' + (MOUSE_ON if self.mouse_vt else ''))
                self.stream.flush()
            yield
        finally:
            if self.input is not None:
                self.input.restore()
                self.input = None
            if self.posix_mode is not None:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.posix_mode)
                self.posix_mode = None
            if self.vt:
                self.stream.write((MOUSE_OFF if self.mouse_vt else '') + '\x1b[0m\x1b[?25h\x1b[?1049l')
                self.stream.flush()
            self.mouse_vt = False
            self.targets = []
            if self.original_mode is not None:
                self.kernel.SetConsoleMode(self.handle, self.original_mode)

    def draw(self, lines):
        width, height = self.size()
        self.draw_size = (width, height)
        self.targets = []
        rows = []
        for line in lines[:max(1, height - 1)]:
            # A row can contain independently styled spans, such as menu columns.
            spans = line if isinstance(line, list) else [line]
            remaining, parts = max(1, width - 2), []
            for span in spans:
                text, role = span[:2]
                text = fit(text, remaining)
                start = max(1, width - 2) - remaining
                if isinstance(span, Control) and text.strip():
                    self.targets.append((start + cell_width(text) - cell_width(text.lstrip()),
                                         start + cell_width(text.rstrip()), len(rows), span.key))
                parts.append(self.paint(text, role))
                remaining -= cell_width(text)
                if remaining <= 0:
                    break
            rows.append(''.join(parts))
        if self.vt:
            # Erase each row in place; avoid clearing the whole screen on each tick.
            self.stream.write('\x1b[H' + '\r\n'.join(row + '\x1b[K' for row in rows) + '\x1b[J')
        else:
            self.stream.write('\n'.join(rows) + '\n')
        self.stream.flush()

    def key(self, timeout=1):
        key = self._read_key(timeout)
        if isinstance(key, MouseClick):
            # A resize invalidates the old geometry until the next draw.
            if tuple(self.size()) != self.draw_size:
                return ''
            return next((keycode for left, right, row, keycode in self.targets
                         if key.y == row and left <= key.x < right), '')
        return key

    def _read_key(self, timeout):
        if self.input is not None:
            key = self.input.read(timeout)
            return self._escape_key(self.input.read) if key == '\x1b' else key
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ('\x00', '\xe0'):
                        return {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right'}.get(msvcrt.getwch(), '')
                    if ch == '\x1b':
                        until = time.monotonic() + .03
                        while time.monotonic() < until and not msvcrt.kbhit():
                            time.sleep(.002)
                        if msvcrt.kbhit():
                            prefix = msvcrt.getwch()
                            if prefix in ('[', 'O') and msvcrt.kbhit():
                                return {'A': 'up', 'B': 'down', 'D': 'left', 'C': 'right'}.get(msvcrt.getwch(), '')
                    if ch == '\x03':
                        raise KeyboardInterrupt
                    return 'enter' if ch == '\r' else ch.lower()
                time.sleep(.03)
            return ''
        import select
        if select.select([sys.stdin], [], [], timeout)[0]:
            ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='ignore')
            if ch == '\x1b':
                return self._escape_key(lambda wait: os.read(sys.stdin.fileno(), 1).decode('ascii', errors='ignore')
                                        if select.select([sys.stdin], [], [], wait)[0] else '')
            return 'enter' if ch in ('\n', '\r') else ch.lower()
        return ''

    def _escape_key(self, read):
        sequence = read(.03)
        if not sequence:
            return '\x1b'
        if sequence not in ('[', 'O'):
            return ''
        # Read a whole CSI/SS3 event, including split SGR mouse reports. Never
        # leave mouse coordinates behind to become numeric menu shortcuts.
        for _ in range(64):
            ch = read(.03)
            if not ch or len(ch) != 1:
                return ''
            sequence += ch
            if '@' <= ch <= '~':
                break
        else:
            return ''
        if sequence.startswith('[<'):
            match = re.fullmatch(r'\[<(\d+);(\d+);(\d+)([Mm])', sequence)
            if not match or match[4] == 'm':
                return ''
            button, x, y = map(int, match.group(1, 2, 3))
            if button & 32:  # Motion/drag reports do not activate anything.
                return ''
            if button & 64:
                return {64: 'up', 65: 'down'}.get(button & ~28, '')
            if button & 128:
                return ''
            return MouseClick(x - 1, y - 1) if button & 3 == 0 else '\x1b' if button & 3 == 2 else ''
        return {'[A': 'up', '[B': 'down', '[D': 'left', '[C': 'right',
                'OA': 'up', 'OB': 'down', 'OD': 'left', 'OC': 'right'}.get(sequence, '')

    def prompt(self, label, secret=False, hint=None):
        if hint:
            self.stream.write('\n' + self.paint('  ' + fit(hint, max(1, self.size().columns - 4)), 'muted') + '\n')
            self.stream.flush()
        try:
            self.targets = []
            if self.input is not None:
                self.input.restore()
            if self.posix_mode is not None:
                import termios
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.posix_mode)
            if self.vt:
                self.stream.write((MOUSE_OFF if self.mouse_vt else '') + '\x1b[?25h'); self.stream.flush()
            if secret:
                import getpass
                return getpass.getpass(label)
            return input(label)
        finally:
            if self.input is not None:
                self.input.enable()
            if self.posix_mode is not None:
                import tty
                tty.setcbreak(sys.stdin.fileno())
            if self.vt:
                self.stream.write('\x1b[?25l' + (MOUSE_ON if self.mouse_vt else '')); self.stream.flush()

    def menu(self, title, options, selected=0, description=''):
        options = [*options, (None, '返回上一级')]
        selected = min(max(0, selected), len(options) - 1)
        while True:
            self.draw(menu_rows(title, options, selected, self.size(), description))
            key = self.key()
            if not key:
                continue
            if key in ('q', '\x1b'):
                return None
            if key in ('up', 'left'):
                selected = (selected - 1) % len(options)
            elif key in ('down', 'right'):
                selected = (selected + 1) % len(options)
            elif key == 'enter':
                return options[selected][0]
            elif key and any(key == option[0] for option in options):
                return key


def wordmark_rows(width, height):
    large = width >= max(map(cell_width, WORDMARK)) + 2 and height >= len(WORDMARK)
    rows, split = (WORDMARK, 24) if large else ((COMPACT_WORDMARK,), 4)
    return [[('  ' + row[:split], 'brand_dim'), (row[split:], 'brand')] for row in rows]


def menu_rows(title, options, selected, size, description=''):
    lines = [(f'  {title}', 'strong'), ('', None)]
    if description:
        lines += [('  ' + description, 'muted'), ('', None)]
    lines = wordmark_rows(size.columns - 2, size.lines - len(lines) - len(options) - 4) + lines
    visible = max(1, size.lines - len(lines) - 4)
    offset = max(0, min(selected - visible + 1, len(options) - visible))
    for index in range(offset, min(len(options), offset + visible)):
        label = options[index][1]
        lines.append(Control(f'  {"›" if index == selected else " "} {label}',
                             'selected' if index == selected else None,
                             options[index][0] if options[index][0] is not None else '\x1b'))
    if len(options) > visible:
        lines.append((f'  {selected + 1} / {len(options)}', 'muted'))
    lines += [[('  ↑↓选择 Enter打开  ' if size.columns < 48 else '  ↑↓ 选择  Enter 打开  ', 'muted'),
               Control('q 返回' if size.columns < 48 else 'q / Esc 返回', 'muted', '\x1b')],
              ('  左键打开  滚轮选择  右键返回', 'muted')]
    return lines


def dashboard(status, device=None, metrics=None, rates=None, selected=0, width=88, message='', monitor=False, height=28):
    width = min(max(24, width - 4), 78)
    lines = [('  设备控制台', 'muted')]
    version = status.get('version', '--') if status else '--'
    lines += [('  ' + '─' * (width - 2), 'muted')]
    lines += [(f'  后台服务  {"运行中" if status else "未启动"}    ·    v{version}', 'good' if status else 'warning')]
    if device:
        name = device.get('alias') or device.get('hostname') or device['device_id']
        pref = device.get('preferred_transport') or device.get('transport') or 'wifi'
        active = device.get('active_transport') or device.get('transport')
        if pref == 'auto':
            transport_text = f'自动 · {"USB" if active == "usb" else "Wi-Fi"}' if device.get('connected') else '自动'
        else:
            transport_text = 'USB' if (active if device.get('connected') else pref) == 'usb' else 'Wi-Fi'
        lines += [(f'  当前设备  {name}', 'strong'),
                  (f'  {device["device_id"]}  ·  {transport_text}  ·  '
                   f'{"在线" if device.get("connected") else "离线"}', None),
                  (f'  目标占用  {"调试会话" if device.get("owner") else "空闲"}    固件  {device.get("firmware", "--")}', 'muted')]
    else:
        lines += [('  尚未选择设备', 'strong'), ('  按 1 发现并配对，或按 2 通过 USB 配置 Wi-Fi', 'muted'), ('', None)]
    traffic = (metrics or {}).get('traffic') or {}
    rates = rates or {}
    rssi = (metrics or {}).get('rssi_dbm')
    signal = f'{rssi} dBm' if rssi is not None else '--'
    traffic_line = f'  接收  {rate(rates.get("rx_bps"))}    发送  {rate(rates.get("tx_bps"))}'
    signal_line = f'信号  {signal}'
    if cell_width(traffic_line + '    ' + signal_line) <= width:
        lines.append((traffic_line + '    ' + signal_line, 'accent'))
    else:
        lines += [(traffic_line, 'accent'), ('  ' + signal_line, 'accent')]
    lines += [
              (f'  连接  {duration(traffic.get("elapsed_s")) if traffic else "--"}    应用层流量 · 每秒刷新', 'muted'),
              ('  ' + '─' * (width - 2), 'muted')]
    if monitor:
        lines += [(f'  累计接收 {(traffic.get("rx_bytes",0)/1024):.1f} KiB    累计发送 {(traffic.get("tx_bytes",0)/1024):.1f} KiB', 'muted'),
                  (f'  非预期掉线 {(metrics or {}).get("link_losses",0)} 次    最近命令批次 '
                   + (f'{traffic["last_batch_ms"]:.2f} ms' if traffic.get('last_batch_ms') is not None else '--'), 'muted')]
        lines += [('  实时订阅', 'strong')]
        for sub in (metrics or {}).get('subscriptions', []):
            elapsed = sub.get('last_us', 0) - sub.get('first_us', 0)
            hz = (sub.get('frames', 0) - 1) * 1e6 / elapsed if elapsed > 0 else None
            actual = f'{hz:.1f} Hz' if hz is not None else '--'
            label = {'memory_series': 'Timeline', 'memory_snapshot': 'Watch', 'rtt_stream': 'RTT'}.get(sub['kind'], sub['kind'])
            lines.append((f'  {label:<10} 请求 {sub["requested_hz"]:.0f} Hz  实际均值 {actual}', None))
        statuses=[s.get('board_status') for s in (metrics or {}).get('subscriptions',[])]
        dropped=str(sum(s['ring_dropped'] for s in statuses)) if statuses and all(statuses) else '--'
        lines.append((f'  板端丢弃 {dropped}  ·  复用调试会话最近一次状态快照', 'muted'))
        if not (metrics or {}).get('subscriptions'):
            lines += [('  暂无活动订阅。调试软件开始采样后自动显示。', 'muted')]
        lines += [('', None), [Control('  q 返回', 'muted', 'q'),
                              ('    r 刷新    统计不申请目标控制权', 'muted')]]
    else:
        if width >= 68:
            for i in range(4):
                left = ('›' if selected == i else ' ') + f' {i + 1}  {MENU[i]}'
                right = (('›' if selected == i + 4 else ' ') + f' {i + 5}  {MENU[i + 4]}') if i + 4 < len(MENU) else ''
                lines.append([Control('  ' + pad(left, 32), 'selected' if selected == i else None, str(i + 1)),
                              Control(right, 'selected' if selected == i + 4 else None, str(i + 5))])
        else:
            for i, label in enumerate(MENU):
                lines.append(Control(f'  {"›" if selected == i else " "} {i + 1}  {label}',
                                     'selected' if selected == i else None, str(i + 1)))
        lines += [[('  ↑↓←→选择 Enter打开  ' if width < 48 else '  ↑↓←→ 选择  Enter 打开  数字快捷键  ', 'muted'),
                   Control('q 退出', 'muted', 'q')],
                  ('  左键打开  滚轮选择  右键返回', 'muted')]
    if message:
        lines.append(('  ' + message, 'warning'))
    return wordmark_rows(width, height - 1 - len(lines)) + lines
