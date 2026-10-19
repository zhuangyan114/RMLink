"""Offline check of source package, Orbit, firmware and frozen EXE contents."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.target_pack import PACK,PACK_TAG,algorithm_bytes


def verify(orbit,firmware,connector):
    report={'pack_tag':PACK_TAG,'pack_version':PACK['packVersion'],'hardware_tested':False,'algorithms':{}}
    canonical=(ROOT/'host/algorithms/targets.json').read_bytes()
    assert json.loads((orbit/'src/ozone-backend/target-pack.json').read_text(encoding='utf-8'))==PACK
    image=firmware.read_bytes()
    assert struct.pack('<I',PACK_TAG) in image,'Firmware does not contain the current package tag'
    from PyInstaller.archive.readers import CArchiveReader
    archive=CArchiveReader(str(connector))
    names={name.replace('\\','/'):name for name in archive.toc}
    def frozen(name):return archive.extract(names['rmlink_algorithms/'+name])
    assert json.loads(frozen('targets.json'))==PACK
    assert json.loads(frozen('manifest.json'))['pack_tag']==PACK_TAG
    for target in PACK['targets']:
        name=target['algorithm'];code=algorithm_bytes(target);meta=PACK['algorithms'][name]
        assert (orbit/f'out/native/win32-x64/orbit-{name}-flash-algorithm.bin').read_bytes()==code
        assert frozen(meta['fileName'])==code
        assert code in image,'Firmware is missing algorithm bytes: '+name
        report['algorithms'][name]={'bytes':len(code),'sha256':hashlib.sha256(code).hexdigest(),'orbit':True,'firmware':True,'frozen_connector':True}
    report['firmware_sha256']=hashlib.sha256(image).hexdigest()
    report['connector_sha256']=hashlib.sha256(connector.read_bytes()).hexdigest()
    report['package_json_sha256']=hashlib.sha256(canonical).hexdigest()
    output=ROOT/'artifacts/logs/shared-target-package-verification.json'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--orbit',type=Path,required=True);parser.add_argument('--firmware',type=Path,required=True);parser.add_argument('--connector',type=Path,required=True)
    args=parser.parse_args();verify(args.orbit,args.firmware,args.connector)
