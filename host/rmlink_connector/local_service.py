"""Per-user connector lifecycle for the terminal client. No system service required."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error

from .client import ApiClient, RMLinkError
from .state import default_directory


class ClientError(RuntimeError):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def hidden_process_options():
    return {'creationflags': 0x08000000} if os.name == 'nt' else {}


def windows_powershell():
    return str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


def powershell_environment():
    env = dict(os.environ)
    # A Python child bypasses PowerShell 7's usual sanitization when launching
    # Windows PowerShell. Mixing its modules causes duplicate security TypeData.
    env['PSModulePath'] = os.pathsep.join([
        str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'),
        str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WindowsPowerShell/Modules')])
    return env


def run_powershell_script(script, arguments=(), timeout=20):
    # PyInstaller's DLL search directory is inherited by Windows subprocesses.
    # External system tools must resolve their own system DLLs instead.
    kernel = None
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW(None)
    try:
        return subprocess.run([windows_powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(script), *arguments], env=powershell_environment(),
                              capture_output=True, encoding='utf-8', errors='replace',
                              timeout=timeout, **hidden_process_options())
    finally:
        if kernel:
            kernel.SetDllDirectoryW(sys._MEIPASS)


class LocalService:
    def __init__(self, directory=None, api_port=3243, usbip_port=3240):
        self.directory = Path(directory) if directory else default_directory()
        self.api_port, self.usbip_port = api_port, usbip_port
        # Keep logs shared with ordinary terminals; packaged hosts virtualize AppData.
        self.logs = self.directory / 'logs'

    def client(self, timeout=30):
        if not (self.directory / 'connector.json').is_file():
            raise FileNotFoundError('后台服务尚未启动')
        return ApiClient(self.directory, self.api_port, timeout=timeout)

    def status(self):
        try:
            return self.client(timeout=1).request('GET', '/v1/status')
        except (FileNotFoundError, urllib.error.URLError, TimeoutError, ConnectionError):
            return None
        except RMLinkError as error:
            raise ClientError('本机端口已有服务，但凭据不匹配；请运行 rmlink doctor 检查') from error

    def request(self, method, path, body=None):
        return self.client().request(method, path, body)

    def command(self):
        args = ['--state-dir', str(self.directory), '--api-port', str(self.api_port),
                '--usbip-port', str(self.usbip_port), 'serve']
        if getattr(sys, 'frozen', False):
            executable = Path(sys.executable).with_name('RMLinkConnector.exe')
            if not executable.is_file():
                raise ClientError('缺少配套 RMLinkConnector.exe，请重新安装客户端')
            return [str(executable), *args]
        return [sys.executable, '-m', 'rmlink_connector', *args]

    @contextlib.contextmanager
    def startup_lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'client-start.lock').open('a+b') as handle:
            # Locking beyond EOF is supported on Windows. Do not write the
            # locked byte before acquiring it: a concurrent launcher may own it.
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                deadline = time.monotonic() + 15
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ClientError('另一个客户端正在启动服务，请稍后重试')
                        time.sleep(.1)
                try:
                    yield
                finally:
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def start(self):
        current = self.status()
        if current:
            return current
        with self.startup_lock():
            current = self.status()
            if current:
                return current
            if os.name == 'nt':
                account = os.environ.get('USERDOMAIN', '') + '\\' + os.environ.get('USERNAME', '')
                subprocess.run(['icacls.exe', str(self.directory), '/inheritance:r', '/grant:r',
                                account + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
                               check=True, capture_output=True, **hidden_process_options())
            self.logs.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
            env['PYTHONIOENCODING'] = 'utf-8'
            with (self.logs / 'service.log').open('ab') as out, (self.logs / 'service-error.log').open('ab') as err:
                process = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                           env=env, cwd=self.directory, **hidden_process_options())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                current = self.status()
                if current:
                    return current
                if process.poll() is not None:
                    raise ClientError('服务启动失败，请运行 rmlink logs 查看原因')
                time.sleep(.15)
            raise ClientError('服务启动超时，请运行 rmlink doctor 检查；未重复启动服务')

    def stop(self):
        status = self.status()
        if not status:
            return dict(running=False)
        if any(d.get('owner') for d in status['devices']):
            raise ClientError('设备仍被调试会话占用，请先结束调试再停止后台服务')
        self.request('POST', '/v1/shutdown')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not self.status():
                return dict(running=False)
            time.sleep(.1)
        raise ClientError('服务正在清理连接，请稍后查看状态')

    def autostart(self, enabled=None):
        if os.name != 'nt':
            raise ClientError('登录自启目前仅支持 Windows')
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if enabled is not None:
                if enabled:
                    executable = Path(sys.executable)
                    script = executable.parent.parent / 'Start-Service.ps1'
                    if not getattr(sys, 'frozen', False) or not script.is_file():
                        raise ClientError('请先运行 Install-Connector.ps1 -Install 安装客户端')
                    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
                    value = subprocess.list2cmdline([str(powershell), '-NoProfile', '-WindowStyle', 'Hidden',
                                                    '-ExecutionPolicy', 'Bypass', '-File', str(script)])
                    winreg.SetValueEx(key, 'RMLinkConnector', 0, winreg.REG_SZ, value)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        winreg.DeleteValue(key, 'RMLinkConnector')
            try:
                winreg.QueryValueEx(key, 'RMLinkConnector')
                return dict(start_at_logon=True)
            except FileNotFoundError:
                return dict(start_at_logon=False)


def inspect_firmware(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.bin':
        raise ClientError('请选择 RMLink 应用固件 .bin 文件')
    size = path.stat().st_size
    if not 24 <= size <= 16 * 1024 * 1024:
        raise ClientError('固件文件长度不符合 ESP32 应用镜像范围')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        header = stream.read(24)
        if header[0] != 0xe9 or not 1 <= header[1] <= 16:
            raise ClientError('文件不是有效的 ESP32 镜像头')
        if int.from_bytes(header[12:14], 'little') != 9:
            raise ClientError('该镜像的目标芯片不是 ESP32-S3')
        digest.update(header)
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return dict(path=str(path), bytes=size, sha256=digest.hexdigest(), chip='ESP32-S3',
                scope='header and SHA256 only; board profile, partition fit and signature not verified',
                upgrade_supported=False)
