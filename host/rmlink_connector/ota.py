"""Connector-owned probe OTA transactions; never acquire the SWD target."""
import asyncio
import base64
import re
import struct
import time
import uuid

from .firmware import FirmwareError, MAX_IMAGE

ERRORS = {1: '升级参数无效', 2: '设备尚未迁移 OTA 分区，请先通过 USB 安装引导程序和分区表',
          3: '设备忙或升级会话不匹配', 4: '升级数据偏移或状态不匹配',
          5: '固件完整性验证失败，未切换启动分区', 6: '设备 Flash 操作失败', 7: '固件硬件配置不匹配'}


def reply_ok(reply, command):
    if len(reply) < 3 or reply[:2] != bytes([0x83, command]):
        raise FirmwareError('设备不支持 OTA，请先通过 USB 安装 0.3.0 或更新的引导程序、分区表和固件')
    if reply[2]:
        raise FirmwareError(ERRORS.get(reply[2], '设备拒绝升级命令'))
    return reply[3:]


def decode_info(reply):
    reply_ok(reply, 0)
    if len(reply) != 63 or reply[3] != 1:
        raise FirmwareError('不支持的设备 OTA 协议版本')
    capacity, received, size = struct.unpack_from('<III', reply, 6)
    return dict(supported=(reply[4] & 3) == 3, methods=['usb', 'wifi'] if (reply[4] & 3) == 3 else [],
                rollback=bool(reply[4] & 2), state={0:'idle',1:'receiving',2:'verified',3:'rebooting'}.get(reply[5], 'unknown'),
                max_bytes=capacity, received=received, bytes=size, version='.'.join(map(str,reply[18:21])),
                elf_sha256=reply[21:53].hex(), profile=reply[53:61].hex(),
                boot_state=reply[61], slot=reply[62],
                message='' if (reply[4] & 3) == 3 else '请通过 USB 首次迁移 OTA 引导程序与分区表')


