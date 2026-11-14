"""Test the production user-key and status-LED state machine without hardware."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/tests/ui-native')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    exe = args.output / 'ui_test.exe'
    subprocess.run([
        shutil.which('gcc') or 'gcc', '-std=c11', '-Wall', '-Wextra', '-Werror',
        str(ROOT / 'tests/ui/ui_test.c'), '-o', str(exe),
    ], check=True)
    subprocess.run([str(exe.resolve())], check=True)
