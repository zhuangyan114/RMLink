"""Compile and fault-test the actual firmware OTA state machine without hardware."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/tests/ota-native')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    for migration in (False, True):
        exe=args.output/('ota_migration_test.exe' if migration else 'ota_test.exe')
        subprocess.run([shutil.which('gcc') or 'gcc','-std=c11','-Wall','-Wextra','-Werror',
            '-DCONFIG_RMLINK_OTA_BOARD_V1_MIGRATION='+str(int(migration)),
            '-I'+str(ROOT/'tests/ota/stubs'),'-I'+str(ROOT/'firmware/components/debug_probe/include'),
            str(ROOT/'firmware/components/debug_probe/rmlink_ota.c'),str(ROOT/'tests/ota/ota_test.c'),'-o',str(exe)],check=True)
        subprocess.run([str(exe.resolve())],check=True)
