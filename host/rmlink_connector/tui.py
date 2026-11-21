"""Interactive TUI flows, OTA confirmation UI and live monitor for RMLink."""
import base64
import json
import math
from pathlib import Path
import time
import urllib.error

from .client import RMLinkError
from .console import MENU, Control, dashboard, rate
from .controller import SignalReader, export_report, read_logs
from .firmware import DEFAULT_REPOSITORY, FirmwareError, download_release, image_info, inspect_image, latest_release
from .formatters import device_title, human, preference_text, transport_text
from .installer import download_installer, latest_installer_release, launch_installer
from .local_service import ClientError


class ExitForUpdate(Exception):
    """Signal the interactive shell to exit cleanly so the detached installer can upgrade."""


def view(terminal, title, result, confirm=False, confirm_label='确认安装并重启 RMLink'):
    rows = human(result).splitlines()
    offset = 0
    while True:
        height = max(1, terminal.size().lines - (7 if confirm else 5))
        footer = ([('', None), Control(f'  Enter {confirm_label}', 'selected', 'enter'),
                   [Control('  q / Esc 取消', 'muted', '\x1b'), ('    ↑↓ / 滚轮 滚动查看', 'muted')]] if confirm else
                  [('', None), [('  ↑↓ / 滚轮 滚动    ', 'muted'), Control('q / Enter 返回', 'muted', 'enter')]])
        terminal.draw([(f'  {title}', 'accent'), ('', None)] + [('  ' + r, None) for r in rows[offset:offset + height]] +
                      footer)
        key = terminal.key()
        if not key:
            continue
        if key in ('q', 'enter', '\x1b'):
            return key == 'enter' if confirm else None
        if key == 'down':
            offset = min(max(0, len(rows) - height), offset + 1)
        if key == 'up':
            offset = max(0, offset - 1)


def ask(terminal, label, secret=False, hint=None):
    """Read a line from the user in TUI mode; return None when cancelled."""
    try:
        return terminal.prompt(label, secret=secret, hint=hint)
    except (EOFError, KeyboardInterrupt):
        return None


def upgrade_firmware(controller, terminal, file=None, latest=False, repository=None, yes=False, wait=True,
                     prerelease=False, device=None):
    interactive = terminal.tty and getattr(getattr(controller, 'args', None), 'json', False) is not True
    if bool(file) == bool(latest):
        raise ClientError('请选择本地固件文件，或使用 --latest 获取 GitHub 最新版本', 2)
    if prerelease and not latest:
        raise ClientError('--prerelease 需与 --latest 一起使用', 2)
    image = inspect_image(file) if file else None
    device = device or controller.select()
    path = f'/v1/devices/{device["device_id"]}/firmware'
    status = controller.call('GET', path)
    if not status.get('supported'):
        raise ClientError(status.get('message') or '设备离线或尚未安装 OTA 引导程序，请先通过 USB 迁移', 3)
    if latest:
        repository = repository or controller.call('GET', '/v1/preferences').get('firmware_repository') or DEFAULT_REPOSITORY
        release = latest_release(repository, include_prereleases=prerelease)
        if tuple(map(int, release['version'].split('.'))) < tuple(map(int, status['version'].split('.'))):
            return dict(state='up_to_date', version=status['version'], message='设备版本高于 GitHub 最新正式版本')
        image = download_release(release, status['profile'], controller.runtime.directory / 'firmware')
        image['发布类型'] = ('测试版（Pre-release）' if release['prerelease'] else
                            '测试通道（发布类型未确认）' if release['prerelease'] is None else '正式版')
    compatible_profiles = (status['profile'], status.get('migration_profile', status['profile']))
    if image['profile'] not in compatible_profiles or image['bytes'] > status['max_bytes']:
        raise ClientError('固件硬件配置或分区大小与设备不匹配，未执行升级', 2)
    if image['elf_sha256'] == status['elf_sha256']:
        return dict(state='up_to_date', version=status['version'], message='设备已运行该固件')
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
                           (f'  上传进度  {"█" * (percent // 5)}{"░" * (20 - percent // 5)}  {percent} %', 'selected'),
                           ('', None), ('  ' + message, 'strong'), ('  ' + hint, 'muted')])

    progress(0, '正在检查固件完整性…')
    data = Path(image['path']).read_bytes()
    if image_info(data)['sha256'] != image['sha256']:
        raise FirmwareError('固件文件在确认后发生变化，未开始升级')
    progress(0, '正在准备设备…')
    upload = controller.call('POST', path, dict(action='begin', size=len(data), sha256=image['sha256'],
                                                profile=image['profile'], elf_sha256=image['elf_sha256']))
    committing = False
    try:
        progress(0, '正在上传固件…', 'Ctrl+C 取消上传')
        for offset in range(0, len(data), 4096):
            chunk = data[offset:offset + 4096]
            result = controller.call('POST', path, dict(action='write', upload_id=upload['upload_id'],
                                                        offset=offset, data=base64.b64encode(chunk).decode('ascii')))
            if result.get('received') != offset + len(chunk):
                raise FirmwareError('后台服务未确认准确的上传位置，已停止')
            progress((offset + len(chunk)) * 100 // len(data),
                     f'正在上传固件  {(offset + len(chunk)) / 1024:.0f} / {len(data) / 1024:.0f} KiB', 'Ctrl+C 取消上传')
        progress(100, '上传完成，正在校验固件并准备重启…')
        committing = True
        result = controller.call('POST', path, dict(action='finish', upload_id=upload['upload_id']))
    except BaseException:
        if not committing:
            try:
                controller.call('POST', path, dict(action='abort', upload_id=upload['upload_id']))
            except Exception:
                pass
        raise
    if not wait:
        return result
    waiting_since = time.monotonic()
    deadline = waiting_since + 90
    while time.monotonic() < deadline:
        progress(100, f'等待设备重连并确认新固件…  已等待 {int(time.monotonic() - waiting_since)} 秒')
        try:
            current = controller.call('GET', path)
            if current.get('elf_sha256') == image['elf_sha256'] and current.get('boot_state') == 2:
                return dict(state='boot_confirmed', version=current['version'], elf_sha256=image['elf_sha256'])
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


