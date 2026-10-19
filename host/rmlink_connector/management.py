"""Device management through the existing connector, without a target lease."""
import asyncio
import os
import re
import socket
import struct
import time

from rmlink_native_usb import UsbClient
from .discovery import Device
from .target import TargetAccessError
from .ota import FirmwareUpdates
from .firmware import repository_name


class ManagementError(ValueError):
    pass


def network_status(reply):
    if len(reply) != 10 or reply[:3] != b'\x80\x04\x00':
        raise ConnectionError('设备未返回有效的网络状态')
    return dict(configured=bool(reply[3]), connected=bool(reply[4]),
                rssi_dbm=struct.unpack('b', reply[5:6])[0] if reply[4] else None,
                ip=socket.inet_ntoa(reply[6:10]))


def provision(exchange, ssid, password):
    """Validate both UTF-8 fields before the first write. Never expose secrets."""
    if not isinstance(ssid, str) or not isinstance(password, str):
        raise ManagementError('请提供 SSID 和密码')
    fields = (ssid.encode('utf-8'), password.encode('utf-8'))
    if not 1 <= len(fields[0]) <= 32 or not (len(fields[1]) == 0 or 8 <= len(fields[1]) <= 63):
        raise ManagementError('SSID 需为 1–32 字节；密码需为 8–63 字节，开放网络可留空')
    if any(b'\0' in field for field in fields):
        raise ManagementError('网络名称和密码不能包含 NUL')
    for field, data in enumerate(fields):
        for offset in range(0, max(1, len(data)), 48):
            chunk = data[offset:offset + 48]
            reply = exchange(bytes([0x80, 2, field, offset, len(data), len(chunk)]) + chunk)
            if reply != b'\x80\x02\x00':
                raise ConnectionError('USB 配网片段被拒绝，未确认保存成功')
    if exchange(b'\x80\x03') != b'\x80\x03\x00':
        raise ConnectionError('设备未确认保存 Wi-Fi 配置；请查询网络状态后再操作')
    return dict(saved=True, message='Wi-Fi 参数已保存，设备正在连接；请稍后查看网络状态')


