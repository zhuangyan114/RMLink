import hashlib
import struct


def application_image(payload=b'firmware-test'*100, flags=0, version='0.3.1'):
    header=bytearray(24);header[0]=0xe9;header[1]=1;header[12]=9;header[23]=1
    app=bytearray(256);struct.pack_into('<I',app,0,0xabcd5432)
    app[16:16+len(version)]=version.encode();app[48:55]=b'rmlink\0'
    app[144:176]=hashlib.sha256(payload).digest()
    metadata=struct.pack('<8sIIIiii32s',b'RMLKOTA1',1,1,flags,4,5,6,bytes(32))
    segment=app+metadata+payload
    data=header+struct.pack('<II',0x3c000020,len(segment))+segment
    checksum=0xef
    for value in segment:checksum^=value
    data+=bytes((15-len(data)%16)%16)+bytes([checksum])
    return bytes(data)+hashlib.sha256(data).digest()


def device_info(profile, elf=bytes(32), state=0, boot_state=2):
    reply=bytearray(63);reply[:6]=bytes([0x83,0,0,1,3,state])
    struct.pack_into('<III',reply,6,3*1024*1024,0,0)
    reply[18:21]=bytes([0,3,0]);reply[21:53]=elf;reply[53:61]=bytes.fromhex(profile)
    reply[61]=boot_state;reply[62]=16
    return bytes(reply)
