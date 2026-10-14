"""Public capture wire schema, independent of Orbit and network fragmentation."""
import struct
import zlib

HEADER = struct.Struct('<4sBBBBIIIIHHI')
SAMPLE = struct.Struct('<IQIIHH')
MAX_PAYLOAD = 352
KINDS = {'memory_series': 1, 'memory_snapshot': 2, 'rtt_stream': 3}


def decode_record(frame):
    if len(frame) < HEADER.size: raise ValueError('Capture header truncated')
    magic, version, kind, sid, flags, capture, revision, sequence, size, count, header_len, crc = HEADER.unpack_from(frame)
    if magic != b'RML1' or version != 1 or kind not in (1,2,3) or not 1 <= sid <= 4 or flags or count != 1 or header_len != 32:
        raise ValueError('Capture header invalid')
    if not SAMPLE.size <= size <= MAX_PAYLOAD or len(frame) != 32 + size:
        raise ValueError('Capture payload bound')
    payload = frame[32:]
    if zlib.crc32(payload) != crc: raise ValueError('Capture CRC mismatch')
    seq, timestamp, duration, valid, status, length = SAMPLE.unpack_from(payload)
    if sequence != seq or len(payload) != 24 + length: raise ValueError('Capture sample layout')
    data = payload[24:]
    result = dict(subscription_id=sid, kind=kind, capture_id=capture, revision=revision,
                  sequence=seq, timestamp_us=timestamp, duration_us=duration,
                  valid_bitmap=valid, flags=status)
    if kind == 3:
        if len(data) < 16: raise ValueError('RTT record truncated')
        channel, byte_seq, n = struct.unpack_from('<IQI', data)
        if n != len(data)-16: raise ValueError('RTT length mismatch')
        result.update(channel=channel, byte_sequence=byte_seq, data=data[16:])
    else:
        if len(data)%4 or not 4<=len(data)<=128: raise ValueError('Memory record size')
        if valid & ~((1<<(len(data)//4))-1):raise ValueError('Memory validity bitmap')
        result['values'] = list(struct.unpack('<'+'I'*(len(data)//4),data))
    return result


class FrameDecoder:
    """Small bounded accumulator: tolerate any TCP split, reject excess lengths."""
    def __init__(self): self.pending = bytearray()
    def feed(self, data):
        # Consume incrementally so a large coalesced recv cannot allocate without bound.
        output=[]
        while data:
            take=min(len(data), 384-len(self.pending))
            self.pending.extend(data[:take]); data=data[take:]
            while len(self.pending)>=32:
                size=int.from_bytes(self.pending[20:24],'little')
                if not 24<=size<=MAX_PAYLOAD: raise ValueError('Capture frame length')
                total=32+size
                if len(self.pending)<total: break
                frame=bytes(self.pending[:total]); del self.pending[:total]
                decode_record(frame); output.append(frame)
            if len(self.pending)>=384: raise ValueError('Capture accumulator bound')
        return output
