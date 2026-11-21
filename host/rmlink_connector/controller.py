"""Service controller, signal reader, diagnostics and log helpers for RMLink CLI/TUI."""
from collections import deque
import json
import os
from pathlib import Path
import sys
import threading
import time

from . import __version__
from .client import RMLinkError
from .firmware import repository_name
from .local_service import ClientError, LocalService, run_powershell_script
from .telemetry import Rates


class SignalReader:
    """Read Wi-Fi RSSI without blocking terminal input or the traffic display."""

    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.Lock()
        self.device_id = None
        self.generation = 0
        self.pending = False
        self.started = 0
        self.value = None
        self.observed = 0

    def sample(self, device):
        key = (device['device_id'] if device and device.get('connected')
               and device.get('transport', 'wifi') == 'wifi' else None)
        now = time.monotonic()
        with self.lock:
            if key != self.device_id:
                self.device_id = key
                self.generation += 1
                self.value = None
                self.observed = 0
            if key and not self.pending and now - self.started >= 1:
                self.pending = True
                self.started = now
                threading.Thread(target=self._read, args=(key, self.generation), daemon=True).start()
            return self.value if key and now - self.observed <= 3 else None

    def _read(self, key, generation):
        try:
            result = self.controller.call('GET', f'/v1/devices/{key}/wifi')
            value = result.get('rssi_dbm') if result.get('connected') else None
            # Firmware uses zero when it cannot read the AP information.
            if not isinstance(value, int) or not -127 <= value < 0:
                value = None
        except (ClientError, RMLinkError, OSError, ValueError):
            value = None
        with self.lock:
            if generation == self.generation:
                self.value = value
                self.observed = time.monotonic()
            self.pending = False