def _format_device_row(device, current_id=None, default_id=None):
    tags = []
    if current_id and device['device_id'] == current_id:
        tags.append('当前')
    if default_id and device['device_id'] == default_id:
        tags.append('默认')
    badge = f'[{"·".join(tags)}] ' if tags else ''
    online = f'在线 · {transport_text(device)}' if device.get('connected') else '离线'
    paired = '已配对' if device.get('paired') else '未配对'
    return f'{badge}{device_title(device)}  ·  {device["device_id"]}  ·  {paired}  ·  {online}'


def _device_options(devices, current_id=None, default_id=None, include_refresh=True):
    use_short_keys = len(devices) <= (8 if include_refresh else 9)
    options = []
    for index, device in enumerate(devices):
        key = str(index + 1) if use_short_keys else f'dev:{device["device_id"]}'
        options.append((key, _format_device_row(device, current_id, default_id)))
    if include_refresh:
        options.append(('r' if use_short_keys else 'refresh', '刷新设备列表'))
    return options, use_short_keys


def pick_device(controller, terminal, title='选择设备', scan_first=False, description=''):
    """Interactive device picker with in-place refresh."""
    notice = ''
    notice_role = 'good'
    if scan_first:
        devices = controller.scan()['devices']
    else:
        status = controller.runtime.status()
        devices = status.get('devices', []) if status else []
        if not devices:
            devices = controller.scan()['devices']
    while True:
        default_id = controller.default_device()
        if not devices:
            choice = terminal.menu(
                title,
                [('r', '重新扫描设备')],
                description=description or '未发现可用设备，请连接 USB&OTG 或确认 2.4 GHz 热点在线',
                notice=notice,
                notice_role=notice_role,
            )
            if choice is None:
                return None
            devices = controller.scan()['devices']
            notice = f'已刷新，发现 {len(devices)} 台设备' if devices else '仍未发现设备，请检查 USB 线缆或热点'
            notice_role = 'good' if devices else 'warning'
            continue
        options, use_short_keys = _device_options(devices, controller.device_id, default_id, include_refresh=True)
        refresh_key = 'r' if use_short_keys else 'refresh'
        choice = terminal.menu(
            title,
            options,
            description=description or f'共发现 {len(devices)} 台设备，选择要操作的设备',
            notice=notice,
            notice_role=notice_role,
        )
        if choice is None:
            return None
        if choice == refresh_key:
            devices = controller.scan()['devices']
            notice = f'设备列表已刷新（共 {len(devices)} 台）'
            notice_role = 'good'
            continue
        if use_short_keys:
            device = devices[int(choice) - 1]
        else:
            target_id = choice.split(':', 1)[1]
            device = next((d for d in devices if d['device_id'] == target_id), None)
        if device is not None:
            controller.device_id = device['device_id']
            return device


