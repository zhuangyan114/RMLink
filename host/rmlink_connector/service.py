"""Discovery, paired-device connection, local Orbit API and virtual USB lifecycle."""
import asyncio
import contextlib
import hmac
import json
import time
import uuid

from . import __version__
from .broker import Broker, PRIORITIES
from .discovery import Device, discover
from .driver import Driver
from .state import State
from .transport import TcpTransport
from .usbip import UsbIpServer
from rmlink_protocol import MAX_PACKET_SIZE, PACKET_COUNT


class Connector:
    def __init__(self, state=None):
        self.state = state or State()
        self.devices, self.brokers, self.sessions = {}, {}, {}
        self.driver = Driver()
        self.usbip = UsbIpServer(self.brokers)
        self.api = None
        self.usbip_port = None
        self.stopping = asyncio.Event()
        self.background = None
        self.scan_error = None
        self.mount_errors = {}
        self.connection_errors = {}
        self.refresh_lock = asyncio.Lock()

    async def refresh(self):
        async with self.refresh_lock: await self._refresh()

    async def _refresh(self):
        # Do not open a second physical TCP session for a connected probe.
        existing = {(d.address, d.port): d for key, d in self.devices.items()
                    if key in self.brokers and not self.brokers[key].failed}
        found = await discover(existing=existing)
        self.devices.update(found)
        for key, saved in list(self.state.data['paired'].items()):
            if key not in self.devices:
                self.devices[key] = Device(**{k: saved[k] for k in Device.__dataclass_fields__})
            broker = self.brokers.get(key)
            if broker and not broker.failed:
                try: await broker.submit('connection-health', [b'\x80\x09'], 'background')
                except (OSError, TimeoutError, ConnectionError): pass
            if broker and broker.failed:
                await broker.close(); self.brokers.pop(key, None)
                for sid, session in list(self.sessions.items()):
                    if session['device_id'] == key: session['lost'] = True
                # A lost active session is terminal. Reconnection never replays writes.
                if key in self.driver.ports:
                    with contextlib.suppress(Exception): await self.driver.detach(key)
                broker = None
            if broker is None:
                device = self.devices[key]
                fresh = Broker(TcpTransport(device.address, device.port), key)
                try:
                    await fresh.start()
                    replies = await fresh.submit('identity', [b'\x80\x09'], 'background')
                    if replies[0] != b'\x80\x09\x00' + bytes.fromhex(key):
                        raise ConnectionError('Pinned device identity mismatch')
                    self.brokers[key] = fresh
                    self.connection_errors.pop(key, None)
                except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
                    self.connection_errors[key] = f'{type(exc).__name__}: {exc}'
                    await fresh.close(); continue
            if saved.get('auto_mount', False) and self.driver.exe.exists() and key not in self.driver.ports:
                try:
                    await self.driver.attach(key, self.usbip_port); self.mount_errors.pop(key, None)
                except Exception as exc: self.mount_errors[key] = str(exc)

    async def upkeep(self):
        while not self.stopping.is_set():
            try: await self.refresh(); self.scan_error = None
            except Exception as exc: self.scan_error = f'{type(exc).__name__}: {exc}'
            for sid, session in list(self.sessions.items()):
                if time.monotonic() - session['seen'] > 30:
                    broker = self.brokers.get(session['device_id'])
                    if broker: await broker.release(sid)
                    self.sessions.pop(sid, None)
            try: await asyncio.wait_for(self.stopping.wait(), 5)
            except TimeoutError: pass

    async def start(self, api_port=3243, usbip_port=3240):
        self.usbip_port = await self.usbip.start(usbip_port)
        self.api = await asyncio.start_server(self.handle_http, '127.0.0.1', api_port, limit=16384)
        self.background = asyncio.create_task(self.upkeep())
        return self.api.sockets[0].getsockname()[1]

    def snapshot(self):
        return {'version': __version__, 'usbip_port': self.usbip_port, 'driver': self.driver.status(),
                'discovery_error': self.scan_error, 'mount_errors': self.mount_errors,
                'connection_errors': self.connection_errors,
                'devices': [dict(d.as_dict(), paired=key in self.state.data['paired'],
                            connected=key in self.brokers and not self.brokers[key].failed,
                            packet_size=self.brokers[key].packet_size if key in self.brokers else None,
                            owner=self.brokers[key].owner if key in self.brokers else None)
                            for key, d in self.devices.items()]}

    async def route(self, method, path, body):
        if method == 'GET' and path == '/v1/status': return self.snapshot()
        if method == 'GET' and path == '/v1/devices': return self.snapshot()['devices']
        if method == 'POST' and path == '/v1/pair':
            key = body.get('device_id')
            if key not in self.devices: raise ValueError('Choose a discovered device_id')
            self.state.data['paired'][key] = self.devices[key].as_dict()
            self.state.save(); await self.refresh()
            return {'paired': key}
        if method == 'POST' and path == '/v1/mount':
            key = body.get('device_id')
            if key not in self.brokers: raise ValueError('Pair and connect the discovered device first')
            port = await self.driver.attach(key, self.usbip_port)
            self.state.data['paired'][key]['auto_mount'] = True; self.state.save()
            return {'device_id': key, 'virtual_port': port}
        if method == 'POST' and path == '/v1/unmount':
            key = body.get('device_id')
            if key not in self.state.data['paired']: raise ValueError('Unknown paired device')
            self.state.data['paired'][key]['auto_mount'] = False; self.state.save()
            await self.driver.detach(key)
            return {'unmounted': key}
        if method == 'POST' and path == '/v1/sessions':
            key = body.get('device_id')
            if key not in self.brokers or self.brokers[key].failed: raise ConnectionError('Device offline')
            sid = 'api-' + uuid.uuid4().hex
            self.sessions[sid] = {'device_id': key, 'seen': time.monotonic(), 'lost': False}
            return {'session_id': sid, 'heartbeat_seconds': 5, 'expiry_seconds': 30,
                    'packet_size': self.brokers[key].packet_size}
        parts = path.split('/')
        if len(parts) in (4, 5) and parts[1:3] == ['v1', 'sessions']:
            sid = parts[3]
            session = self.sessions.get(sid)
            if not session or session['lost']: raise ConnectionError('Session closed/lost; acquire a new session')
            broker = self.brokers[session['device_id']]
            session['seen'] = time.monotonic()
            if method == 'DELETE' and len(parts) == 4:
                await broker.release(sid); self.sessions.pop(sid, None); return {'closed': True}
            if method == 'POST' and len(parts) == 5 and parts[4] == 'heartbeat': return {'alive': True}
            if method == 'POST' and len(parts) == 5 and parts[4] == 'abort': return {'accepted': await broker.abort(sid)}
            if method == 'POST' and len(parts) == 5 and parts[4] == 'dap':
                values = body.get('packets')
                if not isinstance(values, list) or not 1 <= len(values) <= 8 or any(not isinstance(v, str) or len(v) > broker.packet_size*2 for v in values):
                    raise ValueError('packets must be 1..8 hex strings within negotiated capacity')
                responses = await broker.submit(sid, [bytes.fromhex(v) for v in values], body.get('priority', 'control'))
                return {'responses': [r.hex() for r in responses]}
        if method == 'POST' and path == '/v1/shutdown': self.stopping.set(); return {'stopping': True}
        raise ValueError('Unsupported local API route')

    async def handle_http(self, reader, writer):
        code, response = 500, {'error': 'request_failed'}
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3)
            if len(header) > 16384: raise ValueError('HTTP header limit')
            lines = header.decode('ascii').split('\r\n')
            method, path, version = lines[0].split(' ')
            headers = {}
            for line in lines[1:]:
                if not line: continue
                name, value = line.split(':', 1)
                name = name.strip().lower()
                if name in headers: raise ValueError('Duplicate HTTP header')
                headers[name] = value.strip()
            if 'origin' in headers or 'transfer-encoding' in headers:
                code, response = 403, {'error': 'browser_or_chunked_request_denied'}
            elif not hmac.compare_digest(headers.get('authorization', ''), 'Bearer ' + self.state.data['token']):
                code, response = 401, {'error': 'authentication_required'}
            else:
                length = int(headers.get('content-length', 0))
                if not 0 <= length <= MAX_PACKET_SIZE * PACKET_COUNT * 2 + 1024: raise ValueError('HTTP body limit')
                data = await asyncio.wait_for(reader.readexactly(length), 3)
                body = json.loads(data) if data else {}
                if not isinstance(body, dict): raise ValueError('Expected JSON object')
                response = await self.route(method, path, body); code = 200
        except (ValueError, UnicodeError, json.JSONDecodeError, asyncio.LimitOverrunError):
            code, response = 400, {'error': 'invalid_request'}
        except asyncio.QueueFull: code, response = 429, {'error': 'request_queue_full'}
        except (OSError, ConnectionError, TimeoutError) as exc:
            code, response = 503, {'error': str(exc)[:400], 'write_completion_may_be_unknown': True}
        except asyncio.IncompleteReadError: pass
        except Exception as exc: code, response = 500, {'error': type(exc).__name__}
        finally:
            body = json.dumps(response, ensure_ascii=True).encode()
            writer.write(f'HTTP/1.1 {code} Result\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n'.encode() + body)
            with contextlib.suppress(OSError, TimeoutError): await asyncio.wait_for(writer.drain(), 3)
            writer.close()
            with contextlib.suppress(OSError): await writer.wait_closed()

    async def close(self):
        self.stopping.set()
        if self.background: self.background.cancel(); await asyncio.gather(self.background, return_exceptions=True)
        for key in list(self.driver.ports):
            with contextlib.suppress(Exception): await self.driver.detach(key)
        await self.usbip.close()
        for broker in self.brokers.values(): await broker.close()
        if self.api: self.api.close(); await self.api.wait_closed()
