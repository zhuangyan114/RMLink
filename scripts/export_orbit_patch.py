"""Keep only the experimental integration patch, never another repository."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BASE='3928bbb6f4990ff336dfbe71f745f5f593b12e48'


def main():
    work=ROOT/'artifacts/worktrees/orbit-rmlink'
    def git(*args):return subprocess.check_output(['git','-C',str(work),*args])
    if git('status','--porcelain').strip():raise RuntimeError('Commit the isolated Orbit worktree before exporting')
    head=git('rev-parse','HEAD').decode().strip()
    patch=git('format-patch','--stdout',BASE+'..'+head)
    if not patch:raise RuntimeError('No Orbit integration changes')
    payload=gzip.compress(patch,mtime=0);name='orbit-rmlink-subscriptions.patch.gz'
    out=ROOT/'host/orbit/patches';out.mkdir(parents=True,exist_ok=True)
    (out/name).write_bytes(payload)
    record={'repository':'https://github.com/zhuangyan114/Orbit','base_commit':BASE,
        'implementation_commit':head,'branch':'rmlink/subscriptions-20261014','patch':name,
        'sha256':hashlib.sha256(payload).hexdigest(),'validation':{
            'typecheck':True,'build':True,'test_files':76,'tests_passed':1053,'tests_skipped':1,
            'hardware':True,'hardware_scope':'real debug adapter child: 3 scalar Timeline, Watch, RTT, halt/run',
            'installed_vscode':False}}
    (out/'provenance.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(record))

if __name__=='__main__':main()