def ensure_device(controller, terminal, title='选择设备', scan_first=False):
    """Resolve the active device, prompting interactively when needed."""
    if scan_first:
        controller.scan()
    if hasattr(controller, 'try_select'):
        device, devices, reason = controller.try_select()
    else:
        # Support simplified test doubles that only provide select().
        return controller.select()
    if device is not None:
        return device
    if reason == 'ambiguous':
        desc = f'发现 {len(devices)} 台设备，请先选择本次要操作的设备'
    elif reason == 'not_found':
        desc = '此前选定或默认设备当前不在线，请重新选择设备'
    else:
        desc = '尚未发现设备，请连接 USB&OTG 或确认 2.4 GHz 热点在线'
    return pick_device(controller, terminal, title=title, scan_first=False, description=desc)


def _find_device(controller, device_id, rescan=False):
    if rescan:
        devices = controller.scan()['devices']
    else:
        status = controller.runtime.status()
        devices = status.get('devices', []) if status else []
    return next((d for d in devices if d['device_id'] == device_id), None)


def ui_device_detail(controller, terminal, device_id):
    selected = 0
    notice = ''
    notice_role = 'good'
    while True:
        device = _find_device(controller, device_id)
        if device is None:
            return '设备已从列表中移除'
        default_id = controller.default_device()
        is_current = controller.device_id == device_id
        is_default = default_id == device_id
        online = f'在线 · {transport_text(device)}' if device.get('connected') else '离线'
        paired_text = '已配对' if device.get('paired') else '未配对'
        desc = (f'{device_title(device)}  ·  {device_id}\n'
                f'状态：{paired_text} · {online}'
                + (' · 当前设备' if is_current else '')
                + (' · 默认设备' if is_default else ''))
        if not device.get('paired'):
            options = [
                ('1', '配对并设为当前设备'),
                ('2', '仅切换为当前设备（用于免配对 USB 配网）'),
            ]
        else:
            options = []
            idx = 1
            if not is_current:
                options.append((str(idx), '切换为当前设备'))
                idx += 1
            options.append((str(idx), '查看设备信息与健康状态'))
            info_key = str(idx); idx += 1
            default_label = '当前已是默认设备' if is_default else '设为默认设备'
            options.append((str(idx), default_label))
            default_key = str(idx); idx += 1
            options.append((str(idx), '修改设备别名'))
            alias_key = str(idx); idx += 1
            options.append((str(idx), '取消配对'))
            unpair_key = str(idx)
        choice = terminal.menu('设备操作', options, selected=selected, description=desc,
                               notice=notice, notice_role=notice_role)
        if choice is None:
            return ''
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        notice = ''
        notice_role = 'good'
        if not device.get('paired'):
            if choice == '1':
                controller.device_action('pair', device)
                controller.device_id = device_id
                notice = '配对成功，已设为当前设备'
                selected = 0
            elif choice == '2':
                controller.device_id = device_id
                return f'已切换当前设备为 {device_title(device)}'
            continue
        if not is_current and choice == '1':
            controller.device_id = device_id
            notice = f'已切换为当前设备：{device_title(device)}'
            selected = 0
        elif choice == info_key:
            health = controller.call('GET', f'/v1/devices/{device_id}/health') if device.get('connected') else None
            view(terminal, '设备信息', dict(device=device, health=health))
        elif choice == default_key:
            if is_default:
                notice = '该设备已经是默认设备'
            else:
                controller.set_default(device_id)
                controller.device_id = device_id
                notice = f'已将 {device_title(device)} 设为默认设备'
        elif choice == alias_key:
            alias = ask(terminal, '  新设备别名（留空回车取消） › ')
            if alias is None or not alias.strip():
                notice = '已取消修改别名'
                notice_role = 'muted'
            else:
                result = controller.set_alias(device_id, alias.strip())
                notice = f'别名已保存为：{result.get("alias") or alias.strip()}'
        elif choice == unpair_key:
            if view(terminal, '取消配对 · 确认',
                    dict(device_id=device_id, name=device_title(device),
                         message='取消配对将移除本机保存的配对信息，并断开当前连接。'),
                    confirm=True, confirm_label='确认取消配对'):
                controller.device_action('unpair', device)
                return f'已取消设备 {device_title(device)} 的配对'
            notice = '已取消操作'
            notice_role = 'muted'


