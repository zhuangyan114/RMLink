import ctypes as C
import struct
import unittest
from unittest.mock import patch
from rmlink_native_usb import SetupPacket,UsbClient


class NativeStreamTests(unittest.TestCase):
    def client(self,partial=0,mode=1):
        client=object.__new__(UsbClient)
        client.handle=None;client.streaming=True;client.stream_ep=0x84
        class Native:
            def WinUsb_ReadPipe(self,handle,ep,buffer,size,count,overlapped):
                C.cast(count,C.POINTER(C.c_uint32)).contents.value=partial
                return 0
        client.usb=Native()
        client._stream_control=lambda *args,**kwargs:struct.pack('<8I',1,mode,0,1,0,0,2,0)
        return client

    def test_idle_timeout_keeps_the_subscription_open(self):
        with patch.object(C,'get_last_error',return_value=121,create=True):
            self.assertEqual(self.client().read_stream(),b'')

    def test_partial_timeout_cannot_silently_discard_delivered_bytes(self):
        with patch.object(C,'get_last_error',return_value=121,create=True):
            with self.assertRaisesRegex(ConnectionError,'Partial USB'):
                self.client(partial=64).read_stream()

    def test_device_stopping_the_stream_is_terminal(self):
        with patch.object(C,'get_last_error',return_value=121,create=True):
            with self.assertRaisesRegex(ConnectionError,'stream stopped, error=2'):
                self.client(mode=0).read_stream()

    def test_setup_packet_matches_usb_wire_layout(self):
        self.assertEqual(bytes(SetupPacket(0xc0,0x22,2,8,8)),b'\xc0\x22\x02\x00\x08\x00\x08\x00')


if __name__=='__main__':unittest.main()
