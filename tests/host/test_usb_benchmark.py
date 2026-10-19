import struct
import unittest
from benchmark_usb_stream import expected_pattern


class UsbBenchmarkTests(unittest.TestCase):
    def test_absolute_sequence_survives_arbitrary_read_boundaries(self):
        full=expected_pattern(0,8192)
        self.assertEqual(full[:4],struct.pack('<I',0))
        self.assertEqual(full[4096:4100],struct.pack('<I',1))
        for cut in (1,3,4,4095,4097,4100,8191):
            self.assertEqual(expected_pattern(0,cut)+expected_pattern(cut,8192-cut),full)
        self.assertEqual(expected_pattern(65535,9),expected_pattern(65535,1)+expected_pattern(65536,8))

    def test_repeating_a_full_psram_page_is_detectable(self):
        self.assertNotEqual(expected_pattern(0,8192),expected_pattern(65536,8192))
        self.assertEqual(expected_pattern(65536,4),struct.pack('<I',16))


if __name__=='__main__':unittest.main()