class Management:
    def __init__(self, connector):
        self.connector = connector
        self.firmware = FirmwareUpdates(connector, self.idle)

    def idle(self, key):
        c = self.connector
        broker = c.brokers.get(key)
        if (broker and (broker.owner or broker.maintenance)) or any(s['device_id'] == key for s in c.sessions.values()):
            raise TargetAccessError('设备正在被调试会话使用，请先结束该会话', 'TargetBusy')

    def saved(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{12}', key):
            raise ManagementError('请选择有效的设备编号')
        saved = self.connector.state.data['paired'].get(key)
        if saved is None:
            raise ManagementError('请先配对设备')
        return saved

    async def scan_usb(self):
        def scan():
            found, excluded = [], []
            for _ in range(16):
                try:
                    client = UsbClient(bcd_device=0x0002, exclude_serials=excluded)
                except RuntimeError:
                    break  # Includes no free physical interface. Never open a second owner.
                with client:
                    excluded.append(client.serial)
                    identity, info, network = client.exchange_many([b'\x80\x09', b'\x80\x00', b'\x80\x04'])
                    if len(identity) != 9 or identity[:3] != b'\x80\x09\x00' or len(info) != 17 or info[:4] != b'\x80\x00\x00\x01':
                        continue
                    key = identity[3:].hex()
                    found.append(dict(device_id=key, hostname='rmlink-' + key[-6:] + '.local',
                                      address=network_status(network)['ip'], port=3241,
                                      firmware='.'.join(map(str, info[4:7])), usb_serial=client.serial))
            return found
        if os.name != 'nt':
            return dict(devices=[], supported=False)
        async with self.connector.refresh_lock:
            rows = await asyncio.to_thread(scan)
            for row in rows:
                key = row['device_id']
                self.connector.devices[key] = Device(**{k: row[k] for k in Device.__dataclass_fields__})
                self.connector.usb_serials[key] = row['usb_serial']
            return dict(devices=rows, supported=True)

    async def configure_wifi(self, key, body):
        c = self.connector
        # Validate before opening USB or scheduling any network change.
        commands = []
        provision(lambda command: commands.append(command) or bytes([0x80, command[1], 0]),
                  body.get('ssid'), body.get('password'))
        async with c.refresh_lock:
            self.idle(key)
            c.switching.add(key)
            try:
                broker = c.brokers.get(key)
                if broker and getattr(broker.transport, 'native_usb', False):
                    info=(await broker.submit('wifi-status',[b'\x80\x00'],'background'))[0]
                    if len(info)!=17 or info[10]:
                        raise TargetAccessError('设备存在活动目标会话，请先结束调试', 'TargetBusy')
                    replies=await broker.submit('wifi-config',commands[:-1],'background')
                    for command,reply in zip(commands[:-1],replies):
                        if reply != bytes([0x80, command[1], 0]):
                            raise ConnectionError('USB 配网未获确认；请查询设备状态，不自动重放')
                    reply=(await broker.submit('wifi-config',[commands[-1]],'background'))[0]
                    if reply != b'\x80\x03\x00':
                        raise ConnectionError('设备未确认保存 Wi-Fi 配置，请查询状态；不自动重放')
                    return dict(saved=True, message='Wi-Fi 参数已保存，设备正在连接')
                serial = c.usb_serials.get(key) or c.state.data['paired'].get(key, {}).get('usb_serial', key.upper())
                def write():
                    with UsbClient(bcd_device=0x0002, serial=serial) as client:
                        if client.exchange(b'\x80\x09') != b'\x80\x09\x00' + bytes.fromhex(key):
                            raise ConnectionError('USB 设备身份不匹配，未写入网络配置')
                        info=client.exchange(b'\x80\x00')
                        if len(info)!=17 or info[10]:
                            raise TargetAccessError('设备存在活动目标会话，请先结束调试', 'TargetBusy')
                        return provision(client.exchange, body.get('ssid'), body.get('password'))
                return await asyncio.to_thread(write)
            finally:
                c.switching.discard(key)

    def metrics(self, key):
        c = self.connector
        broker = c.brokers.get(key)
        traffic = getattr(broker.transport, 'traffic', None) if broker else None
        subscriptions = []
        for session in c.sessions.values():
            capture = session.get('captures') if session['device_id'] == key else None
            if not capture:
                continue
            for sid, config in capture.configs.items():
                observed = capture.observed.get(sid, {})
                subscriptions.append(dict(subscription_id=sid, kind=config['kind'],
                                          requested_hz=1e6 / config['period_us'],
                                          field_count=len(config['fields']),
                                          board_status=capture.last_status.get(sid), **observed))
        return dict(device_id=key, connected=bool(broker and not broker.failed),
                    traffic=traffic.snapshot() if traffic and not broker.failed else None,
                    subscriptions=subscriptions, link_losses=c.link_losses.get(key,0), observed_at=time.monotonic())

    async def route(self, method, path, body):
        c = self.connector
        if path == '/v1/preferences':
            if method == 'GET':
                return dict(c.state.data.get('preferences', {}))
            if method == 'POST':
                if set(body) - {'default_device', 'firmware_repository'}:
                    raise ManagementError('不支持的客户端设置')
                key = body.get('default_device')
                if key is not None:
                    self.saved(key)
                if 'firmware_repository' in body:
                    repository_name(body['firmware_repository'])
                c.state.data.setdefault('preferences', {}).update(body)
                c.state.save()
                return dict(c.state.data['preferences'])
        if method == 'GET' and path == '/v1/usb-devices':
            return await self.scan_usb()
        if method == 'POST' and path in ('/v1/connect', '/v1/disconnect', '/v1/unpair', '/v1/alias'):
            key = body.get('device_id')
            saved = self.saved(key)
            if path == '/v1/alias':
                alias = body.get('alias')
                if not isinstance(alias, str) or len(alias) > 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in alias):
                    raise ManagementError('别名最多 32 个字符，不能包含控制字符')
                saved['alias'] = alias.strip()
                c.state.save()
                return dict(device_id=key, alias=saved['alias'])
            async with c.refresh_lock:
                self.idle(key)
                if path == '/v1/connect':
                    saved['auto_connect'] = True
                    c.state.save()
                    await c._refresh()
                    return dict(device_id=key, connected=key in c.brokers and not c.brokers[key].failed,
                                error=c.connection_errors.get(key))
                broker = c.brokers.pop(key, None)
                if broker:
                    await broker.close()
                if path == '/v1/disconnect':
                    saved['auto_connect'] = False
                else:
                    del c.state.data['paired'][key]
                    prefs = c.state.data.get('preferences', {})
                    if prefs.get('default_device') == key:
                        prefs.pop('default_device')
                c.state.save()
                return dict(device_id=key, connected=False, paired=path != '/v1/unpair')
        parts = path.split('/')
        if len(parts) == 5 and parts[1:3] == ['v1', 'devices']:
            key, action = parts[3:]
            if key not in c.devices and key not in c.state.data['paired']:
                raise ManagementError('未找到该设备，请刷新设备列表或通过 USB 发现')
            if method == 'GET' and action == 'metrics':
                return self.metrics(key)
            if method == 'GET' and action == 'firmware':
                return await self.firmware.info(key)
            if method == 'POST' and action == 'firmware':
                return await self.firmware.route(key, body)
            if method == 'POST' and action == 'wifi':
                return await self.configure_wifi(key, body)
            if method == 'GET' and action == 'wifi':
                broker = c.brokers.get(key)
                if broker and not broker.failed:
                    return network_status((await broker.submit('network-status', [b'\x80\x04'], 'background'))[0])
                def read():
                    serial = c.usb_serials.get(key, key.upper())
                    with UsbClient(bcd_device=0x0002, serial=serial) as client:
                        if client.exchange(b'\x80\x09') != b'\x80\x09\x00' + bytes.fromhex(key):
                            raise ConnectionError('USB 设备身份不匹配')
                        return network_status(client.exchange(b'\x80\x04'))
                return await asyncio.to_thread(read)
        raise ManagementError('不支持的设备管理操作')

    @staticmethod
    def handles(path):
        return path in ('/v1/preferences', '/v1/usb-devices', '/v1/connect', '/v1/disconnect',
                        '/v1/unpair', '/v1/alias') or (
            path.startswith('/v1/devices/') and path.rsplit('/', 1)[-1] in ('metrics', 'wifi', 'firmware'))
