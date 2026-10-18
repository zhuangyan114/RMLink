"""RMLink application image validation and bounded GitHub Release downloads."""
import hashlib
import json
from pathlib import Path
import re
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

DEFAULT_REPOSITORY = 'zhuangyan114/RMLink'
MAX_IMAGE = 3 * 1024 * 1024
MANIFEST_NAME = 'rmlink-ota-manifest.json'


class FirmwareError(ValueError):
    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


def image_info(data):
    if not 352 <= len(data) <= MAX_IMAGE or data[0] != 0xe9 or not 1 <= data[1] <= 16:
        raise FirmwareError('请选择完整的 RMLink ESP32-S3 应用 .bin，不支持合并镜像或引导程序')
    if int.from_bytes(data[12:14], 'little') != 9:
        raise FirmwareError('固件目标芯片不是 ESP32-S3')
    position, checksum = 24, 0xef
    for index in range(data[1]):
        if position + 8 > len(data):
            raise FirmwareError('固件段头被截断')
        size = struct.unpack_from('<I', data, position + 4)[0]
        position += 8
        if position + size > len(data) or (index == 0 and size < 320):
            raise FirmwareError('固件段长度无效')
        for value in data[position:position + size]:
            checksum ^= value
        position += size
    checksum_at = (position + 16) // 16 * 16 - 1
    if checksum_at >= len(data) or data[checksum_at] != checksum:
        raise FirmwareError('ESP32 固件校验和不匹配')
    end = checksum_at + 1
    if data[23] == 1:
        if data[end:end + 32] != hashlib.sha256(data[:end]).digest():
            raise FirmwareError('ESP32 镜像 SHA256 不匹配，文件可能损坏')
        end += 32
    elif data[23] != 0:
        raise FirmwareError('镜像摘要标志无效')
    if end != len(data):
        raise FirmwareError('应用镜像包含未知尾部数据；请导入未合并的 RMLink 应用镜像')
    if data[32:36] != struct.pack('<I', 0xabcd5432) or data[80:112].split(b'\0', 1)[0] != b'rmlink':
        raise FirmwareError('该应用镜像不属于 RMLink')
    try:
        version = data[48:80].split(b'\0', 1)[0].decode('ascii')
    except UnicodeDecodeError as error:
        raise FirmwareError('固件版本字段无效') from error
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(int(n) > 255 for n in version.split('.')):
        raise FirmwareError('固件版本需为 major.minor.patch，各段不超过 255')
    metadata = data[288:352]
    if metadata[:8] != b'RMLKOTA1':
        raise FirmwareError('此固件没有 RMLink OTA 配置描述，请使用 0.3.0 或更新的 OTA 构建')
    schema, layout, flags, swdio, swclk, nrst = struct.unpack_from('<IIIiii', metadata, 8)
    if schema != 1 or layout != 1 or flags & ~15 or any(metadata[32:]):
        raise FirmwareError('不支持的 RMLink OTA 配置格式')
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), chip='ESP32-S3',
                version=version, profile=hashlib.sha256(metadata).hexdigest()[:16],
                elf_sha256=data[176:208].hex(), target_pins=bool(flags & 1), psram=bool(flags & 2),
                swdio=swdio, swclk=swclk, nrst=nrst, layout=layout, upgrade_supported=True)


def inspect_image(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.bin' or not 352 <= path.stat().st_size <= MAX_IMAGE:
        raise FirmwareError('请选择不超过 3 MiB 的 RMLink 应用 .bin 文件')
    return dict(path=str(path), **image_info(path.read_bytes()))


def repository_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) or '..' in value:
        raise FirmwareError('GitHub 仓库格式应为 owner/repo')
    return value


def github_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not (
            host in ('api.github.com', 'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com')
            or host.endswith('.githubusercontent.com')):
        raise FirmwareError('固件下载仅允许 GitHub 的 HTTPS 地址')
    return url


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, github_url(newurl))