def ui_devices(controller, terminal):
    notice = ''
    notice_role = 'good'
    selected = 0
    devices = controller.scan()['devices']
    while True:
        default_id = controller.default_device()
        if not devices:
            choice = terminal.menu(
                '设备管理',
                [('r', '重新扫描设备')],
                description='未发现可用设备，请连接 USB&OTG 或检查 Wi-Fi / 电脑热点',
                notice=notice,
                notice_role=notice_role,
            )
            if choice is None:
                return
            devices = controller.scan()['devices']
            notice = f'已刷新，发现 {len(devices)} 台设备' if devices else '仍未发现设备'
            notice_role = 'good' if devices else 'warning'
            continue
        options, use_short_keys = _device_options(devices, controller.device_id, default_id, include_refresh=True)
        refresh_key = 'r' if use_short_keys else 'refresh'
        choice = terminal.menu(
            '设备管理',
            options,
            selected=selected,
            description=f'共发现 {len(devices)} 台设备 · 选择设备进行配对、切换、改名或设为默认',
            notice=notice,
            notice_role=notice_role,
        )
        if choice is None:
            return
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        if choice == refresh_key:
            devices = controller.scan()['devices']
            notice = f'设备列表已刷新（共 {len(devices)} 台）'
            notice_role = 'good'
            continue
        if use_short_keys:
            device = devices[int(choice) - 1]
        else:
            target_id = choice.split(':', 1)[1]
            device = next((d for d in devices if d['device_id'] == target_id), None)
        if device is None:
            devices = controller.scan()['devices']
            continue
        feedback = ui_device_detail(controller, terminal, device['device_id'])
        devices = controller.scan()['devices']
        notice = feedback
        notice_role = 'good'


def ui_connection(controller, terminal):
    device = ensure_device(controller, terminal, title='连接与通道 · 选择设备')
    if device is None:
        return
    device_id = device['device_id']
    selected = 0
    notice = ''
    notice_role = 'good'
    while True:
        refreshed = _find_device(controller, device_id)
        if refreshed is not None:
            device = refreshed
        pref = device.get('preferred_transport') or device.get('transport') or 'wifi'
        active = device.get('active_transport') or (device.get('transport') if device.get('connected') else None)
        state_label = '在线' if device.get('connected') else '离线'
        active_label = preference_text(active) if active else '无'
        owner_label = '  ·  调试会话占用中' if device.get('owner') else ''
        desc = (f'{device_title(device)} ({device_id})\n'
                f'状态：{state_label}  ·  当前通道 {active_label}  ·  偏好 {preference_text(pref)}{owner_label}')
        if device.get('connected'):
            conn_options = [('1', '断开连接（暂停自动重连）'), ('2', '重新连接')]
        else:
            conn_options = [('1', '连接设备'), ('2', '断开并暂停自动重连')]
        mode_options = [
            ('3', f'{"●" if pref == "auto" else "○"} 自动切换（USB 优先）'),
            ('4', f'{"●" if pref == "usb" else "○"} 固定为 USB'),
            ('5', f'{"●" if pref == "wifi" else "○"} 固定为 Wi-Fi'),
        ]
        options = conn_options + mode_options
        choice = terminal.menu('连接与通道', options, selected=selected, description=desc,
                               notice=notice, notice_role=notice_role)
        if choice is None:
            return
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        try:
            if choice == '1':
                action = 'disconnect' if device.get('connected') else 'connect'
                result = controller.device_action(action, device)
                if action == 'disconnect':
                    notice, notice_role = '已断开连接并暂停自动重连', 'good'
                elif result.get('connected'):
                    notice, notice_role = '设备已连接', 'good'
                else:
                    notice, notice_role = f'连接未建立：{result.get("error") or "请检查设备是否在线"}', 'warning'
            elif choice == '2':
                action = 'connect' if device.get('connected') else 'disconnect'
                result = controller.device_action(action, device)
                if action == 'disconnect':
                    notice, notice_role = '已暂停自动重连', 'good'
                elif result.get('connected'):
                    notice, notice_role = '连接已刷新', 'good'
                else:
                    notice, notice_role = f'重连未建立：{result.get("error") or "请检查设备是否在线"}', 'warning'
            elif choice in ('3', '4', '5'):
                mode = {'3': 'auto', '4': 'usb', '5': 'wifi'}[choice]
                result = controller.device_action('connect', device, mode)
                if result.get('connected'):
                    active_now = preference_text(result.get('active_transport') or result.get('transport'))
                    notice, notice_role = f'已切换为 {preference_text(mode)}（当前通道：{active_now}）', 'good'
                elif result.get('connection_error'):
                    notice, notice_role = f'已保存偏好为 {preference_text(mode)}，但暂未连上：{result["connection_error"]}', 'warning'
                else:
                    notice, notice_role = f'已保存偏好为 {preference_text(mode)}（等待设备上线）', 'warning'
        except (ClientError, RMLinkError, OSError, ValueError) as error:
            notice, notice_role = str(error), 'warning'


