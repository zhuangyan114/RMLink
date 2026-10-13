"""Control the installed USB/IP client; all process arguments are fixed/validated.

The driver handles USB/IP I/O. RMLink wraps its public CLI in the first prototype;
there is no driver/installer code copied into this repository.
"""
import asyncio
import os
from pathlib import Path
import re


class Driver:
    def __init__(self):
        self.exe = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'USBip' / 'usbip.exe'
        self.ports = {}

    def status(self): return {'installed_cli': self.exe.exists(), 'managed_ports': dict(self.ports)}

    async def run(self, *args):
        if not self.exe.exists(): raise FileNotFoundError('Install the reviewed USBip virtual USB driver package first')
        process = await asyncio.create_subprocess_exec(str(self.exe), *args,
                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                  creationflags=0x08000000 if os.name == 'nt' else 0)
        try: output, _ = await asyncio.wait_for(process.communicate(), 20)
        except TimeoutError:
            process.kill(); await process.communicate()
            raise TimeoutError('Virtual USB attach timed out; inspect driver ports before retry')
        text = output.decode('utf-8', errors='replace')
        if process.returncode: raise RuntimeError(f'USBip control failed ({process.returncode}): {text.strip()[:400]}')
        return text

    async def attach(self, device_id, port):
        if not re.fullmatch('[0-9a-f]{12}', device_id) or not 1 <= port <= 65535: raise ValueError('Invalid device/port')
        if device_id in self.ports: return self.ports[device_id]
        # --once avoids driver-owned auto-reconnect competing with the service.
        text = await self.run(f'--tcp-port={port}', 'attach', '--remote=127.0.0.1',
                              f'--bus-id=rmlink-{device_id}', '--once', '--terse')
        if not re.fullmatch(r'\s*\d+\s*', text): raise RuntimeError('Unknown USBip attach output; do not repeat attach')
        imported_port = int(text)
        self.ports[device_id] = imported_port
        return imported_port

    async def detach(self, device_id):
        port = self.ports.get(device_id)
        if port is not None:
            try:
                await self.run('detach', f'--port={port}')
            except RuntimeError:
                # A closed USB/IP stream may already have unplugged this port.
                # Verify absence before clearing our mapping; retain other devices.
                actual = await self.run('port')
                if re.search(rf'^Port\s+0*{port}:', actual, re.MULTILINE): raise
            self.ports.pop(device_id, None)
