"""Assemble immutable USB_PULL record fragments from one owned executor."""
import struct


class UsbCaptureReader:
    def __init__(self):self.frame_id=1

    async def read(self,exchange):
        frame=bytearray();total=None
        while True:
            reply=await exchange(b'\x81\x10'+struct.pack('<IH',self.frame_id,len(frame)))
            if len(reply)<12 or reply[:3]!=b'\x81\x10\x00':raise ConnectionError('USB capture pull rejected')
            fid,size,offset,chunk=struct.unpack_from('<IHHB',reply,3)
            if fid!=self.frame_id or offset!=len(frame) or len(reply)!=12+chunk or chunk>52:
                raise ConnectionError('USB capture fragment identity or length invalid')
            if size==0:
                if offset or chunk or frame:raise ConnectionError('Empty USB frame during assembly')
                return None
            if not 56<=size<=384 or total is not None and size!=total or chunk==0 or len(frame)+chunk>size:
                raise ConnectionError('USB capture frame range invalid')
            total=size;frame.extend(reply[12:])
            if len(frame)==total:
                self.frame_id=(self.frame_id+1)&0xffffffff or 1
                return bytes(frame)