def ui_wifi(controller, terminal):
    options = [
        ('1', '查看设备网络状态'),
        ('2', '通过 USB 配置 Wi-Fi'),
        ('3', '开启板端 Wi-Fi'),
        ('4', '关闭板端 Wi-Fi'),
        ('5', '检查电脑 2.4 GHz 热点'),
    ]
    selected = 0
    notice = ''
    notice_role = 'good'
    while True:
        choice = terminal.menu('Wi-Fi 与热点', options, selected=selected,
                               description='配置探针 Wi-Fi 凭据、开关板端 Wi-Fi 或检查本机热点',
                               notice=notice, notice_role=notice_role)
        if choice is None:
            return
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        notice = ''
        notice_role = 'good'
        if choice == '5':
            view(terminal, '电脑热点', controller.hotspot())
            continue
        device = ensure_device(controller, terminal, title='Wi-Fi 与热点 · 选择设备', scan_first=True)
        if device is None:
            continue
        path = f'/v1/devices/{device["device_id"]}/wifi'
        try:
            if choice == '1':
                view(terminal, '网络状态', controller.call('GET', path))
            elif choice == '2':
                ssid = ask(terminal, '  2.4 GHz 网络名称（留空回车取消） › ')
                if ssid is None or not ssid.strip():
                    notice, notice_role = '已取消 Wi-Fi 配网', 'muted'
                    continue
                password = ask(terminal, '  密码（隐藏输入；开放网络留空） › ', secret=True)
                if password is None:
                    notice, notice_role = '已取消 Wi-Fi 配网', 'muted'
                    continue
                result = controller.call('POST', path, dict(ssid=ssid, password=password))
                view(terminal, 'Wi-Fi 配置', result)
            elif choice in ('3', '4'):
                enabled = choice == '3'
                controller.call('POST', path, dict(enabled=enabled))
                notice = '板端 Wi-Fi 已开启' if enabled else '板端 Wi-Fi 已关闭（USB 保持可用）'
                notice_role = 'good'
        except (ClientError, RMLinkError, OSError, ValueError) as error:
            notice, notice_role = str(error), 'warning'


def _resolve_release_repo(controller, explicit=None):
    if explicit:
        return explicit
    try:
        saved = controller.preferences().get('firmware_repository')
        if saved:
            return saved
    except Exception:
        pass
    return DEFAULT_REPOSITORY


def _check_no_active_debug_sessions(controller):
    status = controller.runtime.status()
    if not status:
        return
    busy = [d for d in status.get('devices', []) if d.get('owner')]
    if busy:
        names = '、'.join(device_title(d) for d in busy)
        raise ClientError(f'检测到活动调试会话（{names}），请先结束调试再升级控制台与驱动安装包', 3)


