"""GitHub Release discovery, SHA256 verification and detached launcher for the RMLink Windows installer."""
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

from . import __version__
from .firmware import (
    DEFAULT_REPOSITORY,
    FirmwareError,
    github_json,
    github_url,
    open_github,
    repository_name,
)
from .local_service import hidden_process_options, powershell_environment, windows_powershell

MAX_INSTALLER_BYTES = 128 * 1024 * 1024
INSTALLER_PATTERN = re.compile(r'^RMLink-(\d+\.\d+\.\d+)-windows-x64-setup(-unsigned)?\.exe$')


class InstallerError(FirmwareError):
    """Error raised during installer release lookup, verification or launch."""


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise InstallerError(f'无效的版本号格式：{version}')
    return tuple(map(int, version.split('.')))


def _asset_url(repository, asset):
    url = github_url(asset.get('browser_download_url', ''))
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != 'github.com' or not parsed.path.startswith('/' + repository + '/releases/download/'):
        raise InstallerError('安装包下载地址不属于所选仓库')
    return url


def parse_sha256_text(text, expected_filename):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise InstallerError('安装包 SHA256 校验文件格式无效')
    match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', lines[0])
    if not match:
        raise InstallerError('安装包 SHA256 校验文件内容无效')
    digest, filename = match.group(1).lower(), match.group(2).strip()
    if Path(filename).name != expected_filename:
        raise InstallerError('SHA256 校验文件与安装包文件名不匹配')
    return digest


def _read_github_text(url, limit=4096):
    try:
        with open_github(url) as response:
            payload = response.read(limit + 1)
    except FirmwareError as error:
        raise InstallerError(str(error), error.http_status) from error
    if len(payload) > limit:
        raise InstallerError('校验文件大小超出允许范围')
    try:
        return payload.decode('utf-8')
    except UnicodeDecodeError as error:
        raise InstallerError('校验文件不是有效的 UTF-8 文本') from error


def _select_installer_asset(assets, expected_version):
    by_name = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
            raise InstallerError('GitHub Release 资产名称无效')
        if asset['name'] in by_name:
            raise InstallerError('GitHub Release 包含重复资产名称')
        by_name[asset['name']] = asset
    candidates = []
    for name, asset in by_name.items():
        match = INSTALLER_PATTERN.fullmatch(name)
        if not match:
            continue
        file_version, unsigned_flag = match.group(1), bool(match.group(2))
        if expected_version and file_version != expected_version:
            continue
        sha_name = name[:-4] + '.sha256'
        if sha_name not in by_name:
            continue
        json_name = name[:-4] + '.json'
        candidates.append((unsigned_flag, name, asset, by_name[sha_name], by_name.get(json_name)))
    if not candidates:
        raise InstallerError('该 GitHub Release 尚未发布带 SHA256 校验的 Windows 安装包')
    # Prefer signed installer (unsigned_flag == False) over unsigned build.
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0]


def _fallback_release_from_web(repository, include_prereleases):
    prefix = '/' + repository + '/releases/tag/'
    try:
        if include_prereleases:
            with open_github(f'https://github.com/{repository}/releases.atom') as response:
                feed = response.read(1024 * 1024 + 1)
            if len(feed) > 1024 * 1024 or b'<!DOCTYPE' in feed.upper():
                raise InstallerError('GitHub 发布订阅格式或大小无效')
            try:
                root = ET.fromstring(feed)
            except ET.ParseError as error:
                raise InstallerError('GitHub 发布订阅不是有效的 Atom') from error
            tags = []
            for link in root.findall('{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}link'):
                location = urllib.parse.urlsplit(link.get('href', ''))
                if location.scheme == 'https' and location.hostname == 'github.com' and location.path.startswith(prefix):
                    candidate = urllib.parse.unquote(location.path[len(prefix):])
                    if re.fullmatch(r'v\d+\.\d+\.\d+', candidate):
                        tags.append(candidate)
            if not tags:
                raise InstallerError('仓库尚无可用测试版或正式版发布')
            tag = max(tags, key=lambda item: tuple(map(int, item[1:].split('.'))))
        else:
            with open_github(f'https://github.com/{repository}/releases/latest') as response:
                location = urllib.parse.urlsplit(response.geturl())
            if location.hostname != 'github.com' or not location.path.startswith(prefix):
                raise InstallerError('仓库尚无正式 Release')
            tag = urllib.parse.unquote(location.path[len(prefix):])
    except FirmwareError as error:
        raise InstallerError(str(error), error.http_status) from error
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise InstallerError('Release 标签格式无效（需为 vmajor.minor.patch）')
    version = tag[1:]
    base = f'https://github.com/{repository}/releases/download/{urllib.parse.quote(tag, safe="")}/'
    for suffix in ('', '-unsigned'):
        exe_name = f'RMLink-{version}-windows-x64-setup{suffix}.exe'
        sha_name = f'RMLink-{version}-windows-x64-setup{suffix}.sha256'
        json_name = f'RMLink-{version}-windows-x64-setup{suffix}.json'
        try:
            sha_text = _read_github_text(base + sha_name)
        except InstallerError as error:
            if error.http_status == 404:
                continue
            raise
        digest = parse_sha256_text(sha_text, exe_name)
        manifest = None
        try:
            manifest = github_json(base + json_name, 65536)
        except FirmwareError:
            manifest = None
        signed = bool(manifest.get('signed')) if isinstance(manifest, dict) and 'signed' in manifest else (suffix == '')
        return dict(
            repository=repository,
            version=version,
            tag=tag,
            published_at=None,
            prerelease=None if include_prereleases else False,
            file=exe_name,
            sha256=digest,
            size=None,
            url=github_url(base + exe_name),
            signed=signed,
            current_version=__version__,
            update_available=version_tuple(version) > version_tuple(__version__),
            up_to_date=version_tuple(version) <= version_tuple(__version__),
        )
    raise InstallerError('最新 Release 尚未发布带 SHA256 校验的 Windows 安装包')


