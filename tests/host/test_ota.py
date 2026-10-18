import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from ota_fixture import application_image, device_info
from rmlink_connector.broker import Broker
from rmlink_connector.discovery import Device
from rmlink_connector.firmware import FirmwareError, image_info, inspect_image, latest_release, download_release, github_url, open_github
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from rmlink_connector.target import TargetAccessError

ID='50787d15a924'


class ImageTests(unittest.TestCase):
    def test_image_integrity_and_hardware_profile_are_checked(self):
        image=application_image();info=image_info(image)
        self.assertEqual(info['version'],'0.3.1')
        self.assertEqual(info['swdio'],4)
        self.assertNotEqual(info['profile'],image_info(application_image(flags=1))['profile'])
        for damaged in (image[:-1],image+bytes(4),image[:400]+bytes([image[400]^1])+image[401:]):
            with self.assertRaises(FirmwareError):image_info(damaged)

    def test_github_download_matches_manifest_and_rechecks_cached_content(self):
        image=application_image();info=image_info(image)
        name='rmlink-test.bin';base='https://github.com/zhuangyan114/RMLink/releases/download/v0.3.1/'
        manifest=dict(schema=1,version='0.3.1',chip='esp32s3',images=[dict(file=name,size=len(image),
            profile=info['profile'],sha256=info['sha256'])])
        release=dict(tag_name='v0.3.1',assets=[dict(name='rmlink-ota-manifest.json',browser_download_url=base+'rmlink-ota-manifest.json'),
            dict(name=name,size=len(image),browser_download_url=base+name)])
        with tempfile.TemporaryDirectory() as directory, patch('rmlink_connector.firmware.open_github',side_effect=[
                io.BytesIO(json.dumps(release).encode()),io.BytesIO(json.dumps(manifest).encode()),io.BytesIO(image)]) as fetch:
            latest=latest_release()
            saved=download_release(latest,info['profile'],directory)
            self.assertEqual(inspect_image(saved['path'])['sha256'],info['sha256'])
            self.assertEqual(download_release(latest,info['profile'],directory)['sha256'],info['sha256'])
            self.assertEqual(fetch.call_count,3)
            Path(saved['path']).write_bytes(image[:-2])
            with self.assertRaises(FirmwareError):download_release(latest,info['profile'],directory)

    def test_missing_manifest_and_untrusted_redirects_are_rejected(self):
        with patch('rmlink_connector.firmware.open_github',return_value=io.BytesIO(b'{"assets":[]}')):
            with self.assertRaises(FirmwareError):latest_release()
        for url in ('http://github.com/x','https://github.com.evil.test/x','https://localhost/x','https://user:secret@github.com/x'):
            with self.assertRaises(FirmwareError):github_url(url)

    def test_prerelease_requires_explicit_opt_in_and_selects_highest_version(self):
        data=application_image();info=image_info(data)
        base='https://github.com/zhuangyan114/RMLink/releases/download/v0.3.1/'
        manifest=dict(schema=1,version='0.3.1',chip='esp32s3',images=[dict(file='test.bin',size=len(data),
            profile=info['profile'],sha256=info['sha256'])])
        release=dict(tag_name='v0.3.1',prerelease=True,assets=[
            dict(name='rmlink-ota-manifest.json',browser_download_url=base+'rmlink-ota-manifest.json'),
            dict(name='test.bin',size=len(data),browser_download_url=base+'test.bin')])
        with patch('rmlink_connector.firmware.open_github',return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaisesRegex(FirmwareError,'正式版本'):latest_release()
        rows=[dict(tag_name='v9.0.0',draft=True),dict(tag_name='v0.2.0',assets=[]),release]
        with patch('rmlink_connector.firmware.open_github',side_effect=[io.BytesIO(json.dumps(rows).encode()),
                io.BytesIO(json.dumps(manifest).encode())]) as fetch:
            selected=latest_release(include_prereleases=True)
        self.assertTrue(selected['prerelease']);self.assertEqual(selected['version'],'0.3.1')
        self.assertIn('/releases?per_page=100',fetch.call_args_list[0].args[0])

    def test_test_channel_rejects_empty_and_mismatched_lists(self):
        for payload in (b'[]', b'{}', b'[1,{"draft":true,"tag_name":"v1.0.0"}]'):
            with patch('rmlink_connector.firmware.open_github',return_value=io.BytesIO(payload)):
                with self.assertRaises(FirmwareError):latest_release(include_prereleases=True)

    def test_test_channel_quota_falls_back_to_official_atom_and_testing_manifest(self):
        data=application_image();info=image_info(data)
        feed=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://github.com/zhuangyan114/RMLink/releases/tag/v0.3.1"/></entry></feed>'
        manifest=dict(schema=1,version='0.3.1',channel='testing',chip='esp32s3',images=[dict(file='test.bin',
            size=len(data),profile=info['profile'],sha256=info['sha256'])])
        with patch('rmlink_connector.firmware.open_github',side_effect=[FirmwareError('quota',403),io.BytesIO(feed),
                io.BytesIO(json.dumps(manifest).encode())]):
            selected=latest_release(include_prereleases=True)
        self.assertEqual(selected['version'],'0.3.1');self.assertTrue(selected['prerelease'])

    def test_api_quota_falls_back_to_official_release_redirect(self):
        image=application_image();info=image_info(image)
        base='https://github.com/zhuangyan114/RMLink/releases/download/v0.3.1/'
        manifest=dict(schema=1,version='0.3.1',chip='esp32s3',images=[dict(file='test.bin',
            size=len(image),profile=info['profile'],sha256=info['sha256'])])
        class Redirect(io.BytesIO):
            def geturl(self):return 'https://github.com/zhuangyan114/RMLink/releases/tag/v0.3.1'
        with patch('rmlink_connector.firmware.open_github',side_effect=[FirmwareError('quota',403),
                Redirect(),io.BytesIO(json.dumps(manifest).encode())]):
            self.assertEqual(latest_release()['version'],'0.3.1')

    def test_browser_pages_do_not_receive_api_only_accept_headers(self):
        with patch('urllib.request.build_opener') as build:
            open_github('https://github.com/zhuangyan114/RMLink/releases/latest')
            request=build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Accept'),'*/*')

    def test_mismatched_or_oversized_download_is_never_cached(self):
        data=application_image();info=image_info(data)
        release=dict(version='0.3.1',images=[dict(profile=info['profile'],sha256=info['sha256'],size=len(data),
            url='https://github.com/zhuangyan114/RMLink/releases/download/v0.3.1/test.bin')])
        for bad in (data[:-1],data+bytes(1),data[:-1]+bytes([data[-1]^1])):
            with tempfile.TemporaryDirectory() as directory, patch('rmlink_connector.firmware.open_github',return_value=io.BytesIO(bad)):
                with self.assertRaises(FirmwareError):download_release(release,info['profile'],directory)
                self.assertEqual(list(Path(directory).iterdir()),[])


class OtaTransport:
    packet_size=64
    def __init__(self,profile):
        self.profile=profile;self.requests=[];self.data=bytearray();self.closed=False;self.bad_ack=False
    async def connect(self):pass
    async def close(self):self.closed=True
    async def abort(self):pass
    async def exchange_many(self,packets):
        replies=[]
        for packet in packets:
            self.requests.append(packet)
            if packet==b'\x83\x00':replies.append(device_info(self.profile));continue
            command=packet[1]
            if command==1:
                self.size=int.from_bytes(packet[2:6],'little');self.digest=packet[6:38]
            if command==2:
                offset,count=struct.unpack_from('<IH',packet,2)
                if offset!=len(self.data):raise AssertionError('Out-of-order OTA data')
                self.data+=packet[8:8+count]
                replies.append(bytes([0x83,2,0])+struct.pack('<I',len(self.data)+(1 if self.bad_ack else 0)));continue
            if command==3 and (len(self.data)!=self.size or hashlib.sha256(self.data).digest()!=self.digest):
                replies.append(b'\x83\x03\x05');continue
            replies.append(bytes([0x83,command,0]))
        return replies


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.data=application_image(payload=bytes(range(256))*24)
        self.info=image_info(self.data)
        self.connector=Connector(State(self.directory.name))
        device=Device(ID,'probe.local','127.0.0.1',3241,'0.3.0')
        self.connector.devices[ID]=device
        self.connector.state.data['paired'][ID]=device.as_dict()
        self.transport=OtaTransport(self.info['profile'])
        self.broker=Broker(self.transport,ID);await self.broker.start()
        self.connector.brokers[ID]=self.broker
        self.path=f'/v1/devices/{ID}/firmware'
    async def asyncTearDown(self):
        await self.connector.close();self.directory.cleanup()
    async def begin(self,**changes):
        body=dict(action='begin',size=len(self.data),sha256=self.info['sha256'],
            profile=self.info['profile'],elf_sha256=self.info['elf_sha256'])
        body.update(changes)
        return await self.connector.route('POST',self.path,body)
    async def write(self,upload,offset,data):
        return await self.connector.route('POST',self.path,dict(action='write',upload_id=upload['upload_id'],
            offset=offset,data=base64.b64encode(data).decode()))
    async def test_full_update_writes_only_probe_ota_and_releases_maintenance(self):
        upload=await self.begin()
        for offset in range(0,len(self.data),4096):await self.write(upload,offset,self.data[offset:offset+4096])
        result=await self.connector.route('POST',self.path,dict(action='finish',upload_id=upload['upload_id']))
        self.assertEqual(result['state'],'rebooting')
        self.assertEqual(bytes(self.transport.data),self.data)
        self.assertTrue(all(packet[0]==0x83 for packet in self.transport.requests))
        self.assertIsNone(self.broker.owner);self.assertIsNone(self.broker.maintenance)
        self.assertNotIn(ID,self.connector.switching)
    async def test_active_lease_rejects_begin_before_any_probe_command(self):
        await self.connector.route('POST','/v1/sessions',dict(device_id=ID))
        with self.assertRaises(TargetAccessError):await self.begin()
        self.assertEqual(self.transport.requests,[])
    async def test_tcp512_upload_crosses_the_metadata_prefix_in_one_packet(self):
        self.transport.packet_size=512
        upload=await self.begin()
        for offset in range(0,len(self.data),4096):await self.write(upload,offset,self.data[offset:offset+4096])
        self.assertEqual(bytes(self.transport.data),self.data)
        packets=[p for p in self.transport.requests if p[:2]==b'\x83\x02']
        self.assertEqual(len(packets[0]),512)
        self.assertTrue(all(len(packet)<=512 for packet in packets))
    async def test_raw_debug_and_forged_ota_packets_cannot_interleave(self):
        upload=await self.begin();before=len(self.transport.requests)
        self.assertEqual(await self.broker.submit('virtual-usb',[b'\x02\x01']),[b'\x02\x00'])
        self.assertEqual(await self.broker.submit('api-other',[b'\x83\x04']),[b'\x83\xff'])
        result=await self.broker.submit('api-other',[b'\x83\x00',b'\x83\x04'])
        self.assertEqual(result[-1],b'\x83\xff')
        self.assertEqual(self.transport.requests[before:],[b'\x83\x00'])
        with self.assertRaises(FirmwareError):await self.connector.route('POST','/v1/transport',dict(device_id=ID,transport='wifi'))
        with self.assertRaises(FirmwareError):await self.connector.route('POST','/v1/shutdown',{})
        await self.connector.route('POST',self.path,dict(action='abort',upload_id=upload['upload_id']))
    async def test_profile_mismatch_never_begins_a_flash_write(self):
        with self.assertRaises(FirmwareError):await self.begin(profile='0000000000000000')
        self.assertEqual(self.transport.requests,[b'\x83\x00'])
    async def test_invalid_offset_and_bad_ack_are_not_retried_or_committed(self):
        upload=await self.begin()
        with self.assertRaises(FirmwareError):await self.write(upload,1,self.data[:128])
        self.transport.bad_ack=True
        with self.assertRaises(FirmwareError):await self.write(upload,0,self.data[:128])
        self.assertIsNone(self.broker.maintenance)
        self.assertNotIn(b'\x83\x04',self.transport.requests)
        offsets=[int.from_bytes(p[2:6],'little') for p in self.transport.requests if p[:2]==b'\x83\x02']
        self.assertEqual(len(offsets),len(set(offsets)))
    async def test_digest_failure_cannot_commit_and_expiry_releases_the_probe(self):
        upload=await self.begin(sha256='0'*64)
        for offset in range(0,len(self.data),4096):await self.write(upload,offset,self.data[offset:offset+4096])
        with self.assertRaises(FirmwareError):await self.connector.route('POST',self.path,dict(action='finish',upload_id=upload['upload_id']))
        self.assertNotIn(b'\x83\x04',self.transport.requests)
        self.transport.data.clear()
        await self.begin()
        self.connector.management.firmware.uploads[ID]['touched']-=101
        await self.connector.management.firmware.expire()
        self.assertIsNone(self.broker.maintenance)
        self.assertNotIn(ID,self.connector.switching)