def open_github(url):
    request = urllib.request.Request(github_url(url), headers={
        'User-Agent': 'RMLink-firmware-updater', 'Accept': 'application/vnd.github+json'
        if urllib.parse.urlsplit(url).hostname == 'api.github.com' else '*/*'})
    try:
        return urllib.request.build_opener(GitHubRedirect()).open(request, timeout=20)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise FirmwareError('仓库尚无正式 Release 或缺少 OTA 发布资产', error.code) from error
        raise FirmwareError(f'GitHub 请求失败（HTTP {error.code}），请稍后重试', error.code) from error
    except urllib.error.URLError as error:
        raise FirmwareError('无法连接 GitHub，请检查网络后重试') from error


def github_json(url, limit, expected_type=dict):
    with open_github(url) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise FirmwareError('GitHub 元数据超过允许大小')
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as error:
        raise FirmwareError('GitHub 元数据不是有效的 JSON') from error
    if not isinstance(value, expected_type):
        raise FirmwareError('GitHub 元数据格式无效')
    return value


def latest_release(repository=DEFAULT_REPOSITORY, include_prereleases=False):
    repository = repository_name(repository)
    manifest = None
    try:
        if include_prereleases:
            releases = github_json(f'https://api.github.com/repos/{repository}/releases?per_page=100', 1024 * 1024, list)
            candidates = [r for r in releases if isinstance(r, dict) and not r.get('draft')
                          and re.fullmatch(r'v\d+\.\d+\.\d+', str(r.get('tag_name', '')))]
            if not candidates:
                raise FirmwareError('仓库尚无可用测试版或正式版固件')
            release = max(candidates, key=lambda r: tuple(map(int, r['tag_name'][1:].split('.'))))
        else:
            release = github_json(f'https://api.github.com/repos/{repository}/releases/latest', 1024 * 1024)
    except FirmwareError as error:
        if error.http_status not in (403,429):
            raise
        # GitHub's public API quota is shared by IP. The official latest-release
        # redirect provides the same stable tag without an API token.
        prefix = '/' + repository + '/releases/tag/'
        if include_prereleases:
            with open_github(f'https://github.com/{repository}/releases.atom') as response:
                feed = response.read(1024*1024+1)
            if len(feed)>1024*1024 or b'<!DOCTYPE' in feed.upper():
                raise FirmwareError('GitHub 发布订阅格式或大小无效')
            try:
                root = ET.fromstring(feed)
            except ET.ParseError as parse_error:
                raise FirmwareError('GitHub 发布订阅不是有效的 Atom') from parse_error
            tags = []
            for link in root.findall('{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}link'):
                location = urllib.parse.urlsplit(link.get('href',''))
                if location.scheme=='https' and location.hostname=='github.com' and location.path.startswith(prefix):
                    candidate = urllib.parse.unquote(location.path[len(prefix):])
                    if re.fullmatch(r'v\d+\.\d+\.\d+', candidate):tags.append(candidate)
            if not tags:
                raise FirmwareError('仓库尚无可用测试版或正式版固件')
            tag = max(tags, key=lambda t:tuple(map(int,t[1:].split('.'))))
        else:
            with open_github(f'https://github.com/{repository}/releases/latest') as response:
                location = urllib.parse.urlsplit(response.geturl())
            if location.hostname != 'github.com' or not location.path.startswith(prefix):
                raise FirmwareError('仓库尚无正式 Release；请先发布带 OTA 清单的版本')
            tag = urllib.parse.unquote(location.path[len(prefix):])
        base = f'https://github.com/{repository}/releases/download/{urllib.parse.quote(tag,safe="")}/'
        manifest = github_json(base + MANIFEST_NAME, 65536)
        entries = manifest.get('images', [])
        if not isinstance(entries,list):
            raise FirmwareError('OTA 发布清单格式无效')
        release = dict(tag_name=tag,assets=[dict(name=MANIFEST_NAME,browser_download_url=base+MANIFEST_NAME)] + [
            dict(name=item['file'],size=item.get('size'),browser_download_url=base+item['file'])
            for item in entries if isinstance(item,dict) and isinstance(item.get('file'),str)])
        if include_prereleases:
            release['prerelease'] = {'testing':True,'stable':False}.get(manifest.get('channel'))
    if release.get('draft') or (release.get('prerelease') and not include_prereleases):
        raise FirmwareError('最新发布不是正式版本')
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) > 100:
        raise FirmwareError('GitHub Release 资产列表无效')
    if any(not isinstance(asset,dict) or not isinstance(asset.get('name'),str) for asset in assets):
        raise FirmwareError('GitHub Release 资产名称无效')
    by_name = {asset['name']: asset for asset in assets}
    if len(by_name) != len(assets) or MANIFEST_NAME not in by_name:
        raise FirmwareError('最新 Release 尚未发布 RMLink OTA 清单；请先上传 OTA 发布包')
    def asset_url(asset):
        url = github_url(asset.get('browser_download_url', ''))
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname != 'github.com' or not parsed.path.startswith('/' + repository + '/releases/download/'):
            raise FirmwareError('Release 下载地址不属于所选仓库')
        return url
    manifest = manifest or github_json(asset_url(by_name[MANIFEST_NAME]), 65536)
    if manifest.get('schema') != 1 or manifest.get('chip') != 'esp32s3' or not isinstance(manifest.get('images'), list):
        raise FirmwareError('OTA 发布清单格式不受支持')
    version = manifest.get('version', '')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version) or release.get('tag_name') != 'v' + version:
        raise FirmwareError('Release 标签必须与 OTA 清单版本一致（vmajor.minor.patch）')
    images, profiles = [], set()
    for image in manifest['images']:
        if not isinstance(image, dict):
            raise FirmwareError('OTA 镜像条目无效')
        name, profile, digest = image.get('file'), image.get('profile'), image.get('sha256')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.bin', name) or name not in by_name:
            raise FirmwareError('OTA 清单引用了缺失或无效的应用镜像')
        if not isinstance(profile, str) or not re.fullmatch('[0-9a-f]{16}', profile) or profile in profiles:
            raise FirmwareError('OTA 清单的硬件配置编号无效或重复')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise FirmwareError('OTA 清单缺少有效 SHA256')
        if type(image.get('size')) is not int or not 352 <= image['size'] <= MAX_IMAGE or by_name[name].get('size') != image['size']:
            raise FirmwareError('OTA 清单与 Release 资产大小不一致')
        profiles.add(profile)
        images.append(dict(image, url=asset_url(by_name[name])))
    if not images:
        raise FirmwareError('OTA 清单没有应用镜像')
    return dict(repository=repository, version=version, tag=release['tag_name'],
                published_at=release.get('published_at'), prerelease=release.get('prerelease',False), images=images)


def download_release(release, profile, cache):
    image = next((item for item in release['images'] if item['profile'] == profile), None)
    if image is None:
        raise FirmwareError('最新 Release 没有与当前设备硬件配置一致的固件')
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    destination = cache / (image['sha256'] + '.bin')
    def check(path):
        info = inspect_image(path)
        if any(info[key] != expected for key, expected in (
                ('sha256', image['sha256']), ('bytes', image['size']), ('profile', profile), ('version', release['version']))):
            raise FirmwareError('下载固件的摘要、版本或硬件配置与 Release 清单不一致')
        return info
    if destination.exists():
        return check(destination)
    temporary = cache / (uuid.uuid4().hex + '.bin')
    try:
        with open_github(image['url']) as response, temporary.open('xb') as output:
            received = 0
            while True:
                chunk = response.read(min(65536, image['size'] - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > image['size']:
                    raise FirmwareError('下载长度超过 Release 清单，已停止')
                output.write(chunk)
        info = check(temporary)
        temporary.replace(destination)
        info['path'] = str(destination.resolve())
        return info
    finally:
        if temporary.exists():
            temporary.unlink()
