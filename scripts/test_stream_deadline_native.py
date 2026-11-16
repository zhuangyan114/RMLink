"""Compile and exercise the production stream deadline loop without hardware."""
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
output = root / 'artifacts/tests/stream-deadline'
output.mkdir(parents=True, exist_ok=True)
exe = output / 'deadline_test.exe'
subprocess.run([shutil.which('gcc') or r'C:\mingw64\bin\gcc.exe', '-std=c11', '-Wall', '-Wextra', '-Werror',
                str(root / 'tests/stream/deadline_test.c'), '-o', str(exe)], check=True)
subprocess.run([str(exe)], check=True)
