"""Console input records for Windows; no terminal or device state is owned here."""
import ctypes as C
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class MouseClick:
    x: int
    y: int


class Coord(C.Structure):
    _fields_ = [('X', C.c_short), ('Y', C.c_short)]


class Rect(C.Structure):
    _fields_ = [(name, C.c_short) for name in ('Left', 'Top', 'Right', 'Bottom')]


class ScreenInfo(C.Structure):
    _fields_ = [('size', Coord), ('cursor', Coord), ('attributes', C.c_ushort),
                ('window', Rect), ('maximum', Coord)]


class KeyRecord(C.Structure):
    _fields_ = [('down', C.c_int32), ('repeat', C.c_ushort), ('code', C.c_ushort),
                ('scan', C.c_ushort), ('char', C.c_wchar), ('controls', C.c_uint32)]


class MouseRecord(C.Structure):
    _fields_ = [('position', Coord), ('buttons', C.c_uint32),
                ('controls', C.c_uint32), ('flags', C.c_uint32)]


class Event(C.Union):
    _fields_ = [('key', KeyRecord), ('mouse', MouseRecord), ('size', Coord)]


class InputRecord(C.Structure):
    _anonymous_ = ('event',)
    _fields_ = [('kind', C.c_ushort), ('event', Event)]


class WindowsInput:
    """Keep the original input mode for prompts and restore it on every exit."""
    def __init__(self):
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.GetStdHandle.argtypes = [C.c_uint32]
        self.kernel.GetStdHandle.restype = C.c_void_p
        self.kernel.GetConsoleMode.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]
        self.kernel.SetConsoleMode.argtypes = [C.c_void_p, C.c_uint32]
        self.kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
        self.kernel.WaitForSingleObject.restype = C.c_uint32
        self.kernel.ReadConsoleInputW.argtypes = [C.c_void_p, C.POINTER(InputRecord),
                                                C.c_uint32, C.POINTER(C.c_uint32)]
        self.kernel.GetConsoleScreenBufferInfo.argtypes = [C.c_void_p, C.POINTER(ScreenInfo)]
        self.handle = self.kernel.GetStdHandle(-10 & 0xffffffff)
        self.output = self.kernel.GetStdHandle(-11 & 0xffffffff)
        original = C.c_uint32()
        if not self.kernel.GetConsoleMode(self.handle, C.byref(original)):
            raise C.WinError(C.get_last_error())
        self.original_mode = original.value
        # Extended flags are required to disable Quick Edit. ReadConsoleInputW
        # receives both native key records and mouse records, with VT input off.
        self.mode = (original.value | 0x98) & ~0x247
        self.buttons = 0
        self.repeats = 0
        self.repeated_key = ''

    def enable(self):
        if not self.kernel.SetConsoleMode(self.handle, self.mode):
            raise C.WinError(C.get_last_error())
        self.buttons = 0

    def restore(self):
        self.kernel.SetConsoleMode(self.handle, self.original_mode)

    def read(self, timeout):
        if self.repeats:
            self.repeats -= 1
            return self.repeated_key
        deadline = time.monotonic() + max(0, timeout)
        while True:
            remaining = max(0, deadline - time.monotonic())
            # Short waits allow Python's Ctrl+C handler to run promptly.
            result = self.kernel.WaitForSingleObject(self.handle, min(50, int(remaining * 1000)))
            if result == 0:
                record, count = InputRecord(), C.c_uint32()
                if not self.kernel.ReadConsoleInputW(self.handle, C.byref(record), 1, C.byref(count)):
                    raise C.WinError(C.get_last_error())
                if count.value:
                    key = self.decode(record)
                    if key:
                        return key
            elif result != 258:
                raise C.WinError(C.get_last_error())
            if time.monotonic() >= deadline:
                return ''

    def decode(self, record):
        if record.kind == 1 and record.key.down:
            key = {0x25: 'left', 0x26: 'up', 0x27: 'right', 0x28: 'down',
                   0x0d: 'enter', 0x1b: '\x1b'}.get(record.key.code, record.key.char.lower())
            if key == '\x03':
                raise KeyboardInterrupt
            if key and key != '\x00':
                self.repeated_key, self.repeats = key, max(0, record.key.repeat - 1)
                return key
        elif record.kind == 2:
            mouse = record.mouse
            previous, self.buttons = self.buttons, mouse.buttons & 0xffff
            if mouse.flags == 4:  # Signed high word is the vertical wheel delta.
                delta = C.c_short(mouse.buttons >> 16).value
                return 'up' if delta > 0 else 'down' if delta < 0 else ''
            if mouse.flags != 0:  # Ignore motion, horizontal wheel and double clicks.
                return ''
            pressed = self.buttons & ~previous
            if pressed & 2:
                return 'q'
            if pressed & 1:
                info = ScreenInfo()
                if self.kernel.GetConsoleScreenBufferInfo(self.output, C.byref(info)):
                    return MouseClick(mouse.position.X - info.window.Left,
                                      mouse.position.Y - info.window.Top)
        return ''
