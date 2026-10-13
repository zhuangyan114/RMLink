"""Receive credentials through stdin and configure USB, with sanitized status output."""
import json
import socket
import sys
import time
from rmlink_usb import UsbClient
from rmlink_tool import provision_credentials, verify

data = json.loads(sys.stdin.buffer.read(4096).decode('utf-8-sig'))
with UsbClient() as client:
    result = provision_credentials(client, data['ssid'], data['password'])
    data.clear()
    begin = time.monotonic()
    while time.monotonic() - begin < 40:
        response = client.exchange(b'\x80\x04')
        if len(response) != 10 or response[:3] != b'\x80\x04\x00': raise RuntimeError('Invalid network status response')
        if response[4]:
            result.update(connected=True, ip=socket.inet_ntoa(response[6:10]))
            break
        time.sleep(0.5)
    else:
        result.update(connected=False)
    result['usbAfterProvisioning'] = verify(client)
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result['connected']: sys.exit(2)
