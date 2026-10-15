import unittest
import struct
import contextlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import verify_subscription_soak as soak
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

    def run_loss_case(self,corrupt_recording=False):
        class Client:
            now=0;next_id=0;unsubscribed=[];recording_stopped=False
            def open(self,device_id):return self
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def exchange(self,request):return b'\x80\x04\x00\x00\x01\xe0\x00\x00\x00\x00'
            def target(self,action,**options):
                if action=='read_memory':return {'data':'4b4c4d52'}
                return {'state':'Running'}
            def subscribe(self,*args,**options):
                self.next_id+=1;return {'subscription_id':self.next_id,'stream_cursor':-1}
            def start_recording(self,**options):return {'recording_id':'test'}
            def events(self,*args):
                self.now+=.5;return {'cursor':0,'consumer_gap':0,'error':None,'records':[]}
            def subscription(self,sid):
                return {'state':2,'failures':0,'ring_dropped':int(sid==1 and self.now>=2)}
            def recording(self,rid,stop=False):
                self.recording_stopped=stop
                return {'phase':'complete','finalized':True,'path':'fixture.rmlrec','sha256':'expected'}
            def unsubscribe(self,sid):self.unsubscribed.append(sid)
        client=Client()
        def inspect(path,accept):
            if corrupt_recording:raise ValueError('Recording CRC mismatch')
            return {'sha256':'expected','frames':0}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'loss.json'
            with patch.object(soak,'ApiClient',return_value=client), \
                 patch.object(soak.time,'perf_counter',side_effect=lambda:client.now), \
                 patch.object(soak,'inspect_recording',side_effect=inspect), \
                 patch.object(soak.sys,'argv',['soak','--seconds','10','--output',str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError,'Probe data loss'):soak.main()
            result=json.loads(output.read_text())
        self.assertFalse(result['complete']);self.assertLess(result['elapsedSeconds'],10)
        self.assertEqual(result['statuses']['1']['ring_dropped'],1)
        self.assertTrue(client.recording_stopped);self.assertEqual(client.unsubscribed,[3,2,1])
        self.assertEqual(result['consumerGap'],0);self.assertIn('live',result)
        return result

    def test_board_loss_aborts_early_and_keeps_failed_recording_measurements(self):
        result=self.run_loss_case()
        self.assertEqual(result['independentFileValidation']['sha256'],'expected')
        self.assertIn('disk',result);self.assertNotIn('cleanupError',result)

    def test_bad_failed_recording_does_not_mask_the_original_loss(self):
        result=self.run_loss_case(corrupt_recording=True)
        self.assertIn('Probe data loss',result['failure'])
        self.assertEqual(result['failedRecordingValidationError'],'Recording CRC mismatch')


if __name__=='__main__':unittest.main()
