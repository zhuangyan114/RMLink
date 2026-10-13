"""Bounded mDNS/DNS-SD discovery, then verify the probe's full hardware MAC.

Discovery is not device authentication. This prototype pairs probes explicitly
on the user's trusted hotspot and pins their identity. It sends no Wi-Fi secrets.
"""
import asyncio
from dataclasses import dataclass, asdict
import socket
import struct

from .transport import TcpTransport

SERVICE = '_rmlink._tcp.local'


def dns_name(packet, offset):
    labels, visited = [], set()
    end = None
    for _ in range(64):
        if offset >= len(packet) or offset in visited: raise ValueError('Invalid DNS name')
        visited.add(offset)
        size = packet[offset]; offset += 1
        if size & 0xc0 == 0xc0:
            if offset >= len(packet): raise ValueError('Truncated DNS pointer')
            if end is None: end = offset + 1
            offset = ((size & 0x3f) << 8) | packet[offset]
            continue
        if size & 0xc0 or size > 63: raise ValueError('Invalid DNS label')
        if size == 0: return '.'.join(labels).lower(), end or offset
        if offset + size > len(packet): raise ValueError('Truncated DNS label')
        labels.append(packet[offset:offset + size].decode('utf-8'))
        offset += size
    raise ValueError('Too many DNS labels/pointers')


def parse_dns(packet):
    if not 12 <= len(packet) <= 9000: raise ValueError('DNS packet bounds')
    _, flags, qd, an, ns, ar = struct.unpack_from('>6H', packet)
    if not flags & 0x8000 or qd + an + ns + ar > 128: raise ValueError('DNS response/count')
    offset, records = 12, []
    for _ in range(qd):
        _, offset = dns_name(packet, offset); offset += 4
        if offset > len(packet): raise ValueError('DNS question bounds')
    for _ in range(an + ns + ar):
        name, offset = dns_name(packet, offset)
        if offset + 10 > len(packet): raise ValueError('DNS record header')
        kind, cls, ttl, size = struct.unpack_from('>HHIH', packet, offset); offset += 10
        end = offset + size
        if end > len(packet): raise ValueError('DNS record payload')
        if cls & 0x7fff == 1 and ttl:
            value = None
            if kind == 12: value, name_end = dns_name(packet, offset)
            elif kind == 33:
                if size < 7: raise ValueError('DNS SRV bounds')
                _, _, port = struct.unpack_from('>HHH', packet, offset)
                host, name_end = dns_name(packet, offset + 6); value = (host, port)
            elif kind == 1 and size == 4: value = socket.inet_ntoa(packet[offset:end])
            if kind in (12, 33) and name_end > end: raise ValueError('DNS record name bounds')
            if value is not None: records.append((name, kind, value))
        offset = end
    return records


class MdnsProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue): self.queue = queue
    def datagram_received(self, data, address):
        try:
            records = parse_dns(data)
            self.queue.put_nowait((records, address[0]))
        except (ValueError, UnicodeError, asyncio.QueueFull): pass


@dataclass
class Device:
    device_id: str
    hostname: str
    address: str
    port: int
    firmware: str
    def as_dict(self): return asdict(self)


async def verify_probe(hostname, address, port):
    transport = TcpTransport(address, port)
    try:
        await transport.connect()
        info, identity = await transport.exchange_many([b'\x80\x00', b'\x80\x09'])
        if len(info) != 17 or info[:4] != b'\x80\x00\x00\x01' or int.from_bytes(info[15:17], 'little') != 64:
            raise ValueError('Not a compatible RMLink probe')
        if len(identity) != 9 or identity[:3] != b'\x80\x09\x00':
            raise ValueError('Upgrade RMLink firmware to 0.1.4 for stable device identity')
        return Device(identity[3:].hex(), hostname, address, port, '.'.join(str(n) for n in info[4:7]))
    finally: await transport.close()


async def discover(seconds=2, existing=None):
    queue = asyncio.Queue(maxsize=64)
    query_name = b''.join(bytes([len(label)]) + label.encode() for label in SERVICE.split('.')) + b'\0'
    query = struct.pack('>6H', 0, 0, 1, 0, 0, 0) + query_name + struct.pack('>HH', 12, 0x8001)
    # QU requests direct replies to the ephemeral source port, avoiding taking 5353.
    addresses = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), 0, socket.AF_INET, socket.SOCK_DGRAM)}
    addresses = {ip for ip in addresses if not ip.startswith('127.')}
    transports = []
    for address in addresses:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((address, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setblocking(False)
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: MdnsProtocol(queue), sock=sock)
            transports.append(transport)
        except OSError: sock.close()
    records = []
    try:
        for transport in transports: transport.sendto(query, ('224.0.0.251', 5353))
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                received, sender = await asyncio.wait_for(queue.get(), deadline - asyncio.get_running_loop().time())
                records.extend((name, kind, value, sender) for name, kind, value in received)
            except TimeoutError: break
    finally:
        for transport in transports: transport.close()
    instances = {value for name, kind, value, _ in records if kind == 12 and name == SERVICE}
    candidates = {}
    for name, kind, value, sender in records:
        if kind != 33 or name not in instances: continue
        hostname, port = value
        if not hostname.startswith('rmlink-') or not hostname.endswith('.local') or not 1024 <= port <= 65535: continue
        address = next((ip for n, k, ip, _ in records if k == 1 and n == hostname), sender)
        candidates[(hostname, address, port)] = None
    # Limit parallel verification and total externally supplied candidates.
    known = existing or {}
    results = [known[(addr, port)] for host, addr, port in candidates if (addr, port) in known]
    results.extend(await asyncio.gather(*(verify_probe(*c) for c in list(candidates)[:16]
                   if (c[1], c[2]) not in known), return_exceptions=True))
    return {device.device_id: device for device in results if isinstance(device, Device)}