def latest_installer_release(repository=DEFAULT_REPOSITORY, include_prereleases=False):
    repository = repository_name(repository)
    try:
        if include_prereleases:
            releases = github_json(f'https://api.github.com/repos/{repository}/releases?per_page=100', 1024 * 1024, list)
            candidates = [r for r in releases if isinstance(r, dict) and not r.get('draft')
                          and re.fullmatch(r'v\d+\.\d+\.\d+', str(r.get('tag_name', '')))]
            if not candidates:
                raise InstallerError('仓库尚无可用测试版或正式版发布')
            # Pick the highest versioned release that actually carries a Windows installer asset.
            ordered = sorted(candidates, key=lambda r: tuple(map(int, r['tag_name'][1:].split('.'))), reverse=True)
            release = None
            selected = None
            for candidate in ordered:
                assets = candidate.get('assets')
                if not isinstance(assets, list) or len(assets) > 100:
                    continue
                try:
                    selected = _select_installer_asset(assets, candidate['tag_name'][1:])
                    release = candidate
                    break
                except InstallerError:
                    continue
            if release is None or selected is None:
                raise InstallerError('仓库尚无带 SHA256 校验的 Windows 安装包 Release')
        else:
            release = github_json(f'https://api.github.com/repos/{repository}/releases/latest', 1024 * 1024)
            if release.get('draft') or release.get('prerelease'):
                raise InstallerError('最新发布不是正式版本')
            tag = release.get('tag_name', '')
            if not isinstance(tag, str) or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
                raise InstallerError('Release 标签必须为 vmajor.minor.patch')
            assets = release.get('assets')
            if not isinstance(assets, list) or len(assets) > 100:
                raise InstallerError('GitHub Release 资产列表无效')
            selected = _select_installer_asset(assets, tag[1:])
    except FirmwareError as error:
        if error.http_status in (403, 429):
            return _fallback_release_from_web(repository, include_prereleases)
        if isinstance(error, InstallerError):
            raise
        raise InstallerError(str(error), error.http_status) from error

    unsigned_flag, exe_name, exe_asset, sha_asset, json_asset = selected
    tag = release['tag_name']
    version = tag[1:]
    exe_url = _asset_url(repository, exe_asset)
    sha_url = _asset_url(repository, sha_asset)
    size = exe_asset.get('size')
    if size is not None and (type(size) is not int or not 1024 <= size <= MAX_INSTALLER_BYTES):
        raise InstallerError('Release 中的安装包大小异常')
    digest = parse_sha256_text(_read_github_text(sha_url), exe_name)
    signed = not unsigned_flag
    if json_asset is not None:
        try:
            manifest = github_json(_asset_url(repository, json_asset), 65536)
            if isinstance(manifest, dict):
                if manifest.get('version') and manifest.get('version') != version:
                    raise InstallerError('安装包清单版本与 Release 标签不一致')
                if 'signed' in manifest:
                    signed = bool(manifest['signed'])
        except FirmwareError as error:
            if isinstance(error, InstallerError):
                raise
    return dict(
        repository=repository,
        version=version,
        tag=tag,
        published_at=release.get('published_at'),
        prerelease=bool(release.get('prerelease', False)),
        file=exe_name,
        sha256=digest,
        size=size,
        url=exe_url,
        signed=signed,
        current_version=__version__,
        update_available=version_tuple(version) > version_tuple(__version__),
        up_to_date=version_tuple(version) <= version_tuple(__version__),
    )


