import struct
import unittest
from benchmark_mixed_subscriptions import mixed_layout


class MixedLayoutTests(unittest.TestCase):
    def test_five_exactly_three_dereferences_and_five_direct_same_dynamic_leaves(self):
        for shared in (False,True):
            image,fields=mixed_layout(shared)
            self.assertEqual(len(fields),10)
            self.assertEqual([len(f['offsets']) for f in fields],[3]*5+[0]*5)
            for i,field in enumerate(fields):
                address=field['address']
                for offset in field['offsets']:
                    address=struct.unpack_from('<I',image,address-0x20001000)[0]+offset
                self.assertEqual(address,0x20000100+i*64)
