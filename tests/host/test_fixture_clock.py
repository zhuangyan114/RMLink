import struct
import unittest
from fixture_clock import require_fixture_72mhz,discard_pretest_rtt


class ClockTests(unittest.TestCase):
    def test_pretest_rtt_discard_is_explicit_and_unknown_write_is_not_replayed(self):
        class Client:
            writes=0;fail=False
            def target(self,command,**args):
                if command=='read_memory':return {'data':(b'SEGGER RTT'+bytes(6)+struct.pack('<8I',1,0,0,0x20000600,1024,16,1008,0)).hex()}
                self.writes+=1
                if self.fail:raise ConnectionError('Unknown write completion')
                self.data=args['data']
        c=Client();self.assertEqual(discard_pretest_rtt(c)['discardedPretestBytes'],32)
        self.assertEqual(c.data,struct.pack('<I',16).hex());c.fail=True
        with self.assertRaises(ConnectionError):discard_pretest_rtt(c)
        self.assertEqual(c.writes,2)
    def test_performance_baseline_rejects_hsi_fallback_and_wrong_dividers(self):
        class Client:
            cr=0x03038b83;cfgr=0x001d840a
            def target(self,*args,**kwargs):return {'data':struct.pack('<3I',self.cr,self.cfgr,0).hex()}
        c=Client();self.assertEqual(require_fixture_72mhz(c)['pclk1Hz'],36000000)
        for cfg in (0,0x001d800a,0x001d8408):
            c.cfgr=cfg
            with self.assertRaises(AssertionError):require_fixture_72mhz(c)
        c.cfgr=0x001d840a;c.cr=0x008b83
        with self.assertRaises(AssertionError):require_fixture_72mhz(c)