def upgrade_client(controller, terminal, repository=None, prerelease=False, silent=False, yes=False,
                   output_dir=None, launch=True):
    interactive = terminal.tty and getattr(getattr(controller, 'args', None), 'json', False) is not True
    repo = _resolve_release_repo(controller, repository)
    if interactive:
        terminal.draw([('  控制台与驱动安装包更新', 'accent'), ('', None),
                       (f'  正在从 GitHub ({repo}) 查询最新版本…', 'strong'),
                       ('  请稍候', 'muted')])
    release = latest_installer_release(repo, include_prereleases=prerelease)
    if launch:
        _check_no_active_debug_sessions(controller)
        if not yes:
            if not interactive:
                raise ClientError('脚本更新安装包需使用 --yes 确认下载并启动安装向导', 2)
            release_type = ('测试版（Pre-release）' if release['prerelease'] else
                            '测试通道（发布类型未确认）' if release['prerelease'] is None else '正式版')
            size_text = f'{release["size"] / (1024 * 1024):.2f} MiB' if release.get('size') else '下载时确定'
            sign_text = '已签名' if release.get('signed') else '未签名（安装时 Windows SmartScreen 可能提示确认）'
            version_text = (f'{release["current_version"]} → {release["version"]}'
                            if release['update_available'] else
                            f'{release["current_version"]}（已是最新，可按 Enter 重新下载修复安装）')
            info = dict(
                版本状态=version_text,
                发布类型=release_type,
                签名状态=sign_text,
                文件名=release['file'],
                文件大小=size_text,
                sha256=release['sha256'],
                repository=release['repository'],
            )
            action_label = '确认下载并启动静默安装' if silent else '确认下载并启动中文安装向导'
            if not view(terminal, '控制台与驱动安装包 · 更新确认', info, confirm=True, confirm_label=action_label):
                return dict(state='cancelled', version=release['version'], current_version=release['current_version'])

    last_draw = [0.0]

    def on_progress(received, total):
        if not interactive:
            return
        now = time.monotonic()
        if total and received < total and now - last_draw[0] < 0.08:
            return
        last_draw[0] = now
        if total:
            percent = min(100, received * 100 // total)
            bar = f'{"█" * (percent // 5)}{"░" * (20 - percent // 5)}  {percent} %'
            detail = f'{received / (1024 * 1024):.2f} / {total / (1024 * 1024):.2f} MiB'
        else:
            bar = '下载中…'
            detail = f'{received / (1024 * 1024):.2f} MiB'
        terminal.draw([
            ('  控制台与驱动安装包更新', 'accent'),
            ('', None),
            (f'  {release["current_version"]} → {release["version"]}  ·  {release["file"]}', 'strong'),
            (f'  下载进度  {bar}', 'selected'),
            ('', None),
            (f'  已下载  {detail}', 'strong'),
            ('  下载完成后将自动校验 SHA256', 'muted'),
        ])

    if interactive:
        terminal.draw([
            ('  控制台与驱动安装包更新', 'accent'),
            ('', None),
            (f'  正在下载  {release["file"]}…', 'strong'),
            ('  仅允许从 GitHub HTTPS 下载并强制校验 SHA256', 'muted'),
        ])
    destination = Path(output_dir) if output_dir else (controller.runtime.directory / 'updates')
    downloaded = download_installer(release, destination, progress=on_progress)
    if not launch:
        return dict(
            state='downloaded',
            version=downloaded['version'],
            current_version=downloaded['current_version'],
            file=downloaded['file'],
            path=downloaded['path'],
            bytes=downloaded['bytes'],
            sha256=downloaded['sha256'],
            signed=downloaded['signed'],
            cached=downloaded['cached'],
            repository=downloaded['repository'],
        )
    _check_no_active_debug_sessions(controller)
    launched = launch_installer(downloaded['path'], expected_sha256=downloaded['sha256'], silent=silent)
    return dict(
        launched,
        version=downloaded['version'],
        current_version=downloaded['current_version'],
        file=downloaded['file'],
        sha256=downloaded['sha256'],
        signed=downloaded['signed'],
        cached=downloaded['cached'],
        repository=downloaded['repository'],
    )


def ui_firmware(controller, terminal):
    options = [
        ('1', '查看探针固件与升级状态'),
        ('2', '从 GitHub 升级探针正式固件'),
        ('3', '从 GitHub 升级探针测试固件'),
        ('4', '导入本地探针固件 (.bin)'),
        ('5', '从 GitHub 更新控制台与驱动安装包（正式版）'),
        ('6', '从 GitHub 更新控制台与驱动安装包（测试版）'),
        ('7', '设置 GitHub 发布仓库'),
    ]
    selected = 0
    notice = ''
    notice_role = 'good'
    while True:
        choice = terminal.menu('固件/驱动升级', options, selected=selected,
                               description='升级探针固件（ESP32-S3）或更新电脑端控制台与驱动安装包（Windows）',
                               notice=notice, notice_role=notice_role)
        if choice is None:
            return
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        notice = ''
        notice_role = 'good'
        if choice in ('5', '6'):
            try:
                result = upgrade_client(controller, terminal, prerelease=(choice == '6'), silent=False, launch=True)
                if result.get('state') == 'installer_launched':
                    raise ExitForUpdate(result['message'])
                if result.get('state') == 'cancelled':
                    notice, notice_role = '已取消安装包更新', 'muted'
            except ExitForUpdate:
                raise
            except (ClientError, RMLinkError, FirmwareError, OSError, ValueError) as error:
                view(terminal, '安装包更新未完成', dict(message=str(error)))
            continue
        if choice == '7':
            current_repo = _resolve_release_repo(controller)
            hint = f'当前仓库：{current_repo}'
            repository = ask(terminal, '  GitHub 仓库 owner/repo（留空取消） › ', hint=hint)
            if repository is None or not repository.strip():
                notice, notice_role = '已取消修改发布仓库', 'muted'
                continue
            try:
                saved = controller.set_firmware_repository(repository.strip())
                notice = f'发布仓库已保存：{saved.get("firmware_repository")}'
            except (ClientError, RMLinkError, OSError, ValueError) as error:
                notice, notice_role = str(error), 'warning'
            continue
        device = ensure_device(controller, terminal, title='固件/驱动升级 · 选择探针设备')
        if device is None:
            continue
        try:
            if choice == '1':
                view(terminal, 'RMLink 自身固件', controller.call('GET', f'/v1/devices/{device["device_id"]}/firmware'))
            elif choice == '2':
                firmware_result(terminal, upgrade_firmware(controller, terminal, latest=True, device=device))
            elif choice == '3':
                firmware_result(terminal, upgrade_firmware(controller, terminal, latest=True, prerelease=True, device=device))
            elif choice == '4':
                example = Path.home() / 'Downloads' / 'rmlink-0.3.1.bin'
                raw_path = ask(terminal, '  RMLink 应用 .bin 路径（留空取消） › ', hint=f'示例：{example}')
                if raw_path is None or not raw_path.strip():
                    notice, notice_role = '已取消导入本地固件', 'muted'
                    continue
                path = raw_path.strip().strip('"')
                firmware_result(terminal, upgrade_firmware(controller, terminal, file=path, device=device))
        except (ClientError, RMLinkError, OSError, ValueError) as error:
            view(terminal, '固件升级未完成', dict(message=str(error)))


def ui_service_and_diagnostics(controller, terminal):
    selected = 0
    notice = ''
    notice_role = 'good'
    while True:
        autostart_state = None
        try:
            autostart_info = controller.runtime.autostart(None)
            autostart_state = autostart_info.get('start_at_logon')
        except Exception:
            autostart_state = None
        if autostart_state is True:
            autostart_label = '登录自动启动：已开启（点击关闭）'
        elif autostart_state is False:
            autostart_label = '登录自动启动：未开启（点击开启）'
        else:
            autostart_label = '查看登录自动启动状态'
        options = [
            ('1', '运行故障诊断'),
            ('2', '查看近期服务日志'),
            ('3', '导出脱敏诊断报告'),
            ('4', '查看后台服务状态'),
            ('5', '重启后台服务'),
            ('6', autostart_label),
        ]
        choice = terminal.menu('服务与诊断', options, selected=selected,
                               description='排查连接问题、查看日志与管理本机后台连接服务',
                               notice=notice, notice_role=notice_role)
        if choice is None:
            return
        selected = next((i for i, opt in enumerate(options) if opt[0] == choice), 0)
        notice = ''
        notice_role = 'good'
        try:
            if choice == '1':
                view(terminal, '故障诊断', controller.diagnose())
            elif choice == '2':
                view(terminal, '近期日志', read_logs(controller.runtime, 60))
            elif choice == '3':
                default_path = Path.cwd() / 'rmlink-diagnostics.json'
                raw_path = ask(terminal, '  导出 JSON 路径（留空取消） › ', hint=f'示例：{default_path}')
                if raw_path is None or not raw_path.strip():
                    notice, notice_role = '已取消导出', 'muted'
                    continue
                path = raw_path.strip().strip('"')
                export_report(controller.diagnose(), path)
                notice = f'诊断报告已导出：{Path(path).resolve()}'
            elif choice == '4':
                status = controller.runtime.status()
                view(terminal, '后台服务状态', status or dict(running=False, message='后台服务未启动'))
            elif choice == '5':
                if view(terminal, '重启后台服务 · 确认',
                        dict(message='重启将短暂中断所有设备的后台连接；活动调试期间会被拒绝。'),
                        confirm=True, confirm_label='确认重启后台服务'):
                    controller.runtime.stop()
                    status = controller.runtime.start()
                    notice = f'后台服务已重启（v{status.get("version", "--")}）'
                else:
                    notice, notice_role = '已取消重启', 'muted'
            elif choice == '6':
                if autostart_state is None:
                    view(terminal, '登录自动启动', controller.runtime.autostart(None))
                else:
                    target = not autostart_state
                    controller.runtime.autostart(target)
                    notice = '已开启登录自动启动' if target else '已关闭登录自动启动'
        except (ClientError, RMLinkError, OSError, ValueError) as error:
            notice, notice_role = str(error), 'warning'


def ui_action(index, controller, terminal):
    handlers = (
        ui_devices,
        ui_connection,
        ui_wifi,
        monitor,
        ui_firmware,
        ui_service_and_diagnostics,
    )
    if 0 <= index < len(handlers):
        return handlers[index](controller, terminal)
    raise ClientError('未知菜单项', 2)


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
    device = ensure_device(controller, terminal, title='实时监控 · 选择设备')
    if device is None:
        return
    signal = SignalReader(controller)
    while True:
        status = controller.runtime.status()
        current = _find_device(controller, device['device_id']) or device
        metrics = controller.metrics(current)
        metrics['rssi_dbm'] = signal.sample(current)
        terminal.draw(dashboard(status, current, metrics, metrics['rates'], width=terminal.size().columns,
                                monitor=True, height=terminal.size().lines,
                                default_device=controller.default_device()))
        key = terminal.key(interval)
        if key in ('q', 'enter', '\x1b'):
            return


def shell(controller, terminal):
    controller.runtime.start()
    selected = 0
    signal = SignalReader(controller)
    rows_per_col = (len(MENU) + 1) // 2
    exit_message = None
    with terminal.screen():
        while True:
            status = controller.runtime.status()
            device = metrics = None
            message = ''
            default_id = None
            try:
                if status:
                    default_id = controller.default_device() if hasattr(controller, 'default_device') else None
                    if hasattr(controller, 'try_select'):
                        device, _, _ = controller.try_select(status)
                    else:
                        device = controller.select(status)
                    if device is not None:
                        metrics = controller.metrics(device)
            except (ClientError, RMLinkError, urllib.error.URLError) as error:
                message = str(error)
            rssi = signal.sample(device)
            if metrics is not None:
                metrics['rssi_dbm'] = rssi
            terminal.draw(dashboard(status, device, metrics, (metrics or {}).get('rates'), selected,
                                    terminal.size().columns, message, height=terminal.size().lines,
                                    default_device=default_id))
            key = terminal.key()
            while not terminal.vt and not key:
                key = terminal.key()
            if key in ('q', '\x1b'):
                return 0
            if key in ('up', 'down'):
                selected = (selected + (1 if key == 'down' else -1)) % len(MENU)
            if key in ('left', 'right'):
                if terminal.size().columns >= 72:
                    other = selected + rows_per_col if selected < rows_per_col else selected - rows_per_col
                    if other < len(MENU):
                        selected = other
                else:
                    selected = (selected + (1 if key == 'right' else -1)) % len(MENU)
            shortcuts = ''.join(str(i + 1) for i in range(len(MENU)))
            if key in shortcuts and key:
                selected = int(key) - 1
            if key == 'enter' or (key in shortcuts and key):
                try:
                    ui_action(selected, controller, terminal)
                    message = ''
                except ExitForUpdate as notice_exit:
                    exit_message = str(notice_exit)
                    break
                except (ClientError, RMLinkError, OSError, ValueError) as error:
                    view(terminal, '操作未完成', dict(message=str(error)))
                    message = ''
    if exit_message:
        print('RMLink：' + exit_message)
    return 0
