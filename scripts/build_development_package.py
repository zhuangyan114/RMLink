"""Allowlisted, hash-addressed Windows development bundle; no local secrets."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import re

ROOT=Path(__file__).resolve().parents[1]


def select_firmware(root):
    receipt=json.loads((root/'artifacts/hardware/rmlink-last-flash.json').read_text(encoding='utf-8-sig'))
    profile=receipt.get('profile','')
    if not re.fullmatch(r'firmware-target-tcp512-core0(?:-psram)?',profile):
        raise RuntimeError('Only the last flashed target/TCP512/CPU0 profile may be packaged')
    directory=root/'artifacts/build'/profile
    config=(directory/'sdkconfig').read_bytes()
    binary=(directory/'rmlink.bin').read_bytes()
    if hashlib.sha256(binary).hexdigest()!=receipt['binarySha256'].lower() or hashlib.sha256(config).hexdigest()!=receipt['sdkconfigSha256'].lower():
        raise RuntimeError('Firmware changed since the recorded physical flash; qualify it before packaging')
    settings=config.decode()
    for setting in ('CONFIG_RMLINK_EXECUTOR_CORE=0','CONFIG_RMLINK_TARGET_PINS_CONFIRMED=y','CONFIG_DEBUG_PROBE_IFACE_SWD=y'):
        if setting not in settings.splitlines():raise RuntimeError('Firmware profile configuration mismatch: '+setting)
    if bool(receipt.get('imageCachePsram'))!=profile.endswith('-psram'):
        raise RuntimeError('PSRAM profile/receipt mismatch')
    description=json.loads((directory/'project_description.json').read_text())
    return directory,receipt,description['project_version']


def main():
    version=json.loads((ROOT/'host/sdk/package.json').read_text())['version']
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()
    clean=not subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain']).strip()
    if not clean:raise RuntimeError('Commit the reviewed source and validation documents before packaging')
    firmware,receipt,firmware_version=select_firmware(ROOT)
    files={}
    def add(destination,source):files[destination]=source.read_bytes()
    add('bin/RMLinkConnector.exe',ROOT/'artifacts/build/connector/dist/RMLinkConnector.exe')
    for name in ('rmlink.dll','rmlink.h','rmlink.def','librmlink.a',f'rmlink-client-{version}.tgz'):
        add('sdk/'+name,ROOT/'artifacts/build/sdk'/name)
    for name in ('api-v1.md','subscriptions-implementation.md','quickstart.md','recording-format.md',
                 'subscription-performance-20261015.md','subscription-performance-20261015.json','subscription-checkpoint-024.json',
                 'orbit-matrix-acceptance-024.json','subscription-checkpoint-025.json','subscription-checkpoint-026.json'):
        add('docs/'+name,ROOT/'docs'/name)
    for name in ('buffered-flash-plan.md','buffered-flash-protocol.md','buffered-flash-0213.md',
                 'buffered-flash-0214.md','device-diagnostics.md','clock-calibration-0216.md','overnight-20261016.md','flash-subscription-checkpoint-028.json'):
        add('docs/'+name,ROOT/'docs'/name)
    for name in ('Start.ps1','Wait-Ready.ps1','Install.ps1','README.md'):
        add(name,ROOT/'host/distribution'/name)
    for source in sorted((ROOT/'artifacts/build/connector/dist').glob('*.txt')):
        add('licenses/'+source.name,source)
    add('licenses/Firmware-LICENSE',ROOT/'firmware/LICENSE')
    add('firmware/dependencies.lock',ROOT/'firmware/dependencies.lock')
    add('docs/reference-versions.json',ROOT/'docs/reference-versions.json')
    description=json.loads((firmware/'project_description.json').read_text())
    sdk=Path(description['idf_path'])
    for relative in ('LICENSE','components/freertos/FreeRTOS-Kernel/LICENSE.md','components/lwip/lwip/COPYING',
                     'components/mbedtls/mbedtls/LICENSE','components/esp_wifi/lib/LICENSE',
                     'components/esp_phy/lib/LICENSE','components/esp_coex/lib/LICENSE'):
        source=sdk/relative
        if source.is_file():add('licenses/esp-idf/'+relative,source)
    for component in sorted((ROOT/'firmware/managed_components').iterdir()):
        for name in ('LICENSE','LICENSE.txt','NOTICE','NOTICE.txt'):
            source=component/name
            if source.is_file():add('licenses/components/'+component.name+'/'+name,source)
    add('firmware/rmlink.bin',firmware/'rmlink.bin')
    add('firmware/bootloader/bootloader.bin',firmware/'bootloader/bootloader.bin')
    add('firmware/partition_table/partition-table.bin',firmware/'partition_table/partition-table.bin')
    add('firmware/flasher_args.json',firmware/'flasher_args.json')
    manifest={'version':version,'source_commit':commit,'source_clean':clean,
        'firmware_version':firmware_version,'firmware_profile':receipt['profile'],
        'firmware_sdkconfig_sha256':receipt['sdkconfigSha256'].lower(),
        'firmware_last_physical_flash_at':receipt['flashedAt'],
        'scope':'development; F103C8 target, subscriptions and recording measured; installed VS Code and other targets pending',
        'kernel_driver_included':False,'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
    files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output=ROOT/'artifacts/packages';output.mkdir(parents=True,exist_ok=True)
    path=output/f'RMLink-{version}-windows-x64-dev.zip'
    if path.exists():
        raise RuntimeError('Versioned package already exists; preserve it and use a new version for a new checkpoint')
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():raise RuntimeError('ZIP integrity error')
        for item in manifest['files']:
            if hashlib.sha256(archive.read(item['path'])).hexdigest()!=item['sha256']:raise RuntimeError('Manifest mismatch')
    digest=hashlib.sha256(path.read_bytes()).hexdigest();path.with_suffix('.sha256').write_text(digest+'  '+path.name+'\n')
    print(json.dumps({'package':str(path),'bytes':path.stat().st_size,'sha256':digest,'files':len(files),'source_commit':commit,'source_clean':clean}))

if __name__=='__main__':main()