def file_sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_installer_file(path, expected_sha256):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise InstallerError('安装包文件不存在')
    size = resolved.stat().st_size
    if not 1024 <= size <= MAX_INSTALLER_BYTES:
        raise InstallerError('安装包文件大小异常')
    actual = file_sha256(resolved)
    if actual != expected_sha256.lower():
        raise InstallerError('安装包 SHA256 校验失败，文件可能已损坏或被篡改')
    return dict(path=str(resolved), bytes=size, sha256=actual)


def download_installer(release, directory, progress=None):
    target_dir = Path(directory).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / release['file']
    expected_sha = release['sha256'].lower()
    expected_size = release.get('size')
    if destination.is_file():
        try:
            verified = verify_installer_file(destination, expected_sha)
            if expected_size is None or verified['bytes'] == expected_size:
                if progress:
                    progress(verified['bytes'], verified['bytes'])
                return dict(release, path=verified['path'], bytes=verified['bytes'], cached=True)
        except InstallerError:
            pass
    temporary = target_dir / (uuid.uuid4().hex + '.part')
    limit = expected_size if expected_size is not None else MAX_INSTALLER_BYTES
    hasher = hashlib.sha256()
    received = 0
    try:
        try:
            response_cm = open_github(release['url'])
        except FirmwareError as error:
            raise InstallerError(str(error), error.http_status) from error
        with response_cm as response, temporary.open('xb') as output:
            header_size = response.headers.get('Content-Length') if hasattr(response, 'headers') and response.headers else None
            total = expected_size or (int(header_size) if header_size and header_size.isdigit() else None)
            if total is not None and total > MAX_INSTALLER_BYTES:
                raise InstallerError('安装包大小超过允许上限（128 MiB）')
            while True:
                chunk = response.read(min(65536, limit - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > limit:
                    raise InstallerError('下载长度超过允许范围，已停止')
                hasher.update(chunk)
                output.write(chunk)
                if progress:
                    progress(received, total)
        if expected_size is not None and received != expected_size:
            raise InstallerError('下载文件大小与 Release 资产不一致')
        if hasher.hexdigest() != expected_sha:
            raise InstallerError('下载安装包的 SHA256 校验不匹配，已丢弃文件')
        temporary.replace(destination)
        return dict(release, path=str(destination.resolve()), bytes=received, cached=False)
    finally:
        if temporary.exists():
            temporary.unlink()


def launch_installer(path, expected_sha256=None, silent=False, wait_pid=None):
    if os.name != 'nt':
        raise InstallerError('自动运行安装向导仅支持 Windows，请手动查看已下载的安装包')
    resolved = Path(path).expanduser().resolve()
    if expected_sha256:
        verify_installer_file(resolved, expected_sha256)
    elif not resolved.is_file():
        raise InstallerError('安装包文件不存在')

    # Capture the bootloader PID before the console exits. A CIM lookup of the
    # console after PowerShell starts can miss it and leave the bootloader running.
    pid_to_wait = int(wait_pid) if wait_pid is not None else os.getpid()
    env = powershell_environment()
    env['RMLINK_INSTALLER_PATH'] = str(resolved)
    frozen = getattr(sys, 'frozen', False)
    parent_check = ''
    if frozen and wait_pid is None:
        env['RMLINK_INSTALLER_CALLER'] = sys.executable
        parent_check = f'''
    $parent = Get-Process -Id {os.getppid()} -ErrorAction SilentlyContinue
    if ($parent.Path -eq $env:RMLINK_INSTALLER_CALLER) {{ $clients += $parent }}'''
    installer_args = "'/LANG=chinesesimplified'"
    if silent:
        installer_args += ", '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'"
    script = f'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {{
    $clients = @(Get-Process -Id {pid_to_wait} -ErrorAction SilentlyContinue){parent_check}
    if ($clients.Count) {{ Wait-Process -InputObject $clients -Timeout 15 }}
    $setup = Start-Process -FilePath $env:RMLINK_INSTALLER_PATH -ArgumentList @({installer_args}) -WindowStyle Normal -PassThru
    Write-Output ('Installer process started (PID ' + $setup.Id + ')')
}} catch {{
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}}
'''
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    log_path = resolved.with_name(f'installer-launch-{uuid.uuid4().hex[:12]}.log')
    kernel = None
    if frozen:
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW(None)
    try:
        with log_path.open('xb') as log:
            # DETACHED_PROCESS overrides CREATE_NO_WINDOW and can cause Windows
            # PowerShell to exit without evaluating the command.
            subprocess.Popen(
                [windows_powershell(), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                **hidden_process_options(),
            )
    finally:
        if kernel:
            kernel.SetDllDirectoryW(sys._MEIPASS)
    action = '静默安装' if silent else '打开中文安装向导'
    return dict(
        state='installer_launched',
        path=str(resolved),
        silent=bool(silent),
        log=str(log_path),
        message=f'控制台退出后将{action}。启动日志：{log_path}',
    )
