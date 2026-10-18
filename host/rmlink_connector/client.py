"""Synchronous local client, also usable by the existing SWD verification helpers."""
import json
import urllib.request
import urllib.error
import time
import threading
import struct
import hashlib
from .capture_protocol import decode_record

class RMLinkError(RuntimeError):
    def __init__(self,status,result):
        super().__init__(result.get('error',f'RMLink HTTP {status}'))
        self.status=status; self.code=result.get('code','HttpError'); self.details=result

from .state import State


class ApiClient:
    def time_sync(self,device_id,samples=8):
        return self.request('POST',f'/v1/devices/{device_id}/time-sync',{'samples':samples})
    def set_transport(self,device_id,transport):
        return self.request('POST','/v1/transport',{'device_id':device_id,'transport':transport})
    def device_health(self,device_id):return self.request('GET',f'/v1/devices/{device_id}/health')

    def __init__(self, directory=None, port=3243, timeout=30):
        for attempt in range(25):
            try:
                self.token = State(directory, create=False).data['token']
                break
            except FileNotFoundError:
                if attempt == 24: raise
                time.sleep(.2)
        self.port, self.session = port, None
        self.timeout = timeout
        self._stop=threading.Event(); self._heartbeat=None

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=data, method=method,
                 headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        # Bypass HTTP proxy environment variables for this local-only endpoint.
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=180 if path.endswith('/flash') else self.timeout) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as error:
            result=json.load(error)
            raise RMLinkError(error.code,result) from error

    def open(self, device_id):
        if self.session:raise RuntimeError('RMLink session already open')
        result = self.request('POST', '/v1/sessions', {'device_id': device_id})
        self.session = result['session_id']
        self.packet_size = result.get('packet_size', 64)
        self._stop=stop=threading.Event();session=self.session
        def heartbeat_loop():
            while not stop.wait(5):
                try:self.request('POST',f'/v1/sessions/{session}/heartbeat')
                except Exception:stop.set()
        self._heartbeat=threading.Thread(target=heartbeat_loop,daemon=True)
        self._heartbeat.start()
        return self

    def target(self, command, **parameters):
        return self.request('POST', f'/v1/sessions/{self.session}/target', {'command':command,**parameters})
    def flash_image(self,data,address=0x08000000,backend='auto',verify_on_host=False):
        data=bytes(data);path=f'/v1/sessions/{self.session}/flash'
        self.request('POST',path,{'action':'begin','address':address,'byte_length':len(data),'sha256':hashlib.sha256(data).hexdigest(),'backend':backend,'verify_on_host':verify_on_host})
        for offset in range(0,len(data),4096):self.request('POST',path,{'action':'chunk','offset':offset,'data':data[offset:offset+4096].hex()})
        return self.request('POST',path,{'action':'commit'})
    def flash_status(self):
        if not self.session:raise RuntimeError('RMLink session closed')
        return self.request('GET',f'/v1/sessions/{self.session}/flash')
    def cancel_flash(self):
        if not self.session:raise RuntimeError('RMLink session closed')
        return self.request('POST',f'/v1/sessions/{self.session}/flash',{'action':'cancel'})
    def subscribe(self, kind, **parameters):
        result=self.request('POST',f'/v1/sessions/{self.session}/subscriptions',{'kind':kind,**parameters})
        if not hasattr(self,'_stream_cursor'):self._stream_cursor=result.get('stream_cursor',-1)
        return result
    def subscription(self, sid, action='status'):
        return self.request('GET' if action=='status' else 'POST',f'/v1/sessions/{self.session}/subscriptions/{sid}',None if action=='status' else {'action':action})
    def start_recording(self,**options):return self.request('POST',f'/v1/sessions/{self.session}/recordings',options)
    def recording(self,recording_id,stop=False):
        return self.request('POST' if stop else 'GET',f'/v1/sessions/{self.session}/recordings/{recording_id}',{'action':'stop'} if stop else None)
    def unsubscribe(self,sid): return self.request('DELETE',f'/v1/sessions/{self.session}/subscriptions/{sid}')
    def events(self,after=-1,limit=128,wait_ms=1000):
        result=self.request('POST',f'/v1/sessions/{self.session}/events',{'after':after,'limit':limit,'wait_ms':wait_ms})
        for row in result['records']: row['sample']=decode_record(bytes.fromhex(row['frame']))
        return result
    def stream(self,after=None):
        if after is None:after=getattr(self,'_stream_cursor',-1)
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}/v1/sessions/{self.session}/stream?after={after}',headers={'Authorization':'Bearer '+self.token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=30) as reply:
            def exact(n):
                data=bytearray()
                while len(data)<n:
                    part=reply.read(n-len(data))
                    if not part: raise ConnectionError('Capture stream ended')
                    data.extend(part)
                return bytes(data)
            while not self._stop.is_set():
                cursor,size=struct.unpack('<QI',exact(12))
                if size==0: continue
                if not 56<=size<=384: raise ValueError('Local capture frame bound')
                self._stream_cursor=cursor
                yield {'cursor':cursor,**decode_record(exact(size))}

    def exchange(self, request): return self.exchange_many([request])[0]
    def exchange_many(self, requests):
        result = self.request('POST', f'/v1/sessions/{self.session}/dap', {'packets': [bytes(r).hex() for r in requests]})
        return [bytes.fromhex(r) for r in result['responses']]
    def abort_transfer(self): return self.request('POST', f'/v1/sessions/{self.session}/abort')
    def heartbeat(self): return self.request('POST', f'/v1/sessions/{self.session}/heartbeat')
    def close(self):
        self._stop.set()
        if hasattr(self,'_stream_cursor'):del self._stream_cursor
        if self._heartbeat: self._heartbeat.join(timeout=1)
        if self.session:
            session=self.session;self.session=None
            self.request('DELETE',f'/v1/sessions/{session}')
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_value, traceback):
        try:self.close()
        except Exception:
            if exc_type is None:raise
