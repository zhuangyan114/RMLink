"""Allowlisted, hash-addressed Windows development bundle; no local secrets."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    version=json.loads((ROOT/'host/sdk/package.json').read_text())['version']
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()
    clean=not subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain']).strip()
    files={}
    def add(destination,source):files[destination]=source.read_bytes()
    add('bin/RMLinkConnector.exe',ROOT/'artifacts/build/connector/dist/RMLinkConnector.exe')
    for name in ('rmlink.dll','rmlink.h','rmlink.def','librmlink.a',f'rmlink-client-{version}.tgz'):
        add('sdk/'+name,ROOT/'artifacts/build/sdk'/name)
    for name in ('api-v1.md','subscriptions-implementation.md','quickstart.md','recording-format.md',
                 'subscription-performance-20261015.md','subscription-performance-20261015.json'):
        add('docs/'+name,ROOT/'docs'/name)
    for name in ('Start.ps1','Install.ps1','README.md'):
        add(name,ROOT/'host/distribution'/name)
    for source in sorted((ROOT/'artifacts/build/connector/dist').glob('*.txt')):
        add('licenses/'+source.name,source)
    add('licenses/Firmware-LICENSE',ROOT/'firmware/LICENSE')
    add('firmware/rmlink.bin',ROOT/'artifacts/build/firmware-target-tcp512/rmlink.bin')
    manifest={'version':version,'source_commit':commit,'source_clean':clean,
        'scope':'development; F103C8 target, subscriptions and recording measured; installed VS Code and other targets pending',
        'kernel_driver_included':False,'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
    files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output=ROOT/'artifacts/packages';output.mkdir(parents=True,exist_ok=True)
    path=output/f'RMLink-{version}-windows-x64-dev.zip'
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
