import struct
import unittest
from rmlink_connector.diagnostics import decode_health,decode_clock_header,summarize_clock


class DiagnosticsTests(unittest.TestCase):
    def test_exact_usb64_health_units_and_failure_cause(self):
        packet=b'\x80\x0a\0\x01'+struct.pack('<6IIQIQIH6B',100000,60000,20000,8000000,7900000,7800000,
            4000000,9999999999,3,8888888888,2500,7,16,9,8,2,0,0)
        self.assertEqual(len(packet),64);result=decode_health(packet)
        self.assertEqual(result['memory']['internal']['largest_block_bytes'],60000)
        self.assertEqual(result['snapshot_uptime_us'],9999999999)
        self.assertEqual(result['last_capture_io_failure']['abort_reason'],'wall_clock_deadline')
        self.assertEqual(result['last_capture_io_failure']['completed_operations'],9)
        self.assertFalse(result['last_capture_io_failure']['target_write'])
        self.assertEqual(decode_health(b'\x80\x0a\x02'),{'supported':False})
        with self.assertRaises(ValueError):decode_health(packet[:-1])
    def test_raw_rmt_ticks_keep_idle_but_do_not_treat_it_as_clock_period(self):
        header=decode_clock_header(struct.pack('<BBBBIIIIIBB',1,1,10,0,7,80000000,4000000,10,0x1ba01477,0,1))
        words=[1000|1<<15|1000<<16]+[10|1<<15|10<<16]*8+[1000|1<<15]
        result=summarize_clock(header,words)
        self.assertTrue(result['measurement_valid']);self.assertEqual(result['median_clock_hz'],4000000)
        self.assertEqual(result['complete_cycles'],8);self.assertEqual(result['raw_symbols'],words)
        self.assertFalse(summarize_clock(header,words[:3])['measurement_valid'])
        with self.assertRaises(ValueError):decode_clock_header(bytes(26))
