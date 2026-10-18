"""Prevent packaging a stale/non-PSRAM image instead of the physically tested one."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from build_development_package import select_firmware,firmware_assets


class PackageProfileTests(unittest.TestCase):
    def test_selects_exact_flashed_profile_and_rejects_modified_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);profile='firmware-target-tcp512-core0-psram-streamdiag-video-ota'
            directory=root/'artifacts/build'/profile;directory.mkdir(parents=True)
            config=b'CONFIG_RMLINK_EXECUTOR_CORE=0\nCONFIG_RMLINK_TARGET_PINS_CONFIRMED=y\nCONFIG_DEBUG_PROBE_IFACE_SWD=y\n'
            (directory/'sdkconfig').write_bytes(config);(directory/'rmlink.bin').write_bytes(b'qualified')
            (directory/'project_description.json').write_text('{"project_version":"0.2.16"}')
            receipt=dict(profile=profile,imageCachePsram=True,binarySha256=hashlib.sha256(b'qualified').hexdigest(),sdkconfigSha256=hashlib.sha256(config).hexdigest())
            path=root/'artifacts/hardware/rmlink-last-flash.json';path.parent.mkdir(parents=True)
            def save():path.write_text(json.dumps(receipt))
            save();selected,_,version=select_firmware(root)
            self.assertEqual((selected,version),(directory,'0.2.16'))
            (directory/'rmlink.bin').write_bytes(b'new untested build')
            with self.assertRaisesRegex(RuntimeError,'changed since'):select_firmware(root)
            (directory/'rmlink.bin').write_bytes(b'qualified');receipt['imageCachePsram']=False;save()
            with self.assertRaisesRegex(RuntimeError,'PSRAM'):select_firmware(root)
            receipt['profile']='../../unexpected';save()
            with self.assertRaisesRegex(RuntimeError,'Only the last'):select_firmware(root)

    def test_bundle_includes_bootstrap_data_and_rejects_nvs_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            flash={'0x0':'bootloader/bootloader.bin','0x8000':'partition_table/partition-table.bin',
                   '0x10000':'rmlink.bin','0x310000':'ota_data_initial.bin'}
            for name in flash.values():
                path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
            path=directory/'flasher_args.json'
            path.write_text(json.dumps({'flash_files':flash}))
            assets=firmware_assets(directory)
            self.assertEqual(assets['ota_data_initial.bin'],b'ota_data_initial.bin')
            self.assertTrue(set(flash.values()).issubset(assets))
            (directory/'ota_data_initial.bin').unlink()
            with self.assertRaises(FileNotFoundError):firmware_assets(directory)
            flash['0x9000']='nvs.bin';path.write_text(json.dumps({'flash_files':flash}))
            with self.assertRaisesRegex(RuntimeError,'Unexpected firmware flash'):firmware_assets(directory)