class FirmwareUpdates:
    def __init__(self, connector, idle):
        self.connector, self.idle = connector, idle
        self.uploads, self.last = {}, {}

    async def info(self, key):
        broker = self.connector.brokers.get(key)
        if not broker or broker.failed:
            return dict(device_id=key, supported=False, connected=False, **self.last.get(key, {}))
        version = self.connector.devices[key].firmware if key in self.connector.devices else '0.0.0'
        try:
            legacy = tuple(map(int, version.split('.'))) < (0,3,0)
        except ValueError:
            legacy = True
        if legacy:
            return dict(device_id=key, version=version, supported=False, methods=[], state='migration_required',
                        message='当前固件不支持 OTA，请先通过 USB 安装 0.3.0 的引导程序、分区表和应用')
        info = decode_info((await broker.submit('ota-status', [b'\x83\x00'], 'background'))[0])
        expected = self.last.get(key, {}).get('expected_elf_sha256')
        if expected and info['elf_sha256'] == expected and info['boot_state'] == 2:
            self.last[key]['state'] = 'boot_confirmed'
        return dict(info, device_id=key, connected=True, last_update=self.last.get(key))

    async def release(self, key, abort=False):
        upload = self.uploads.pop(key, None)
        if not upload:
            return
        broker = upload['broker']
        try:
            if abort and not broker.failed:
                reply_ok((await broker.submit(upload['source'], [b'\x83\x05'], 'background'))[0], 5)
        finally:
            if broker.maintenance == upload['source']:
                broker.maintenance = None
            self.connector.switching.discard(key)

    async def expire(self):
        for key, upload in list(self.uploads.items()):
            if time.monotonic() - upload['touched'] > 100:
                try:
                    await self.release(key, abort=True)
                except (OSError, ValueError, ConnectionError):
                    pass
                self.last[key] = dict(state='expired', message='升级会话超时；原启动分区保持不变')

    async def route(self, key, body):
        c = self.connector
        async with c.refresh_lock:
            action = body.get('action')
            if action == 'begin':
                if key in self.uploads:
                    raise FirmwareError('已有固件上传会话')
                self.idle(key)
                info = await self.info(key)
                if not info.get('supported'):
                    raise FirmwareError(info.get('message') or '设备离线或尚未迁移 OTA')
                size, digest, profile, elf = (body.get(name) for name in ('size','sha256','profile','elf_sha256'))
                if type(size) is not int or not 352 <= size <= min(MAX_IMAGE, info['max_bytes']):
                    raise FirmwareError('应用固件大小超过备用分区')
                if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or not isinstance(elf,str) or not re.fullmatch('[0-9a-f]{64}',elf):
                    raise FirmwareError('请提供有效的固件和 ELF SHA256')
                if profile != info['profile']:
                    raise FirmwareError('固件硬件配置与当前设备不一致，未写入 Flash')
                broker = c.brokers[key]
                self.idle(key)  # Re-check after the awaited INFO: USBip may have acquired a target meanwhile.
                if broker.maintenance:
                    raise FirmwareError('设备正在维护')
                source = 'ota-' + uuid.uuid4().hex
                broker.maintenance = source; c.switching.add(key)
                upload = dict(source=source,broker=broker,size=size,received=0,touched=time.monotonic(),elf=elf)
                self.uploads[key] = upload
                try:
                    if key in c.driver.ports:
                        await c.driver.detach(key)
                    packet = b'\x83\x01' + struct.pack('<I',size) + bytes.fromhex(digest)
                    reply_ok((await broker.submit(source,[packet],'background'))[0],1)
                except BaseException:
                    await self.release(key)
                    raise
                return dict(upload_id=source, received=0, bytes=size)
            upload = self.uploads.get(key)
            if not upload or body.get('upload_id') != upload['source']:
                raise FirmwareError('固件上传会话不存在或编号不匹配')
            upload['touched'] = time.monotonic()
            broker, source = upload['broker'], upload['source']
            if action == 'abort':
                await self.release(key, abort=True)
                return dict(state='aborted')
            if action == 'write':
                if type(body.get('offset')) is not int or body['offset'] != upload['received']:
                    raise FirmwareError('上传偏移不匹配；未重放任何写入')
                encoded = body.get('data')
                if not isinstance(encoded,str) or len(encoded) > 5464:
                    raise FirmwareError('每批上传数据最多 4096 字节')
                try:
                    data = base64.b64decode(encoded, validate=True)
                except ValueError as error:
                    raise FirmwareError('上传数据不是有效的 Base64') from error
                if not 1 <= len(data) <= min(4096, upload['size'] - upload['received']):
                    raise FirmwareError('上传长度超出声明的应用镜像')
                packets, offset = [], upload['received']
                for start in range(0,len(data),broker.packet_size-8):
                    chunk=data[start:start+broker.packet_size-8]
                    packets.append(b'\x83\x02'+struct.pack('<IH',offset,len(chunk))+chunk)
                    offset+=len(chunk)
                try:
                    for start in range(0,len(packets),8):
                        batch=packets[start:start+8]
                        replies=await broker.submit(source,batch,'background')
                        if len(replies)!=len(batch):
                            raise FirmwareError('设备返回的升级应答数量不完整；不自动重放')
                        for packet, reply in zip(batch,replies):
                            payload=reply_ok(reply,2)
                            expected=int.from_bytes(packet[2:6],'little')+int.from_bytes(packet[6:8],'little')
                            if len(payload)!=4 or int.from_bytes(payload,'little')!=expected:
                                raise FirmwareError('设备未确认准确的写入偏移；不自动重放')
                    upload['received']=offset
                except BaseException:
                    try: await self.release(key,abort=True)
                    except Exception: pass
                    raise
                return dict(received=offset,bytes=upload['size'])
            if action == 'finish':
                if upload['received'] != upload['size']:
                    raise FirmwareError('应用镜像尚未完整上传')
                try:
                    reply_ok((await broker.submit(source,[b'\x83\x03'],'background'))[0],3)
                    # A lost COMMIT acknowledgement is ambiguous. Never replay it.
                    self.last[key]=dict(state='commit_pending',expected_elf_sha256=upload['elf'])
                    reply_ok((await broker.submit(source,[b'\x83\x04'],'background'))[0],4)
                    self.last[key]['state']='rebooting'
                except BaseException:
                    if key not in self.last or self.last[key].get('state')!='commit_pending':
                        try: await self.release(key,abort=True)
                        except Exception: pass
                    else:
                        await self.release(key)
                    raise
                await self.release(key)
                if c.brokers.get(key) is broker:
                    c.brokers.pop(key)
                await broker.close()
                return dict(self.last[key])
            raise FirmwareError('不支持的固件升级操作')
