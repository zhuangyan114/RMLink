import struct
import unittest
from rmlink_connector.usb_capture import UsbCaptureReader
from test_capture import record
from rmlink_connector.capture_protocol import decode_record


class UsbCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_poll_and_multi_fragment_frame_then_next_id(self):
        reader=UsbCaptureReader();data=record(1);calls=[];ready=False
        async def exchange(request):
            fid,offset=struct.unpack('<IH',request[2:]);calls.append((fid,offset))
            chunk=data[offset:offset+52] if ready else b''
            return b'\x81\x10\x00'+struct.pack('<IHHB',fid,len(data) if ready else 0,offset,len(chunk))+chunk
        self.assertIsNone(await reader.read(exchange));self.assertEqual(reader.frame_id,1)
        ready=True;frame=await reader.read(exchange)
        self.assertEqual(decode_record(frame)['sequence'],1)
        self.assertEqual(calls,[(1,0),(1,0),(1,52)])
        self.assertEqual(reader.frame_id,2)
    async def test_short_wrong_id_and_changing_total_are_terminal(self):
        for corruption in ('short','id','total'):
            reader=UsbCaptureReader();count=0;data=record(1)
            async def exchange(request):
                nonlocal count
                fid,offset=struct.unpack('<IH',request[2:]);count+=1
                chunk=data[offset:offset+52]
                response=b'\x81\x10\x00'+struct.pack('<IHHB',fid+(corruption=='id'),len(data)+(corruption=='total' and count==2),offset,len(chunk))+chunk
                return response[:-1] if corruption=='short' else response
            with self.assertRaises(ConnectionError):await reader.read(exchange)
            self.assertEqual(reader.frame_id,1) # no speculative advancement or replay
