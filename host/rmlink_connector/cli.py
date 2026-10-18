"""RMLink terminal client. UI and scripts share the same management operations."""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error

from . import __version__
from .client import RMLinkError
from .console import Terminal, dashboard, safe_text, rate
from .local_service import ClientError, LocalService, inspect_firmware, run_powershell_script
from .telemetry import Rates
from .firmware import DEFAULT_REPOSITORY, FirmwareError, image_info, inspect_image, latest_release, download_release, repository_name


def shared(parser, defaults=False):
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument('--device', default=default, help='设备编号；省略时使用默认设备或唯一设备')
    parser.add_argument('--json', action='store_true', default=False if defaults else argparse.SUPPRESS, help='输出 JSON，便于脚本处理')
    parser.add_argument('--no-color', action='store_true', default=False if defaults else argparse.SUPPRESS, help='关闭颜色')
    parser.add_argument('--state-dir', default=default, help=argparse.SUPPRESS)
    parser.add_argument('--api-port', type=int, default=3243 if defaults else argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument('--usbip-port', type=int, default=3240 if defaults else argparse.SUPPRESS, help=argparse.SUPPRESS)


def parser():
    root = argparse.ArgumentParser(prog='rmlink', description='RMLink 设备控制台 · 不带参数进入交互界面')
    shared(root, True)
    root.add_argument('--version', action='version', version='RMLink ' + __version__)
    root.add_argument('--preview', action='store_true', help='输出界面快照，不启动后台服务')
    commands = root.add_subparsers(dest='command', metavar='命令')
    def add(parent, name, help):
        result = parent.add_parser(name, help=help, description=help)
        shared(result)
        return result
    add(commands, 'devices', '发现设备并列出配对、连接和挂载状态')
    add(commands, 'status', '查看后台服务与设备状态')
    add(commands, 'info', '查看设备信息和健康状态')
    for name, help in [('pair', '配对设备'), ('unpair', '取消配对'), ('connect', '连接设备'),
                       ('disconnect', '断开设备并暂停自动重连'), ('mount', '挂载为虚拟 USB 调试器'),
                       ('unmount', '卸载虚拟 USB 调试器')]:
        item = add(commands, name, help)
        item.add_argument('device_id', nargs='?', help='设备编号')
        if name == 'connect':
            item.add_argument('--transport', choices=('usb', 'wifi'), help='选择有线 USB 或 Wi-Fi')
    wifi = add(commands, 'wifi', '配置或查看设备 Wi-Fi')
    wifi_sub = wifi.add_subparsers(dest='action', metavar='操作')
    add(wifi_sub, 'status', '查看连接状态、IP 和信号强度')
    add(wifi_sub, 'hotspot', '检查电脑热点状态与频段，不读取密码')
    configure = add(wifi_sub, 'configure', '通过 USB 保存 Wi-Fi 参数；密码隐藏输入')
    configure.add_argument('--ssid', help='2.4 GHz 网络名称')
    passwords = configure.add_mutually_exclusive_group()
    passwords.add_argument('--password-stdin', action='store_true', help='从标准输入读取密码，不放入命令行')
    passwords.add_argument('--open-network', action='store_true', help='显式配置无密码的开放网络')
    monitor = add(commands, 'monitor', '被动查看实时收发速率和活动订阅')
    monitor.add_argument('--interval', type=float, default=1, help='刷新间隔，0.25–10 秒')
    monitor.add_argument('--count', type=int, default=0, help='输出指定次数后退出；0 为持续监控')
    driver = add(commands, 'driver', '检测、安装或维护虚拟 USB 驱动')
    driver_sub = driver.add_subparsers(dest='action', metavar='操作')
    add(driver_sub, 'status', '核对已安装客户端和内核驱动状态')
    for name in ('install', 'repair'):
        item = add(driver_sub, name, '从有 SHA256 清单的本地驱动包安装；由 Windows 请求提权')
        item.add_argument('--package', required=True, help='已审核驱动包的 driver-package.json')
    uninstall = add(driver_sub, 'uninstall', '卸载明确指定的 USBip 驱动包')
    uninstall.add_argument('--published-name', required=True, help='Windows 发布名，例如 oem123.inf')
    firmware = add(commands, 'firmware', 'RMLink 自身固件检查、GitHub 获取与 OTA 升级')
    fw_sub = firmware.add_subparsers(dest='action', metavar='操作')
    add(fw_sub, 'status', '查看当前固件版本与升级支持状态')
    inspect = add(fw_sub, 'inspect', '离线校验应用镜像、版本、硬件配置及 SHA256')
    inspect.add_argument('file')
    latest = add(fw_sub, 'latest', '查询 GitHub 最新正式 Release 与固件清单')
    latest.add_argument('--repo', default=DEFAULT_REPOSITORY, help='GitHub 仓库 owner/repo')
    latest.add_argument('--prerelease', action='store_true', help='显式包含 GitHub 测试版固件')
    update = add(fw_sub, 'update', '通过 USB 或 Wi-Fi 写入备用分区，校验并重启确认')
    update.add_argument('file', nargs='?')
    update.add_argument('--latest', action='store_true', help='从 GitHub 获取与设备配置一致的最新固件')
    update.add_argument('--repo', help='GitHub 仓库 owner/repo；省略时使用客户端设置')
    update.add_argument('--prerelease', action='store_true', help='与 --latest 一起使用，显式包含测试版')
    update.add_argument('--yes', action='store_true', help='确认安装并重启 RMLink；脚本调用必需')
    update.add_argument('--no-wait', action='store_true', help='提交后返回，由 firmware status 查询启动结果')
    service = add(commands, 'service', '管理每用户后台连接服务')
    service_sub = service.add_subparsers(dest='action', metavar='操作')
    for name, help in [('status', '查看服务状态'), ('start', '启动服务，已运行时复用'),
                       ('stop', '停止服务；活动调试期间拒绝停止'), ('restart', '停止后重新启动服务')]:
        add(service_sub, name, help)
    auto = add(service_sub, 'autostart', '查看或设置登录自动启动')
    auto.add_argument('mode', nargs='?', choices=('on', 'off', 'status'), default='status')
    logs = add(commands, 'logs', '查看近期服务日志')
    logs.add_argument('--lines', type=int, default=40)
    doctor = add(commands, 'doctor', '检查服务、发现、连接、驱动与挂载环节')
    doctor.add_argument('--output', help='导出脱敏诊断 JSON，不覆盖已有文件')
    config = add(commands, 'config', '查看设置或设置默认设备、设备别名')
    config_sub = config.add_subparsers(dest='action', metavar='操作')
    default = add(config_sub, 'default', '设置默认设备')
    default.add_argument('device_id')
    alias = add(config_sub, 'alias', '设置设备别名')
    alias.add_argument('name')
    recording = add(commands, 'inspect-recording', '离线检查 .rmlrec 录制文件')
    recording.add_argument('file')
    recording.add_argument('--anomalies', action='store_true')
    return root


class Controller:
    def __init__(self, args):
        self.args = args
        self.runtime = LocalService(args.state_dir, args.api_port, args.usbip_port)
        self.device_id = args.device or getattr(args, 'device_id', None)
        self.rates = Rates()

    def call(self, method, path, body=None):
        return self.runtime.request(method, path, body)

    def select(self, status=None):
        status = status or self.runtime.status()
        devices = status.get('devices', []) if status else []
        preferred = self.device_id
        if not preferred:
            preferred = self.call('GET', '/v1/preferences').get('default_device')
        if preferred:
            match = next((d for d in devices if d['device_id'] == preferred.lower()), None)
            if match:
                return match
            raise ClientError('找不到所选设备，请运行 rmlink devices 刷新列表')
        if len(devices) == 1:
            return devices[0]
        if not devices:
            raise ClientError('尚未发现设备，请连接 USB&OTG 接口，或确认电脑热点与设备 Wi-Fi 在线')
        raise ClientError('发现多台设备，请用 --device 指定，或用 rmlink config default 设置默认设备')

    def scan(self):
        result = self.call('GET', '/v1/usb-devices')
        return dict(devices=self.call('GET', '/v1/devices'), usb_scan_supported=result['supported'])

    def device_action(self, action, device=None, transport=None):
        device = device or self.select()
        key = device['device_id']
        if action == 'connect' and transport:
            self.call('POST', '/v1/transport', dict(device_id=key, transport=transport))
        return self.call('POST', '/v1/' + action, dict(device_id=key))

    def metrics(self, device):
        result = self.call('GET', f'/v1/devices/{device["device_id"]}/metrics')
        result['rates'] = self.rates.update(device['device_id'], result['traffic']) if result.get('traffic') else dict(rx_bps=None, tx_bps=None)
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
        data['band'] = {'TwoPointFourGigahertz': '2.4 GHz', 'FiveGigahertz': '5 GHz', 'Auto': '自动'}.get(data.get('band'), data.get('band'))
        return data

    def driver(self, action='status', package=None, published_name=None):
        if os.name != 'nt':
            raise ClientError('驱动管理目前仅支持 Windows')
        if action != 'status':
            current = self.runtime.status()
            if current and any(d.get('owner') for d in current['devices']):
                raise ClientError('请先结束活动调试会话，再维护驱动')
        script = (Path(sys.executable).parent / 'Driver.ps1' if getattr(sys, 'frozen', False) else
                  Path(__file__).resolve().parents[1] / 'distribution' / 'Driver.ps1')
        args = ['-Action', action]
        if package:
            args += ['-ManifestPath', str(Path(package).expanduser().resolve())]
        if published_name:
            args += ['-PublishedName', published_name]
        result = run_powershell_script(script, args, timeout=120 if action != 'status' else 20)
        if result.returncode not in (0, 3010):
            raise ClientError(result.stderr.strip() or '驱动操作未完成，请运行 rmlink doctor 检查')
        return json.loads(result.stdout.lstrip('\ufeff'))

    def diagnose(self):
        status = self.runtime.status()
        checks = [dict(name='后台服务', ok=bool(status), detail='运行中' if status else '未启动；运行 rmlink service start')]
        if status:
            checks += [dict(name='设备发现', ok=not status.get('discovery_error'), detail=status.get('discovery_error') or '正常'),
                       dict(name='虚拟 USB 客户端', ok=status['driver']['installed_cli'],
                            detail='已安装' if status['driver']['installed_cli'] else '未安装；有线直连与增强 API 可独立使用')]
            for device in status['devices']:
                key = device['device_id']
                checks.append(dict(name=device.get('alias') or key, ok=device['connected'],
                                   detail=status.get('connection_errors', {}).get(key) or ('已连接' if device['connected'] else '未连接')))
                if key in status.get('mount_errors', {}):
                    checks.append(dict(name='虚拟 USB 挂载', ok=False, detail=status['mount_errors'][key]))
        try:
            driver = self.driver()
        except (ClientError, OSError, subprocess.TimeoutExpired) as error:
            driver = dict(error=str(error))
        return dict(client_version=__version__, checks=checks, driver=driver, status=status,
                    note='未读取凭据文件内容，未获取目标控制权，未执行主动测速')


LABELS = {'device_id': '设备编号', 'hostname': '设备名称', 'alias': '别名', 'address': '地址',
          'firmware': '固件', 'version': '版本', 'paired': '已配对', 'connected': '已连接',
          'mounted': '虚拟 USB 已挂载', 'owner': '目标占用', 'transport': '传输', 'running': '服务运行中',
          'installed_cli': 'USBip 客户端已安装', 'supported': '支持', 'saved': '已保存',
          'default_device': '默认设备', 'start_at_logon': '登录自启', 'message': '说明', 'ip': 'IP',
          'configured': '已保存网络参数', 'rssi_dbm': '信号强度 / dBm', 'bytes': '字节数',
          'upgrade_supported': '支持自身升级', 'scope': '检查范围', 'path': '文件', 'state': '状态',
          'name': '名称', 'ok': '通过', 'detail': '详情', 'chip': '芯片', 'service': '服务',
          'services': '内核驱动', 'signature': '签名', 'managed_ports': '已管理端口',
          'virtual_port': '虚拟 USB 端口', 'error': '错误', 'completed': '已完成'}


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


def view(terminal, title, result, confirm=False):
    rows = human(result).splitlines()
    offset = 0
    while True:
        height = max(1, terminal.size().lines - (7 if confirm else 5))
        footer = ([('', None), ('  Enter 确认安装并重启 RMLink', 'selected'),
                   ('  q / Esc 取消    ↑↓ 查看固件信息', 'muted')] if confirm else
                  [('', None), ('  ↑↓ 滚动    q / Enter 返回', 'muted')])
        terminal.draw([(f'  {title}', 'accent'), ('', None)] + [('  ' + r, None) for r in rows[offset:offset + height]] +
                      footer)
        key = terminal.key()
        while not key:
            key = terminal.key()
        if key in ('q', 'enter', '\x1b'):
            return key == 'enter' if confirm else None
        if key == 'down':
            offset = min(max(0, len(rows) - height), offset + 1)
        if key == 'up':
            offset = max(0, offset - 1)


def upgrade_firmware(controller, terminal, file=None, latest=False, repository=None, yes=False, wait=True, prerelease=False):
    interactive = terminal.tty and getattr(getattr(controller,'args',None),'json',False) is not True
    if bool(file) == bool(latest):
        raise ClientError('请选择本地固件文件，或使用 --latest 获取 GitHub 最新版本', 2)
    if prerelease and not latest:
        raise ClientError('--prerelease 需与 --latest 一起使用', 2)
    image = inspect_image(file) if file else None
    device = controller.select()
    path = f'/v1/devices/{device["device_id"]}/firmware'
    status = controller.call('GET', path)
    if not status.get('supported'):
        raise ClientError(status.get('message') or '设备离线或尚未安装 OTA 引导程序，请先通过 USB 迁移', 3)
    if latest:
        repository = repository or controller.call('GET','/v1/preferences').get('firmware_repository') or DEFAULT_REPOSITORY
        release = latest_release(repository, include_prereleases=prerelease)
        if tuple(map(int,release['version'].split('.'))) < tuple(map(int,status['version'].split('.'))):
            return dict(state='up_to_date',version=status['version'],message='设备版本高于 GitHub 最新正式版本')
        image = download_release(release, status['profile'], controller.runtime.directory / 'firmware')
        image['发布类型'] = ('测试版（Pre-release）' if release['prerelease'] else
                            '测试通道（发布类型未确认）' if release['prerelease'] is None else '正式版')
    if image['profile'] != status['profile'] or image['bytes'] > status['max_bytes']:
        raise ClientError('固件硬件配置或分区大小与设备不匹配，未执行升级', 2)
    if image['elf_sha256'] == status['elf_sha256']:
        return dict(state='up_to_date',version=status['version'],message='设备已运行该固件')
    if not yes:
        if not interactive:
            raise ClientError('脚本升级需使用 --yes 确认安装并重启 RMLink', 2)
        if not view(terminal, '导入固件 · 安装确认',
                    dict(版本变更=f'{status["version"]} → {image["version"]}', **image), confirm=True):
            return dict(state='cancelled')
    def progress(percent, message, hint='请保持设备供电和连接'):
        if interactive:
            terminal.draw([('  固件升级', 'accent'), ('', None),
                (f'  {status["version"]} → {image["version"]}', 'strong'),
                (f'  上传进度  {"█"*(percent//5)}{"░"*(20-percent//5)}  {percent} %', 'selected'),
                ('', None), ('  ' + message, 'strong'), ('  ' + hint, 'muted')])
    progress(0, '正在检查固件完整性…')
    data = Path(image['path']).read_bytes()
    if image_info(data)['sha256'] != image['sha256']:
        raise FirmwareError('固件文件在确认后发生变化，未开始升级')
    progress(0, '正在准备设备…')
    upload = controller.call('POST',path,dict(action='begin',size=len(data),sha256=image['sha256'],
                                            profile=image['profile'],elf_sha256=image['elf_sha256']))
    committing = False
    try:
        progress(0, '正在上传固件…', 'Ctrl+C 取消上传')
        for offset in range(0,len(data),4096):
            chunk = data[offset:offset+4096]
            result = controller.call('POST',path,dict(action='write',upload_id=upload['upload_id'],
                offset=offset,data=base64.b64encode(chunk).decode('ascii')))
            if result.get('received') != offset + len(chunk):
                raise FirmwareError('后台服务未确认准确的上传位置，已停止')
            progress((offset+len(chunk))*100//len(data),
                     f'正在上传固件  {(offset+len(chunk))/1024:.0f} / {len(data)/1024:.0f} KiB', 'Ctrl+C 取消上传')
        progress(100, '上传完成，正在校验固件并准备重启…')
        committing = True
        result = controller.call('POST',path,dict(action='finish',upload_id=upload['upload_id']))
    except BaseException:
        if not committing:
            try: controller.call('POST',path,dict(action='abort',upload_id=upload['upload_id']))
            except Exception: pass
        raise
    if not wait:
        return result
    waiting_since=time.monotonic()
    deadline=waiting_since+90
    while time.monotonic()<deadline:
        progress(100, f'等待设备重连并确认新固件…  已等待 {int(time.monotonic()-waiting_since)} 秒')
        try:
            current=controller.call('GET',path)
            if current.get('elf_sha256')==image['elf_sha256'] and current.get('boot_state')==2:
                return dict(state='boot_confirmed',version=current['version'],elf_sha256=image['elf_sha256'])
        except (OSError, RMLinkError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise ClientError('已提交升级，但未确认新固件启动；请运行 rmlink firmware status 检查，勿重复提交', 3)


def firmware_result(terminal, result):
    if result.get('state') == 'cancelled':
        return
    confirmed = result.get('state') == 'boot_confirmed'
    view(terminal, '固件升级完成' if confirmed else '固件升级',
         dict(state='安装成功' if confirmed else result.get('state'), version=result.get('version'),
              message='设备已重启，新固件启动确认通过。' if confirmed else result.get('message', '')))


def submenu(terminal, title, options, description=''):
    selected = 0
    while True:
        choice = terminal.menu(title, options, selected=selected, description=description)
        if choice is None:
            return
        selected = next(i for i, option in enumerate(options) if option[0] == choice)
        yield choice


def ui_devices(controller, terminal):
    result = controller.scan()
    devices = result['devices']
    if not devices:
        return view(terminal, '设备管理', dict(message='未发现可用设备，请连接 USB&OTG 或检查 Wi-Fi / 电脑热点'))
    options = [(str(i + 1), f'{d.get("alias") or d["hostname"]}  ·  {d["device_id"]}  ·  '
                f'{"已配对" if d["paired"] else "未配对"}  ·  {"在线" if d["connected"] else "离线"}') for i, d in enumerate(devices)]
    answer = terminal.menu('设备管理', options)
    if answer is None:
        return
    device = devices[int(answer) - 1]
    controller.device_id = device['device_id']
    if not device['paired']:
        choice = terminal.menu('选择设备', [('s', '仅选择设备'), ('p', '配对并选择')], description=device['device_id'])
        if choice == 'p':
            controller.device_action('pair', device)
    else:
        for choice in submenu(terminal, '已选择设备', [('i', '设备信息'), ('u', '取消配对')], device['device_id']):
            if choice == 'u':
                view(terminal, '取消配对', controller.device_action('unpair', device))
                return
            elif choice == 'i':
                health = controller.call('GET', f'/v1/devices/{device["device_id"]}/health') if device['connected'] else None
                view(terminal, '设备信息', dict(device=device, health=health))


def ui_action(index, controller, terminal):
    if index == 0:
        return ui_devices(controller, terminal)
    if index == 1:
        for choice in submenu(terminal, 'Wi-Fi 配置', [('s', '设备网络状态'), ('c', '通过 USB 配网'), ('h', '电脑热点状态')]):
            if choice == 'h':
                view(terminal, '电脑热点', controller.hotspot())
                continue
            controller.scan()
            device = controller.select()
            path = f'/v1/devices/{device["device_id"]}/wifi'
            if choice == 's':
                view(terminal, '网络状态', controller.call('GET', path))
            elif choice == 'c':
                ssid = terminal.prompt('  2.4 GHz 网络名称 › ')
                password = terminal.prompt('  密码（隐藏输入；开放网络留空） › ', secret=True)
                view(terminal, 'Wi-Fi 配置', controller.call('POST', path, dict(ssid=ssid, password=password)))
    elif index == 2:
        device = controller.select()
        options = [('c', '连接设备'), ('d', '断开连接'), ('m', '挂载虚拟 USB'), ('u', '卸载虚拟 USB'),
                   ('1', '切换到 USB'), ('2', '切换到 Wi-Fi')]
        for choice in submenu(terminal, '连接与挂载', options):
            if choice in ('c', 'd', 'm', 'u'):
                action = {'c': 'connect', 'd': 'disconnect', 'm': 'mount', 'u': 'unmount'}[choice]
                view(terminal, '连接与挂载', controller.device_action(action, device))
            elif choice in ('1', '2'):
                view(terminal, '连接与挂载', controller.device_action('connect', device, 'usb' if choice == '1' else 'wifi'))
    elif index == 3:
        monitor(controller, terminal)
    elif index == 4:
        options = [('s', '查看驱动状态'), ('i', '安装已审核驱动包'), ('r', '修复驱动')]
        for choice in submenu(terminal, '驱动管理', options, '驱动维护时 Windows 将请求管理员权限。'):
            if choice == 's':
                view(terminal, '驱动状态', controller.driver())
            else:
                package = terminal.prompt('  driver-package.json 路径 › ').strip().strip('"')
                if package:
                    view(terminal, '驱动管理', controller.driver('install' if choice == 'i' else 'repair', package))
    elif index == 5:
        options = [('s','查看固件与升级状态'),('i','导入本地固件'),('g','从 GitHub 获取最新正式固件'),
                   ('t','从 GitHub 获取测试固件'),('r','设置 GitHub 固件仓库')]
        for choice in submenu(terminal,'固件升级',options):
            if choice=='s':
                device=controller.select()
                view(terminal,'RMLink 自身固件',controller.call('GET',f'/v1/devices/{device["device_id"]}/firmware'))
            elif choice=='r':
                repository=terminal.prompt('  GitHub 仓库 owner/repo › ').strip()
                if repository:
                    view(terminal,'固件仓库',controller.call('POST','/v1/preferences',dict(firmware_repository=repository_name(repository))))
            elif choice=='i':
                example = Path.home() / 'Downloads' / 'rmlink-0.3.1.bin'
                path=terminal.prompt('  RMLink 应用 .bin 路径 › ', hint=f'示例：{example}').strip().strip('"')
                if path:
                    firmware_result(terminal,upgrade_firmware(controller,terminal,file=path))
            elif choice=='g':
                firmware_result(terminal,upgrade_firmware(controller,terminal,latest=True))
            elif choice=='t':
                firmware_result(terminal,upgrade_firmware(controller,terminal,latest=True,prerelease=True))
    elif index == 6:
        for choice in submenu(terminal, '诊断与日志', [('d', '运行故障诊断'), ('l', '查看日志'), ('e', '导出诊断')]):
            if choice == 'd':
                view(terminal, '故障诊断', controller.diagnose())
            elif choice == 'l':
                view(terminal, '近期日志', read_logs(controller.runtime, 60))
            elif choice == 'e':
                path = terminal.prompt('  导出 JSON 路径 › ').strip().strip('"')
                if path:
                    export_report(controller.diagnose(), path)
                    view(terminal, '诊断已导出', dict(path=path))
    elif index == 7:
        options = [('d', '当前设备设为默认'), ('n', '设置设备别名'), ('a', '开启登录自启'), ('x', '关闭登录自启')]
        for choice in submenu(terminal, '设置', options):
            if choice in ('d', 'n'):
                device = controller.select()
                result = (controller.call('POST', '/v1/preferences', dict(default_device=device['device_id'])) if choice == 'd' else
                          controller.call('POST', '/v1/alias', dict(device_id=device['device_id'], alias=terminal.prompt('  设备别名 › '))))
                view(terminal, '设置已保存', result)
            elif choice in ('a', 'x'):
                view(terminal, '登录自启', controller.runtime.autostart(choice == 'a'))


def shell(controller, terminal):
    controller.runtime.start()
    selected, message = 0, ''
    with terminal.screen():
        while True:
            status = controller.runtime.status()
            device = metrics = None
            try:
                if status:
                    device = controller.select(status)
                    metrics = controller.metrics(device)
            except (ClientError, RMLinkError, urllib.error.URLError) as error:
                message = str(error)
            terminal.draw(dashboard(status, device, metrics, (metrics or {}).get('rates'), selected,
                                    terminal.size().columns, message, height=terminal.size().lines))
            key = terminal.key()
            while not terminal.vt and not key:
                key = terminal.key()
            if key in ('q', '\x1b'):
                return 0
            if key in ('up', 'down'):
                selected = (selected + (1 if key == 'down' else -1)) % 8
            if key in ('left', 'right'):
                step = 4 if terminal.size().columns >= 72 else 1
                selected = (selected + (step if key == 'right' else -step)) % 8
            if key in '12345678' and key:
                selected = int(key) - 1
            if key == 'enter' or key in '12345678' and key:
                try:
                    ui_action(selected, controller, terminal)
                    message = ''
                except (ClientError, RMLinkError, OSError, ValueError) as error:
                    view(terminal, '操作未完成', dict(message=str(error)))
                    message = ''


def monitor(controller, terminal, interval=1, count=0, as_json=False):
    if not math.isfinite(interval) or not .25 <= interval <= 10 or count < 0:
        raise ClientError('刷新间隔需为 0.25–10 秒，输出次数不能为负数', 2)
    if not terminal.vt or as_json or count:
        count = count or (0 if terminal.tty else 1)
        i = 0
        while count == 0 or i < count:
            result = controller.metrics(controller.select())
            if as_json:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            else:
                print(f'接收 {rate(result["rates"]["rx_bps"])}    发送 {rate(result["rates"]["tx_bps"])}', flush=True)
            i += 1
            if count == 0 or i < count:
                time.sleep(interval)
        return
    while True:
        status = controller.runtime.status()
        device = controller.select(status)
        metrics = controller.metrics(device)
        terminal.draw(dashboard(status, device, metrics, metrics['rates'], width=terminal.size().columns,
                                monitor=True, height=terminal.size().lines))
        if terminal.key(interval) in ('q', 'enter', '\x1b'):
            return


def read_logs(runtime, lines):
    if not 1 <= lines <= 1000:
        raise ClientError('日志行数需为 1–1000', 2)
    from collections import deque
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


def run(args, controller, terminal):
    command = args.command
    if args.preview:
        status = controller.runtime.status()
        device = None
        try:
            if status:
                device = controller.select(status)
        except ClientError:
            pass
        if args.json:
            return dict(version=__version__, service=status)
        terminal.draw(dashboard(status, device, width=terminal.size().columns, height=terminal.size().lines))
        return None
    if command == 'firmware' and args.action == 'inspect':
        return inspect_image(args.file)
    if command == 'firmware' and args.action == 'latest':
        return latest_release(args.repo, include_prereleases=args.prerelease)
    if command == 'firmware' and args.action == 'update' and bool(args.file) == bool(args.latest):
        raise ClientError('请选择本地固件文件，或使用 --latest',2)
    if command == 'inspect-recording':
        from .recording_audit import audit_recording
        from .recording_reader import inspect_recording
        return audit_recording(args.file) if args.anomalies else inspect_recording(args.file)
    if command == 'logs':
        return read_logs(controller.runtime, args.lines)
    if command == 'wifi' and args.action == 'hotspot':
        return controller.hotspot()
    if command == 'doctor':
        result = controller.diagnose()
        if args.output:
            export_report(result, args.output)
            result['exported_to'] = str(Path(args.output).resolve())
        return result
    if command == 'service':
        action = args.action or 'status'
        if action == 'autostart':
            return controller.runtime.autostart(None if args.mode == 'status' else args.mode == 'on')
        if action == 'stop':
            return controller.runtime.stop()
        if action == 'restart':
            controller.runtime.stop()
        status = controller.runtime.start() if action in ('start', 'restart') else controller.runtime.status()
        return dict(running=bool(status), version=status.get('version') if status else None)
    if command == 'driver':
        return controller.driver(args.action or 'status', getattr(args, 'package', None), getattr(args, 'published_name', None))
    if command == 'status':
        return controller.runtime.status() or dict(running=False, message='运行 rmlink service start 启动后台服务')
    if command is None and terminal.tty and sys.stdin.isatty() and not args.json:
        shell(controller, terminal)
        return None
    controller.runtime.start()
    if command is None:
        return controller.runtime.status()
    if command == 'devices':
        return controller.scan()
    if command in ('pair', 'unpair', 'connect', 'disconnect', 'mount', 'unmount'):
        if command == 'pair':
            controller.scan()
        return controller.device_action(command, transport=getattr(args, 'transport', None))
    if command == 'info':
        device = controller.select()
        health = controller.call('GET', f'/v1/devices/{device["device_id"]}/health') if device['connected'] else None
        return dict(device=device, health=health)
    if command == 'wifi':
        controller.scan()
        device = controller.select()
        path = f'/v1/devices/{device["device_id"]}/wifi'
        if args.action != 'configure':
            return controller.call('GET', path)
        ssid = args.ssid
        if ssid is None:
            if not sys.stdin.isatty():
                raise ClientError('脚本配网请提供 --ssid 和 --password-stdin（或 --open-network）', 2)
            ssid = input('2.4 GHz 网络名称 › ')
        if args.password_stdin:
            password = (sys.stdin.buffer.readline().decode('utf-8-sig') if hasattr(sys.stdin,'buffer') else
                        sys.stdin.readline()).rstrip('\r\n')
        elif args.open_network:
            password = ''
        elif sys.stdin.isatty():
            import getpass
            password = getpass.getpass('Wi-Fi 密码（隐藏输入） › ')
        else:
            raise ClientError('请用 --password-stdin 输入密码，或显式使用 --open-network', 2)
        return controller.call('POST', path, dict(ssid=ssid, password=password))
    if command == 'monitor':
        if terminal.tty and not args.json and not args.count:
            with terminal.screen():
                monitor(controller, terminal, args.interval, args.count, args.json)
        else:
            monitor(controller, terminal, args.interval, args.count, args.json)
        return None
    if command == 'firmware':
        if args.action == 'update':
            return upgrade_firmware(controller,terminal,args.file,args.latest,args.repo,args.yes,not args.no_wait,args.prerelease)
        device = controller.select()
        result = controller.call('GET', f'/v1/devices/{device["device_id"]}/firmware')
        return result
    if command == 'config':
        if args.action == 'default':
            return controller.call('POST', '/v1/preferences', dict(default_device=args.device_id.lower()))
        if args.action == 'alias':
            return controller.call('POST', '/v1/alias', dict(device_id=controller.select()['device_id'], alias=args.name))
        return controller.call('GET', '/v1/preferences')
    raise ClientError('未知操作', 2)


def main(argv=None):
    # Windows pipes otherwise inherit the legacy ANSI code page (often GBK).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    args = parser().parse_args(argv)
    terminal = Terminal(args.no_color)
    try:
        if not 1 <= args.api_port <= 65535 or not 1 <= args.usbip_port <= 65535:
            raise ClientError('端口需为 1–65535', 2)
        result = run(args, Controller(args), terminal)
        if result is not None:
            emit(result, args)
        return 0
    except KeyboardInterrupt:
        return 130
    except (ClientError, RMLinkError, OSError, ValueError, subprocess.SubprocessError) as error:
        code = error.code if isinstance(error, ClientError) else 1
        message = safe_text(error)
        if args.json:
            print(json.dumps(dict(error=message, exit_code=code), ensure_ascii=False))
        else:
            print('RMLink：' + message, file=sys.stderr)
        return code


if __name__ == '__main__':
    raise SystemExit(main())
