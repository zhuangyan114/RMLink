"""Synchronous local client, also usable by the existing SWD verification helpers."""
import json
import urllib.request
import time

from .state import State


class ApiClient:
    def __init__(self, directory=None, port=3243):
        for attempt in range(25):
            try:
                self.token = State(directory, create=False).data['token']
                break
            except FileNotFoundError:
                if attempt == 24: raise
                time.sleep(.2)
        self.port, self.session = port, None

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=data, method=method,
                 headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        # Bypass HTTP proxy environment variables for this local-only endpoint.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as reply:
            return json.load(reply)

    def open(self, device_id):
        result = self.request('POST', '/v1/sessions', {'device_id': device_id})
        self.session = result['session_id']
        self.packet_size = result.get('packet_size', 64)
        return self

    def exchange(self, request): return self.exchange_many([request])[0]
    def exchange_many(self, requests):
        result = self.request('POST', f'/v1/sessions/{self.session}/dap', {'packets': [bytes(r).hex() for r in requests]})
        return [bytes.fromhex(r) for r in result['responses']]
    def abort_transfer(self): return self.request('POST', f'/v1/sessions/{self.session}/abort')
    def heartbeat(self): return self.request('POST', f'/v1/sessions/{self.session}/heartbeat')
    def close(self):
        if self.session:
            self.request('DELETE', f'/v1/sessions/{self.session}'); self.session = None
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