class Controller:
    PREFS_TTL = 5.0

    def __init__(self, args):
        self.args = args
        self.runtime = LocalService(args.state_dir, args.api_port)
        self.device_id = (args.device or getattr(args, 'device_id', None) or None)
        if self.device_id:
            self.device_id = self.device_id.lower()
        self.rates = Rates()
        self._prefs_cache = None
        self._prefs_at = 0.0

    def call(self, method, path, body=None):
        return self.runtime.request(method, path, body)

    def preferences(self, refresh=False):
        now = time.monotonic()
        if not refresh and self._prefs_cache is not None and now - self._prefs_at < self.PREFS_TTL:
            return dict(self._prefs_cache)
        prefs = self.call('GET', '/v1/preferences')
        self._prefs_cache = dict(prefs)
        self._prefs_at = now
        return dict(prefs)

    def invalidate_preferences(self):
        self._prefs_cache = None
        self._prefs_at = 0.0

    def default_device(self, refresh=False):
        try:
            value = self.preferences(refresh=refresh).get('default_device')
            return value.lower() if isinstance(value, str) and value else None
        except (ClientError, RMLinkError, OSError, ValueError):
            return None

    def try_select(self, status=None):
        """Resolve the active device without raising on empty or multi-device states.

        Returns (device, devices, reason) where reason is one of:
        'ok', 'none', 'ambiguous', 'not_found'.
        """
        status = status if status is not None else self.runtime.status()
        devices = status.get('devices', []) if status else []
        preferred = self.device_id
        if not preferred and devices:
            preferred = self.default_device()
        if preferred:
            target = preferred.lower()
            match = next((d for d in devices if d['device_id'] == target), None)
            if match:
                return match, devices, 'ok'
            return None, devices, 'not_found'
        if len(devices) == 1:
            return devices[0], devices, 'ok'
        if not devices:
            return None, devices, 'none'
        return None, devices, 'ambiguous'

    def select(self, status=None):
        device, devices, reason = self.try_select(status)
        if device is not None:
            return device
        if reason == 'not_found':
            raise ClientError('找不到所选设备，请运行 rmlink devices 刷新列表')
        if reason == 'none':
            raise ClientError('尚未发现设备，请连接 USB&OTG 接口，或确认电脑热点与设备 Wi-Fi 在线')
        raise ClientError('发现多台设备，请用 --device 指定，或用 rmlink config default 设置默认设备')

    def scan(self):
        result = self.call('GET', '/v1/usb-devices')
        return dict(devices=self.call('GET', '/v1/devices'), usb_scan_supported=result['supported'])

    def device_action(self, action, device=None, transport=None):
        device = device or self.select()
        key = device['device_id']
        if action == 'connect' and transport:
            return self.call('POST', '/v1/transport', dict(device_id=key, transport=transport, connect=True))
        result = self.call('POST', '/v1/' + action, dict(device_id=key))
        if action == 'unpair':
            self.invalidate_preferences()
            if self.device_id == key:
                self.device_id = None
        return result

    def set_default(self, device_id):
        result = self.call('POST', '/v1/preferences', dict(default_device=device_id.lower()))
        self._prefs_cache = dict(result)
        self._prefs_at = time.monotonic()
        return result

    def set_alias(self, device_id, alias):
        return self.call('POST', '/v1/alias', dict(device_id=device_id.lower(), alias=alias))

    def set_firmware_repository(self, repository):
        result = self.call('POST', '/v1/preferences', dict(firmware_repository=repository_name(repository)))
        self._prefs_cache = dict(result)
        self._prefs_at = time.monotonic()
        return result

    def metrics(self, device):
        result = self.call('GET', f'/v1/devices/{device["device_id"]}/metrics')
        result['rates'] = (self.rates.update(device['device_id'], result['traffic'])
                           if result.get('traffic') else dict(rx_bps=None, tx_bps=None))
        return result

    def hotspot(self):
        if os.name != 'nt':
            return dict(supported=False, message='请在电脑网络设置中开启 2.4 GHz 热点')
        script = (Path(sys.executable).parent / 'Hotspot.ps1' if getattr(sys, 'frozen', False) else
                  Path(__file__).resolve().parents[1] / 'distribution' / 'Hotspot.ps1')
        result = run_powershell_script(script)
        if result.returncode:
            raise ClientError('无法读取电脑热点状态，请在 Windows 网络设置中检查')
        data = json.loads(result.stdout.lstrip('\ufeff'))
        data['message'] = ('电脑热点已开启，请确认使用 2.4 GHz 频段' if data.get('connected') else
                           '请在 Windows 设置 → 网络和 Internet → 移动热点中开启 2.4 GHz 热点')
        data['band'] = {'TwoPointFourGigahertz': '2.4 GHz', 'FiveGigahertz': '5 GHz', 'Auto': '自动'}.get(
            data.get('band'), data.get('band'))
        return data

    def diagnose(self):
        status = self.runtime.status()
        checks = [dict(name='后台服务', ok=bool(status), detail='运行中' if status else '未启动；运行 rmlink service start')]
        if status:
            checks.append(dict(name='设备发现', ok=not status.get('discovery_error'),
                               detail=status.get('discovery_error') or '正常'))
            for device in status['devices']:
                key = device['device_id']
                checks.append(dict(name=device.get('alias') or key, ok=device['connected'],
                                   detail=status.get('connection_errors', {}).get(key) or ('已连接' if device['connected'] else '未连接')))
        return dict(client_version=__version__, checks=checks, status=status,
                    note='未读取凭据文件内容，未获取目标控制权，未执行主动测速')


def read_logs(runtime, lines):
    if not 1 <= lines <= 1000:
        raise ClientError('日志行数需为 1–1000', 2)
    result = {}
    for name in ('service.log', 'service-error.log'):
        path = runtime.logs / name
        if path.is_file():
            with path.open(encoding='utf-8', errors='replace') as stream:
                result[name] = ''.join(deque(stream, maxlen=lines)).strip() or '暂无日志'
    return result or dict(message='暂无日志；运行 rmlink service start 后自动记录')


def export_report(report, path):
    # The report is allowlisted. Never copy connector.json or arbitrary request bodies.
    with Path(path).expanduser().open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
