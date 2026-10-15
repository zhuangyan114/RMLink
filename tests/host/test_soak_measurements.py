import unittest
import struct
from verify_subscription_soak import Measurements
from inspect_fixture_recording import Audit


class SoakMeasurementsTests(unittest.TestCase):
    def sample(self,sequence=1,**changes):
        return dict(kind=1,sequence=sequence,timestamp_us=sequence*1000,flags=0,
                    valid_bitmap=1023,values=[i<<24 for i in range(10)],**changes)

    def test_gap_and_budget_are_reported_without_inventing_frames(self):
        m=Measurements();m.accept(1,self.sample(1));s=self.sample(4);s['flags']=2;m.accept(2,s)
        result=m.summary()['kinds']['1']
        self.assertEqual(result['frames'],2);self.assertEqual(result['gaps'],2)
        self.assertEqual(result['budgetFlags'],1);self.assertAlmostEqual(result['effectiveHz'],1000/3)

    def test_bad_field_and_repeated_sequence_fail(self):
        for mode in ('identity','bitmap','partial','repeat'):
            with self.subTest(mode=mode):
                m=Measurements();m.accept(1,self.sample());s=self.sample(2)
                if mode=='identity':s['values'][4]=0
                if mode=='bitmap':s['valid_bitmap']=1022
                if mode=='partial':s['flags']=1
                if mode=='repeat':s['sequence']=1
                with self.assertRaises(AssertionError):m.accept(2,s)

    def test_rtt_fragmented_fixture_record_checksum(self):
        m=Measurements();data=struct.pack('<IIII',0x524d5254,5,99,0x524d5254^5^99)
        for sequence,offset in enumerate((0,7)):
            m.accept(sequence,dict(kind=3,sequence=sequence,timestamp_us=1000*sequence,
                flags=0,byte_sequence=offset,data=data[offset:7 if not sequence else 16]))
        self.assertEqual(m.rtt_records,1);self.assertFalse(m.rtt_pending)
        with self.assertRaises(AssertionError):m.accept(2,dict(kind=3,sequence=2,timestamp_us=2000,
                flags=0,byte_sequence=17,data=b'bad'))

    def test_memory_use_bounded_for_long_run(self):
        m=Measurements()
        for i in range(20000):m.accept(i,self.sample(i))
        self.assertEqual(len(m.recent_intervals),8192)
        self.assertEqual(m.summary()['kinds']['1']['frames'],20000)

    def test_rtt_duplicate_payload_is_rejected_even_with_new_byte_sequence(self):
        m=Measurements();magic=0x524d5254;offset=0
        for sequence,producer in enumerate((5,8,8)):
            data=struct.pack('<IIII',magic,producer,producer,magic)
            sample=dict(kind=3,sequence=sequence,timestamp_us=sequence*1000,
                        flags=0,byte_sequence=offset,data=data)
            if sequence==2:
                with self.assertRaisesRegex(AssertionError,'repeated/reordered'):m.accept(sequence,sample)
            else:m.accept(sequence,sample)
            offset+=16
        self.assertEqual(m.summary()['rttProducerSkipped'],2)

    def test_rtt_producer_sequence_wrap_is_valid(self):
        m=Measurements();magic=0x524d5254
        for sequence,producer in enumerate((0xffffffff,0)):
            m.accept(sequence,dict(kind=3,sequence=sequence,timestamp_us=sequence*1000,
                flags=0,byte_sequence=sequence*16,data=struct.pack('<IIII',magic,producer,producer,magic)))
        self.assertEqual(m.summary()['rttProducerSkipped'],0)

    def test_dynamic_audit_accepts_repeated_samples_and_counter_wrap(self):
        audit=Audit()
        for sequence,counter in enumerate((0xffffff,0xffffff,0)):
            sample=self.sample(sequence);sample['values']=[(i<<24)|counter for i in range(10)]
            audit.accept(sequence,sample)
        audit.require_dynamic_timeline()
        self.assertEqual(audit.field_changes['1'],[1]*10)

    def test_dynamic_audit_rejects_one_frozen_leaf_with_valid_identity(self):
        audit=Audit()
        for sequence in range(3):
            sample=self.sample(sequence)
            sample['values']=[(i<<24)|(0 if i==4 else sequence) for i in range(10)]
            audit.accept(sequence,sample)
        with self.assertRaisesRegex(AssertionError,'never changed'):audit.require_dynamic_timeline()


if __name__=='__main__':unittest.main()
