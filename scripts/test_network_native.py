"""Fault-test the actual Wi-Fi recovery handlers without hardware."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/tests/network-native')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    exe = args.output / 'network_test.exe'
    subprocess.run([
        shutil.which('gcc') or 'gcc', '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-Wno-unused-variable', '-I' + str(ROOT / 'tests/network'),
        str(ROOT / 'tests/network/network_test.c'), '-o', str(exe),
    ], check=True)
    subprocess.run([str(exe.resolve())], check=True)
