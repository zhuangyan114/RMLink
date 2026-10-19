"""Synchronize the source-built package into an Orbit integration worktree.

Applies only the tracked incremental patch; never resets/stages user changes.
Run Build-FlashAlgorithm.ps1 before synchronizing. No device access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.target_pack import PACK,PACK_TAG,algorithm_bytes


def sync(orbit):
    for target in PACK['targets']:algorithm_bytes(target)
    patch=ROOT/'host/orbit/patches/unified-target-pack.patch'
    copies={ROOT/'host/algorithms/targets.json':orbit/'src/ozone-backend/target-pack.json'}
    native=orbit/'out/native/win32-x64';native.mkdir(parents=True,exist_ok=True)
    for name,meta in PACK['algorithms'].items():
        copies[ROOT/'artifacts/build/flash-algorithms'/meta['fileName']]=native/f'orbit-{name}-flash-algorithm.bin'
        source_name='stm32f407' if name=='stm32f407ig' else name
        for suffix in ['c','ld']:
            source=ROOT/'host/algorithms'/name/f'algorithm.{suffix}'
            destination=orbit/f'native/cmsis-dap-flash-algorithm/{source_name}_flash_algorithm.{suffix}'
            relative=destination.relative_to(orbit).as_posix()
            if destination.exists() and destination.read_bytes()!=source.read_bytes():
                if subprocess.run(['git','-C',str(orbit),'diff','--quiet','--',relative]).returncode:
                    raise ValueError('Preserve existing Orbit algorithm edits before synchronizing: '+relative)
            copies[source]=destination
    for name in ['rmlink-client.mjs','rmlink-client.d.ts']:
        copies[ROOT/'host/sdk'/name]=orbit/'clients/rmlink'/name
    tests=ROOT/'host/orbit/tests'
    if tests.exists():
        for source in tests.glob('*.test.ts'):copies[source]=orbit/'src/ozone-backend'/source.name
    check=subprocess.run(['git','-C',str(orbit),'apply','--check',str(patch)],capture_output=True)
    if check.returncode==0:subprocess.run(['git','-C',str(orbit),'apply',str(patch)],check=True)
    elif subprocess.run(['git','-C',str(orbit),'apply','--reverse','--check',str(patch)],capture_output=True).returncode:
        raise ValueError('Orbit integration context differs; inspect the incremental patch and preserve local edits')
    for source,destination in copies.items():
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    # TypeScript resolves .mjs imports through .d.mts before .d.ts.
    shutil.copyfile(ROOT/'host/sdk/rmlink-client.d.ts',orbit/'clients/rmlink/rmlink-client.d.mts')
    receipt={'pack_tag':PACK_TAG,'pack_version':PACK['packVersion'],'targets':[t['name'] for t in PACK['targets']],
             'integration_sha256':hashlib.sha256(patch.read_bytes()).hexdigest()}
    (native/'target-pack-manifest.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(f'Orbit and RMLink share {len(PACK["targets"])} targets; tag={PACK_TAG:08x}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('orbit',type=Path)
    sync(parser.parse_args().orbit.resolve())
