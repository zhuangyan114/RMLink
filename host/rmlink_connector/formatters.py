"""Human-readable and JSON formatting helpers for the RMLink CLI and TUI."""
import json

from .console import safe_text

TRANSPORT_NAMES = {'auto': '自动', 'usb': 'USB', 'wifi': 'Wi-Fi'}

LABELS = {
    'device_id': '设备编号',
    'hostname': '设备名称',
    'alias': '别名',
    'address': '地址',
    'firmware': '固件版本',
    'version': '版本',
    'paired': '已配对',
    'connected': '已连接',
    'auto_connect': '自动重连',
    'owner': '目标占用',
    'transport': '传输',
    'preferred_transport': '传输偏好',
    'active_transport': '当前通道',
    'wifi_enabled': '板端 Wi-Fi 开关',
    'board_link': '板端链路状态',
    'connection_error': '连接错误',
    'connection_errors': '连接错误',
    'discovery_error': '发现错误',
    'packet_size': '单包长度',
    'running': '服务运行中',
    'supported': '支持',
    'saved': '已保存',
    'default_device': '默认设备',
    'firmware_repository': '固件仓库',
    'start_at_logon': '登录自启',
    'message': '说明',
    'ip': 'IP 地址',
    'ssid': '热点名称',
    'band': '频段',
    'clients': '已连客户端',
    'max_clients': '最大客户端数',
    'configured': '已保存网络参数',
    'rssi_dbm': '信号强度 / dBm',
    'bytes': '字节数',
    'max_bytes': '最大分区字节数',
    'upgrade_supported': '支持自身升级',
    'boot_state': '启动状态',
    'profile': '硬件配置指纹',
    'migration_profile': '兼容迁移指纹',
    'sha256': '镜像 SHA256',
    'elf_sha256': 'ELF SHA256',
    'tag': '发布标签',
    'prerelease': '测试版',
    'published_at': '发布时间',
    'release_url': '发布页面',
    'scope': '检查范围',
    'path': '文件',
    'exported_to': '导出路径',
    'state': '状态',
    'name': '名称',
    'ok': '通过',
    'detail': '详情',
    'chip': '芯片',
    'service': '服务',
    'devices': '设备列表',
    'usb_scan_supported': '支持 USB 扫描',
    'client_version': '客户端版本',
    'checks': '检查项',
    'status': '状态详情',
    'note': '备注',
    'device': '设备',
    'health': '健康状态',
    'error': '错误',
    'completed': '已完成',
    'current_version': '当前版本',
    'update_available': '有可用更新',
    'up_to_date': '已是最新',
    'signed': '已代码签名',
    'cached': '复用本地缓存',
    'silent': '静默安装',
    'file': '文件名',
    'size': '文件大小',
    'url': '下载地址',
    'repository': 'GitHub 仓库',
}


def device_title(device):
    if not device:
        return '--'
    return safe_text(device.get('alias') or device.get('hostname') or device['device_id'])


def transport_text(device):
    if not device:
        return '--'
    pref = device.get('preferred_transport') or device.get('transport') or 'wifi'
    active = device.get('active_transport') or (device.get('transport') if device.get('connected') else None)
    if pref == 'auto':
        if device.get('connected') and active in ('usb', 'wifi'):
            return f'自动 · {TRANSPORT_NAMES[active]}'
        return '自动'
    selected = active if device.get('connected') and active in ('usb', 'wifi') else pref
    return TRANSPORT_NAMES.get(selected, str(selected))


def preference_text(mode):
    return TRANSPORT_NAMES.get(mode, str(mode or '--'))


def human(value, indent=0):
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            label = LABELS.get(key, key)
            if isinstance(item, (dict, list)):
                rows += [' ' * indent + label + '：', human(item, indent + 2)]
            elif isinstance(item, str) and '\n' in item:
                rows += [' ' * indent + label + '：'] + [' ' * (indent + 2) + safe_text(line) for line in item.splitlines()]
            else:
                display = '是' if item is True else '否' if item is False else '--' if item is None else safe_text(item)
                rows.append(' ' * indent + label + '：' + display)
        return '\n'.join(rows)
    if isinstance(value, list):
        return '\n\n'.join(human(item, indent) for item in value) if value else ' ' * indent + '暂无'
    return ' ' * indent + safe_text(value)


def emit(result, args):
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human(result))
