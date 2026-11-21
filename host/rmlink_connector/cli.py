"""RMLink terminal client. CLI commands and interactive TUI share one controller."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .client import RMLinkError
from .console import Terminal, dashboard, safe_text
from .controller import Controller, SignalReader, export_report, read_logs
from .firmware import DEFAULT_REPOSITORY, inspect_image, latest_release
from .formatters import emit, human
from .installer import latest_installer_release
from .local_service import ClientError
from .tui import monitor, shell, ui_action, upgrade_client, upgrade_firmware, view


def shared_global(target, defaults=False):
    default = None if defaults else argparse.SUPPRESS
    target.add_argument('--json', action='store_true', default=False if defaults else argparse.SUPPRESS,
                        help='输出 JSON，便于脚本处理')
    target.add_argument('--no-color', action='store_true', default=False if defaults else argparse.SUPPRESS,
                        help='关闭颜色')
    target.add_argument('--state-dir', default=default, help=argparse.SUPPRESS)
    target.add_argument('--api-port', type=int, default=3243 if defaults else argparse.SUPPRESS, help=argparse.SUPPRESS)


def shared_device(target, defaults=False):
    default = None if defaults else argparse.SUPPRESS
    target.add_argument('--device', default=default, help='设备编号；省略时使用默认设备或唯一设备')


def parser():
    root = argparse.ArgumentParser(prog='rmlink', description='RMLink 设备控制台 · 不带参数进入交互界面')
    shared_global(root, True)
    shared_device(root, True)
    root.add_argument('--version', action='version', version='RMLink ' + __version__)
    root.add_argument('--preview', action='store_true', help='输出界面快照，不启动后台服务')
    commands = root.add_subparsers(dest='command', metavar='命令')

    def add(parent, name, help_text, with_device=False):
        item = parent.add_parser(name, help=help_text, description=help_text)
        shared_global(item)
        if with_device:
            shared_device(item)
        return item

    add(commands, 'devices', '发现设备并列出配对和连接状态')
    add(commands, 'status', '查看后台服务与设备状态')
    add(commands, 'info', '查看设备信息和健康状态', with_device=True)
    for name, help_text in (('pair', '配对设备'), ('unpair', '取消配对'), ('connect', '连接设备'),
                            ('disconnect', '断开设备并暂停自动重连')):
        item = add(commands, name, help_text, with_device=True)
        item.add_argument('device_id', nargs='?', help='设备编号（也可通过 --device 指定）')
        if name == 'connect':
            item.add_argument('--transport', choices=('auto', 'usb', 'wifi'),
                              help='选择自动切换（USB 优先）、有线 USB 或 Wi-Fi')

    wifi = add(commands, 'wifi', '配置或查看设备 Wi-Fi 与电脑热点', with_device=True)
    wifi_sub = wifi.add_subparsers(dest='action', metavar='操作', required=True)
    add(wifi_sub, 'status', '查看连接状态、IP 和信号强度', with_device=True)
    add(wifi_sub, 'hotspot', '检查电脑热点状态与频段，不读取密码')
    add(wifi_sub, 'on', '开启板端 Wi-Fi', with_device=True)
    add(wifi_sub, 'off', '关闭板端 Wi-Fi', with_device=True)
    configure = add(wifi_sub, 'configure', '通过 USB 保存 Wi-Fi 参数；密码隐藏输入', with_device=True)
    configure.add_argument('--ssid', help='2.4 GHz 网络名称')
    passwords = configure.add_mutually_exclusive_group()
    passwords.add_argument('--password-stdin', action='store_true', help='从标准输入读取密码，不放入命令行')
    passwords.add_argument('--open-network', action='store_true', help='显式配置无密码的开放网络')

    mon = add(commands, 'monitor', '被动查看实时收发速率和活动订阅', with_device=True)
    mon.add_argument('--interval', type=float, default=1, help='刷新间隔，0.25–10 秒')
    mon.add_argument('--count', type=int, default=0, help='输出指定次数后退出；0 为持续监控')

    firmware = add(commands, 'firmware', 'RMLink 自身固件检查、GitHub 获取与 OTA 升级', with_device=True)
    fw_sub = firmware.add_subparsers(dest='action', metavar='操作', required=True)
    add(fw_sub, 'status', '查看当前固件版本与升级支持状态', with_device=True)
    inspect = add(fw_sub, 'inspect', '离线校验应用镜像、版本、硬件配置及 SHA256')
    inspect.add_argument('file')
    latest = add(fw_sub, 'latest', '查询 GitHub 最新正式 Release 与固件清单')
    latest.add_argument('--repo', default=DEFAULT_REPOSITORY, help='GitHub 仓库 owner/repo')
    latest.add_argument('--prerelease', action='store_true', help='显式包含 GitHub 测试版固件')
    update = add(fw_sub, 'update', '通过 USB 或 Wi-Fi 写入备用分区，校验并重启确认', with_device=True)
    update.add_argument('file', nargs='?')
    update.add_argument('--latest', action='store_true', help='从 GitHub 获取与设备配置一致的最新固件')
    update.add_argument('--repo', help='GitHub 仓库 owner/repo；省略时使用客户端设置')
    update.add_argument('--prerelease', action='store_true', help='与 --latest 一起使用，显式包含测试版')
    update.add_argument('--yes', action='store_true', help='确认安装并重启 RMLink；脚本调用必需')
    update.add_argument('--no-wait', action='store_true', help='提交后返回，由 firmware status 查询启动结果')

    app_update = add(commands, 'update', '从 GitHub 检查、下载或安装最新控制台与驱动安装包')
    app_update_sub = app_update.add_subparsers(dest='action', metavar='操作', required=True)
    upd_check = add(app_update_sub, 'check', '查询 GitHub 最新控制台与驱动安装包版本')
    upd_check.add_argument('--repo', help='GitHub 仓库 owner/repo；省略时使用客户端设置')
    upd_check.add_argument('--prerelease', action='store_true', help='显式包含 GitHub 测试版')
    upd_dl = add(app_update_sub, 'download', '下载并校验最新 Windows 安装包（不自动安装）')
    upd_dl.add_argument('--repo', help='GitHub 仓库 owner/repo；省略时使用客户端设置')
    upd_dl.add_argument('--prerelease', action='store_true', help='显式包含 GitHub 测试版')
    upd_dl.add_argument('--output', help='保存安装包的目录；默认写入 ~/.rmlink/updates')
    upd_inst = add(app_update_sub, 'install', '下载校验并启动中文安装向导升级控制台与驱动')
    upd_inst.add_argument('--repo', help='GitHub 仓库 owner/repo；省略时使用客户端设置')
    upd_inst.add_argument('--prerelease', action='store_true', help='显式包含 GitHub 测试版')
    upd_inst.add_argument('--output', help='保存安装包的目录；默认写入 ~/.rmlink/updates')
    upd_inst.add_argument('--silent', action='store_true', help='使用静默覆盖安装，不弹出图形向导')
    upd_inst.add_argument('--yes', action='store_true', help='跳过确认并启动安装；脚本调用必需')

    service = add(commands, 'service', '管理每用户后台连接服务')
    service_sub = service.add_subparsers(dest='action', metavar='操作', required=True)
    for name, help_text in (('status', '查看服务状态'), ('start', '启动服务，已运行时复用'),
                            ('stop', '停止服务；活动调试期间拒绝停止'), ('restart', '停止后重新启动服务')):
        add(service_sub, name, help_text)
    auto = add(service_sub, 'autostart', '查看或设置登录自动启动')
    auto.add_argument('mode', nargs='?', choices=('on', 'off', 'status'), default='status')

    logs = add(commands, 'logs', '查看近期服务日志')
    logs.add_argument('--lines', type=int, default=40)

    doctor = add(commands, 'doctor', '检查服务、设备发现与连接')
    doctor.add_argument('--output', help='导出脱敏诊断 JSON，不覆盖已有文件')

    config = add(commands, 'config', '查看设置或设置默认设备、设备别名与固件仓库', with_device=True)
    config_sub = config.add_subparsers(dest='action', metavar='操作', required=True)
    add(config_sub, 'show', '查看当前客户端设置')
    default = add(config_sub, 'default', '设置默认设备', with_device=True)
    default.add_argument('device_id', nargs='?', help='设备编号（也可通过 --device 指定）')
    alias = add(config_sub, 'alias', '设置设备别名', with_device=True)
    alias.add_argument('name')
    repo = add(config_sub, 'repo', '设置默认 GitHub 固件仓库')
    repo.add_argument('repository', help='GitHub 仓库 owner/repo')

    recording = add(commands, 'inspect-recording', '离线检查 .rmlrec 录制文件')
    recording.add_argument('file')
    recording.add_argument('--anomalies', action='store_true')
    return root


def validate_args(args):
    if not 1 <= args.api_port <= 65535:
        raise ClientError('端口需为 1–65535', 2)
    if args.preview and args.command is not None:
        raise ClientError('--preview 不能与子命令同时使用', 2)
    positional_id = getattr(args, 'device_id', None)
    if args.device and positional_id and args.device.lower() != positional_id.lower():
        raise ClientError('--device 与位置参数 device_id 指定了不同设备，请只保留一个', 2)
    if args.command == 'config' and args.action == 'default' and not (args.device or positional_id):
        raise ClientError('请指定默认设备编号，例如 rmlink config default <设备编号>', 2)
    if args.command == 'firmware' and args.action == 'update':
        if bool(args.file) == bool(args.latest):
            raise ClientError('请选择本地固件文件，或使用 --latest 获取 GitHub 最新版本', 2)
        if args.prerelease and not args.latest:
            raise ClientError('--prerelease 需与 --latest 一起使用', 2)


def _handle_preview(args, controller, terminal):
    status = controller.runtime.status()
    device = None
    default_id = None
    if status:
        default_id = controller.default_device()
        device, _, _ = controller.try_select(status)
    if args.json:
        return dict(version=__version__, service=status)
    terminal.draw(dashboard(status, device, width=terminal.size().columns,
                            height=terminal.size().lines, default_device=default_id))
    return None


def _handle_status(args, controller, terminal):
    return controller.runtime.status() or dict(running=False, message='运行 rmlink service start 启动后台服务')


def _handle_devices(args, controller, terminal):
    return controller.scan()


def _handle_info(args, controller, terminal):
    device = controller.select()
    health = controller.call('GET', f'/v1/devices/{device["device_id"]}/health') if device['connected'] else None
    return dict(device=device, health=health)


def _handle_device_action(args, controller, terminal):
    if args.command == 'pair':
        controller.scan()
    return controller.device_action(args.command, transport=getattr(args, 'transport', None))


def _handle_wifi_status(args, controller, terminal):
    controller.scan()
    device = controller.select()
    return controller.call('GET', f'/v1/devices/{device["device_id"]}/wifi')


def _handle_wifi_toggle(args, controller, terminal):
    controller.scan()
    device = controller.select()
    return controller.call('POST', f'/v1/devices/{device["device_id"]}/wifi', dict(enabled=args.action == 'on'))


def _handle_wifi_configure(args, controller, terminal):
    controller.scan()
    device = controller.select()
    path = f'/v1/devices/{device["device_id"]}/wifi'
    ssid = args.ssid
    if ssid is None:
        if not sys.stdin.isatty():
            raise ClientError('脚本配网请提供 --ssid 和 --password-stdin（或 --open-network）', 2)
        ssid = input('2.4 GHz 网络名称 › ')
    if args.password_stdin:
        password = (sys.stdin.buffer.readline().decode('utf-8-sig') if hasattr(sys.stdin, 'buffer') else
                    sys.stdin.readline()).rstrip('\r\n')
    elif args.open_network:
        password = ''
    elif sys.stdin.isatty():
        import getpass
        password = getpass.getpass('Wi-Fi 密码（隐藏输入） › ')
    else:
        raise ClientError('请用 --password-stdin 输入密码，或显式使用 --open-network', 2)
    return controller.call('POST', path, dict(ssid=ssid, password=password))


def _handle_wifi_hotspot(args, controller, terminal):
    return controller.hotspot()


def _handle_monitor(args, controller, terminal):
    if terminal.tty and not args.json and not args.count:
        with terminal.screen():
            monitor(controller, terminal, args.interval, args.count, args.json)
    else:
        monitor(controller, terminal, args.interval, args.count, args.json)
    return None


def _handle_firmware_inspect(args, controller, terminal):
    return inspect_image(args.file)


def _handle_firmware_latest(args, controller, terminal):
    return latest_release(args.repo, include_prereleases=args.prerelease)


def _handle_firmware_status(args, controller, terminal):
    device = controller.select()
    return controller.call('GET', f'/v1/devices/{device["device_id"]}/firmware')


def _handle_firmware_update(args, controller, terminal):
    return upgrade_firmware(controller, terminal, args.file, args.latest, args.repo,
                            args.yes, not args.no_wait, args.prerelease)


def _handle_update_check(args, controller, terminal):
    repo = args.repo
    if not repo:
        try:
            repo = controller.preferences().get('firmware_repository')
        except Exception:
            repo = None
    return latest_installer_release(repo or DEFAULT_REPOSITORY, include_prereleases=args.prerelease)


def _handle_update_download(args, controller, terminal):
    return upgrade_client(controller, terminal, repository=args.repo, prerelease=args.prerelease,
                          yes=True, output_dir=args.output, launch=False)


def _handle_update_install(args, controller, terminal):
    return upgrade_client(controller, terminal, repository=args.repo, prerelease=args.prerelease,
                          silent=args.silent, yes=args.yes, output_dir=args.output, launch=True)


def _handle_service(args, controller, terminal):
    action = args.action
    if action == 'autostart':
        return controller.runtime.autostart(None if args.mode == 'status' else args.mode == 'on')
    if action == 'stop':
        return controller.runtime.stop()
    if action == 'restart':
        controller.runtime.stop()
    status = controller.runtime.start() if action in ('start', 'restart') else controller.runtime.status()
    return dict(running=bool(status), version=status.get('version') if status else None)


def _handle_logs(args, controller, terminal):
    return read_logs(controller.runtime, args.lines)


def _handle_doctor(args, controller, terminal):
    result = controller.diagnose()
    if args.output:
        export_report(result, args.output)
        result['exported_to'] = str(Path(args.output).resolve())
    return result


def _handle_config_show(args, controller, terminal):
    return controller.preferences(refresh=True)


def _handle_config_default(args, controller, terminal):
    device_id = (args.device or args.device_id).lower()
    return controller.set_default(device_id)


def _handle_config_alias(args, controller, terminal):
    return controller.set_alias(controller.select()['device_id'], args.name)


def _handle_config_repo(args, controller, terminal):
    return controller.set_firmware_repository(args.repository)


def _handle_inspect_recording(args, controller, terminal):
    from .recording_audit import audit_recording
    from .recording_reader import inspect_recording
    return audit_recording(args.file) if args.anomalies else inspect_recording(args.file)


# Route table: (command, action) -> (handler, requires_service)
COMMANDS = {
    ('status', None): (_handle_status, False),
    ('logs', None): (_handle_logs, False),
    ('doctor', None): (_handle_doctor, False),
    ('inspect-recording', None): (_handle_inspect_recording, False),
    ('wifi', 'hotspot'): (_handle_wifi_hotspot, False),
    ('firmware', 'inspect'): (_handle_firmware_inspect, False),
    ('firmware', 'latest'): (_handle_firmware_latest, False),
    ('update', 'check'): (_handle_update_check, False),
    ('update', 'download'): (_handle_update_download, False),
    ('update', 'install'): (_handle_update_install, False),
    ('service', 'status'): (_handle_service, False),
    ('service', 'start'): (_handle_service, False),
    ('service', 'stop'): (_handle_service, False),
    ('service', 'restart'): (_handle_service, False),
    ('service', 'autostart'): (_handle_service, False),
    ('devices', None): (_handle_devices, True),
    ('info', None): (_handle_info, True),
    ('pair', None): (_handle_device_action, True),
    ('unpair', None): (_handle_device_action, True),
    ('connect', None): (_handle_device_action, True),
    ('disconnect', None): (_handle_device_action, True),
    ('wifi', 'status'): (_handle_wifi_status, True),
    ('wifi', 'on'): (_handle_wifi_toggle, True),
    ('wifi', 'off'): (_handle_wifi_toggle, True),
    ('wifi', 'configure'): (_handle_wifi_configure, True),
    ('monitor', None): (_handle_monitor, True),
    ('firmware', 'status'): (_handle_firmware_status, True),
    ('firmware', 'update'): (_handle_firmware_update, True),
    ('config', 'show'): (_handle_config_show, True),
    ('config', 'default'): (_handle_config_default, True),
    ('config', 'alias'): (_handle_config_alias, True),
    ('config', 'repo'): (_handle_config_repo, True),
}


def run(args, controller, terminal):
    validate_args(args)
    if args.preview:
        return _handle_preview(args, controller, terminal)
    if args.command is None:
        if terminal.tty and sys.stdin.isatty() and not args.json:
            shell(controller, terminal)
            return None
        raise ClientError('非交互模式请指定子命令，例如 rmlink status --json 或 rmlink --help', 2)
    key = (args.command, getattr(args, 'action', None))
    entry = COMMANDS.get(key)
    if entry is None:
        raise ClientError('未知操作', 2)
    handler, requires_service = entry
    if requires_service:
        controller.runtime.start()
    return handler(args, controller, terminal)


def main(argv=None):
    # Windows pipes otherwise inherit the legacy ANSI code page (often GBK).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    args = parser().parse_args(argv)
    terminal = Terminal(args.no_color)
    try:
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


__all__ = [
    'Controller',
    'SignalReader',
    'emit',
    'export_report',
    'human',
    'main',
    'monitor',
    'parser',
    'read_logs',
    'run',
    'shell',
    'ui_action',
    'upgrade_client',
    'upgrade_firmware',
    'validate_args',
    'view',
]


if __name__ == '__main__':
    raise SystemExit(main())
