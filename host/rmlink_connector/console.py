"""A small, dependency-free terminal surface with a quiet instrument-panel style."""
import contextlib
import os
import re
import shutil
import sys
import time
import unicodedata

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
        original = None
        if self.tty and os.name != 'nt':
            import termios
            import tty
            original = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        if self.vt:
            self.stream.write('\x1b[?1049h\x1b[?25l'); self.stream.flush()
        try:
            yield
        finally:
            if original is not None:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original)
            if self.vt:
                self.stream.write('\x1b[0m\x1b[?25h\x1b[?1049l'); self.stream.flush()
            if self.original_mode is not None:
                self.kernel.SetConsoleMode(self.handle, self.original_mode)

    def draw(self, lines):
        width, height = self.size()
        rows = []
        for line in lines[:max(1, height - 1)]:
            # A row can contain independently styled spans, such as menu columns.
            spans = line if isinstance(line, list) else [line]
            remaining, parts = max(1, width - 2), []
            for text, role in spans:
                text = fit(text, remaining)
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
            ch = sys.stdin.read(1)
            if ch == '\x1b' and select.select([sys.stdin], [], [], .03)[0]:
                return {'[A': 'up', '[B': 'down', '[D': 'left', '[C': 'right',
                        'OA': 'up', 'OB': 'down', 'OD': 'left', 'OC': 'right'}.get(sys.stdin.read(2), '')
            return 'enter' if ch in ('\n', '\r') else ch.lower()
        return ''

    def prompt(self, label, secret=False, hint=None):
        if hint:
            self.stream.write('\n' + self.paint('  ' + fit(hint, max(1, self.size().columns - 4)), 'muted') + '\n')
            self.stream.flush()
        if self.vt:
            self.stream.write('\x1b[?25h'); self.stream.flush()
        try:
            if secret:
                import getpass
                return getpass.getpass(label)
            return input(label)
        finally:
            if self.vt:
                self.stream.write('\x1b[?25l'); self.stream.flush()

    def menu(self, title, options, selected=0, description=''):
        options = [*options, (None, '返回上一级')]
        selected = min(max(0, selected), len(options) - 1)
        while True:
            self.draw(menu_rows(title, options, selected, self.size(), description))
            key = self.key()
            while not key:
                key = self.key()
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
        lines.append((f'  {"›" if index == selected else " "} {label}', 'selected' if index == selected else None))
    if len(options) > visible:
        lines.append((f'  {selected + 1} / {len(options)}', 'muted'))
    lines += [('', None), ('  ↑↓ 选择    Enter 打开    q / Esc 返回', 'muted')]
    return lines


def dashboard(status, device=None, metrics=None, rates=None, selected=0, width=88, message='', monitor=False, height=28):
    width = min(max(24, width - 4), 78)
    lines = [('  设备控制台', 'muted')]
    version = status.get('version', '--') if status else '--'
    lines += [('  ' + '─' * (width - 2), 'muted')]
    lines += [(f'  后台服务  {"运行中" if status else "未启动"}    ·    v{version}', 'good' if status else 'warning')]
    if device:
        name = device.get('alias') or device.get('hostname') or device['device_id']
        lines += [(f'  当前设备  {name}', 'strong'),
                  (f'  {device["device_id"]}  ·  {"USB" if device.get("transport") == "usb" else "Wi-Fi"}  ·  '
                   f'{"在线" if device.get("connected") else "离线"}', None),
                  (f'  目标占用  {"调试会话" if device.get("owner") else "空闲"}    固件  {device.get("firmware", "--")}', 'muted')]
    else:
        lines += [('  尚未选择设备', 'strong'), ('  按 1 发现并配对，或按 2 通过 USB 配置 Wi-Fi', 'muted'), ('', None)]
    traffic = (metrics or {}).get('traffic') or {}
    rates = rates or {}
    lines += [(f'  接收  {rate(rates.get("rx_bps"))}    发送  {rate(rates.get("tx_bps"))}', 'accent'),
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
        lines += [('', None), ('  q 返回    r 刷新    统计不申请目标控制权', 'muted')]
    else:
        if width >= 68:
            for i in range(4):
                left = ('›' if selected == i else ' ') + f' {i + 1}  {MENU[i]}'
                right = (('›' if selected == i + 4 else ' ') + f' {i + 5}  {MENU[i + 4]}') if i + 4 < len(MENU) else ''
                lines.append([('  ' + pad(left, 32), 'selected' if selected == i else None),
                              (right, 'selected' if selected == i + 4 else None)])
        else:
            for i, label in enumerate(MENU):
                lines.append((f'  {"›" if selected == i else " "} {i + 1}  {label}', 'selected' if selected == i else None))
        lines += [('', None), ('  ↑↓←→ 选择  Enter 打开  数字快捷键  q 退出', 'muted')]
    if message:
        lines.append(('  ' + message, 'warning'))
    return wordmark_rows(width, height - 1 - len(lines)) + lines
