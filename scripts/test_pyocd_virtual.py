"""Independent pyOCD CMSIS-DAP v2 connection through the virtual probe only."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

# Bound discovery/idle-read problems without leaving a hung Python child behind.
if __name__ == '__main__' and '--child' not in sys.argv:
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--child'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        output, _ = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        output, _ = process.communicate(timeout=5)
        from importlib.metadata import version
        (root / 'artifacts/logs/verify-pyocd-virtual.json').write_text(json.dumps({
            'capturedAt': datetime.now().astimezone().isoformat(), 'complete': False,
            'pyocdVersion': version('pyocd'), 'timeoutSeconds': 20,
            'failure': 'pyOCD test exceeded time limit; inspect the run log for the stage'
        }, indent=2) + '\n', encoding='utf-8')
    (root / 'artifacts/logs/verify-pyocd-virtual-run.log').write_bytes(output)
    print(output.decode('utf-8', errors='replace'))
    sys.exit(process.returncode)

import pyocd
from pyocd.core.helpers import ConnectHelper

ROOT = Path(__file__).resolve().parents[1]
report = dict(capturedAt=datetime.now().astimezone().isoformat(), complete=False, pyocdVersion=pyocd.__version__)
try:
    print('Select virtual CMSIS-DAP RM50787D15A924', flush=True)
    session = ConnectHelper.session_with_chosen_probe(
        blocking=False, unique_id='cmsisdap:RM50787D15A924',
        target_override='cortex_m', frequency=2000000, connect_mode='attach',
        resume_on_disconnect=True)
    assert session is not None, 'Virtual probe not discovered by pyOCD'
    with session:
        target = session.target
        report['probeSerial'] = session.probe.unique_id
        assert report['probeSerial'] == 'RM50787D15A924'
        report['cpuid'] = hex(target.read32(0xe000ed00))
        assert report['cpuid'] == '0x411fc231'
        target.halt()
        target.flush()
        assert target.is_halted()
        report['pcWhileHalted'] = hex(target.read_core_register('pc'))
        target.resume()
        target.flush()
        assert target.is_running()
        report['haltReadRegisterResume'] = True
    report['complete'] = True
except BaseException as exc:
    report['failure'] = f'{type(exc).__name__}: {exc}'
    raise
finally:
    (ROOT / 'artifacts/logs/verify-pyocd-virtual.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
