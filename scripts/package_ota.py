"""Build GitHub OTA assets from validated ESP-IDF application builds."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from rmlink_connector.firmware import inspect_image, MANIFEST_NAME


def package(builds, output, channel='stable'):
    if channel not in ('stable','testing'):
        raise ValueError('Choose stable or testing release channel')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    images=[];version=None;profiles=set()
    for build in builds:
        build=Path(build)
        config=(build/'sdkconfig').read_text(encoding='utf-8')
        if 'CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y' not in config.splitlines():
            raise ValueError('OTA assets require a rollback-enabled build')
        info=inspect_image(build/'rmlink.bin')
        if version is not None and version!=info['version']:
            raise ValueError('All release images must have one version')
        version=info['version']
        if info['profile'] in profiles:
            raise ValueError('Only one image per hardware profile may be published')
        profiles.add(info['profile'])
        name=f'rmlink-{version}-{info["profile"]}.bin'
        destination=output/name
        data=(build/'rmlink.bin').read_bytes()
        if destination.exists() and destination.read_bytes()!=data:
            raise FileExistsError('Preserve existing versioned OTA assets: '+str(destination))
        destination.write_bytes(data)
        images.append(dict(file=name,size=info['bytes'],sha256=info['sha256'],profile=info['profile'],
                           elf_sha256=info['elf_sha256'],target_pins=info['target_pins'],psram=info['psram']))
    if not images:raise ValueError('At least one build is required')
    manifest=dict(schema=1,chip='esp32s3',version=version,channel=channel,images=images)
    path=output/MANIFEST_NAME
    encoded=(json.dumps(manifest,indent=2)+'\n').encode()
    if path.exists() and path.read_bytes()!=encoded:
        raise FileExistsError('Preserve existing OTA manifest: '+str(path))
    path.write_bytes(encoded)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--channel',choices=('stable','testing'),default='stable')
    args=parser.parse_args()
    print(json.dumps(package(args.build,args.output,args.channel),indent=2))
