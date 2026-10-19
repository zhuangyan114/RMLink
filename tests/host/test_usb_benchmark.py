import struct
import unittest
from unittest.mock import Mock,call,patch
from benchmark_usb_stream import expected_pattern,idle_connector


class UsbBenchmarkTests(unittest.TestCase):
    def test_raw_benchmark_restores_connector_after_read_failure(self):
        api=Mock();api.request.return_value={'devices':[dict(device_id='abc',paired=True,transport='usb',owner=None,mounted=False)]}
        with patch('benchmark_usb_stream.Path.is_file',return_value=True),patch('benchmark_usb_stream.ApiClient',return_value=api):
            with self.assertRaisesRegex(ValueError,'read failed'):
                with idle_connector('ABC') as changed:
                    self.assertTrue(changed)
                    self.assertEqual(api.set_transport.call_args_list,[call('abc','wifi')])
                    raise ValueError('read failed')
        self.assertEqual(api.set_transport.call_args_list,[call('abc','wifi'),call('abc','usb')])

    def test_raw_benchmark_does_not_interrupt_an_active_owner(self):
        api=Mock();api.request.return_value={'devices':[dict(device_id='abc',paired=True,transport='usb',owner='active',mounted=False)]}
        with patch('benchmark_usb_stream.Path.is_file',return_value=True),patch('benchmark_usb_stream.ApiClient',return_value=api):
            with self.assertRaisesRegex(RuntimeError,'Close target sessions'):
                with idle_connector('abc'):self.fail('Busy target was accepted')
        api.set_transport.assert_not_called()

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
