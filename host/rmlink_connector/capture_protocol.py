"""Public capture wire schema, independent of Orbit and network fragmentation."""
import struct
import zlib

HEADER = struct.Struct('<4sBBBBIIIIHHI')
SAMPLE = struct.Struct('<IQIIHH')
MAX_PAYLOAD = 352
KINDS = {'memory_series': 1, 'memory_snapshot': 2, 'rtt_stream': 3}
COMPACT_HEADER = struct.Struct('<4sBBHIII')


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


class CompactFrameDecoder:
    """Negotiated RMS2/RMS3 groups, expanded to the unchanged RML1 public API.

    RMS3 additionally restores lossless XOR/zero runs. Every sample retains its original payload,
    absolute sequence/timestamp and CRC, including gaps and partial flags.
    """
    def __init__(self,delta=False): self.pending=bytearray();self.delta=delta

    def feed(self,data):
        output=[]
        while data:
            take=min(len(data),4116-len(self.pending))
            self.pending.extend(data[:take]);data=data[take:]
            while len(self.pending)>=COMPACT_HEADER.size:
                magic,kind,sid,count,capture,revision,size=COMPACT_HEADER.unpack_from(self.pending)
                is_delta=magic==b'RMC3' and self.delta
                if (magic!=b'RMC2' and not is_delta) or kind not in (1,2,3) or not 1<=sid<=4 or not 1<=count<=73 or not (9 if is_delta else 28)*count<=size<=4096:
                    raise ValueError('Compact capture header invalid')
                total=COMPACT_HEADER.size+size
                if len(self.pending)<total:break
                frames=[];position=COMPACT_HEADER.size;previous=b''
                for _ in range(count):
                    if is_delta:
                        if position+8>total:raise ValueError('Delta capture sample truncated')
                        crc,length,encoded=struct.unpack_from('<IHH',self.pending,position)
                        stored=encoded or length
                        if not SAMPLE.size<=length<=MAX_PAYLOAD or encoded>=length or position+8+stored>total:
                            raise ValueError('Delta capture sample bound')
                        chunk=self.pending[position+8:position+8+stored];position+=8+stored
                        if encoded:
                            restored=bytearray();offset=0
                            while offset<len(chunk):
                                control=chunk[offset];offset+=1;n=(control&127)+1
                                if len(restored)+n>length:raise ValueError('Delta capture run bound')
                                if control&128:restored.extend(b'\0'*n)
                                else:
                                    if offset+n>len(chunk):raise ValueError('Delta capture literal truncated')
                                    restored.extend(chunk[offset:offset+n]);offset+=n
                            if len(restored)!=length:raise ValueError('Delta capture payload length')
                            payload=bytes(v^(previous[i] if i<len(previous) else 0) for i,v in enumerate(restored))
                        else:payload=bytes(chunk)
                        previous=payload;seq=struct.unpack_from('<I',payload)[0]
                        frame=HEADER.pack(b'RML1',1,kind,sid,0,capture,revision,seq,length,1,32,crc)+payload
                        decode_record(frame);frames.append(frame)
                        continue
                    if position+28>total:raise ValueError('Compact capture sample truncated')
                    crc=struct.unpack_from('<I',self.pending,position)[0]
                    length=24+struct.unpack_from('<H',self.pending,position+26)[0]
                    if length>MAX_PAYLOAD or position+4+length>total:raise ValueError('Compact capture sample bound')
                    payload=bytes(self.pending[position+4:position+4+length])
                    seq=struct.unpack_from('<I',payload)[0]
                    frame=HEADER.pack(b'RML1',1,kind,sid,0,capture,revision,seq,length,1,32,crc)+payload
                    decode_record(frame);frames.append(frame);position+=4+length
                if position!=total:raise ValueError('Compact capture trailing data')
                output.extend(frames);del self.pending[:total]
            if len(self.pending)>=4116:raise ValueError('Compact capture accumulator bound')
        return output
