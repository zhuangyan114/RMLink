"""Streaming validation of the public RMLR v1 recording container."""
import hashlib
import json
import struct
from .capture_protocol import decode_record


def inspect_recording(path,on_frame=None):
    digest=hashlib.sha256();count=0
    with open(path,'rb') as source:
        def exact(n):
            data=source.read(n)
            if len(data)!=n:raise ValueError('Recording truncated or missing footer')
            digest.update(data);return data
        if exact(4)!=b'RMLR':raise ValueError('Recording magic')
        size=struct.unpack('<I',exact(4))[0]
        if not 1<=size<=65536:raise ValueError('Recording metadata bound')
        metadata=json.loads(exact(size))
        if metadata.get('version')!=1:raise ValueError('Recording version')
        while True:
            cursor,size=struct.unpack('<QI',exact(12))
            if size==0:
                length=struct.unpack('<I',exact(4))[0]
                if not 1<=length<=65536:raise ValueError('Recording footer bound')
                footer=json.loads(exact(length))
                if source.read(1):raise ValueError('Trailing recording data')
                if footer['frames']!=count:raise ValueError('Recording count mismatch')
                return {'metadata':metadata,'summary':footer,'frames':count,'sha256':digest.hexdigest()}
            if not 56<=size<=384:raise ValueError('Recording frame bound')
            frame=exact(size);sample=decode_record(frame);count+=1
            if on_frame:on_frame(cursor,sample)
