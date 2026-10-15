import struct
import tempfile
import unittest
import zlib
from rmlink_connector.recording import Recording
from rmlink_connector.recording_audit import audit_recording
from test_capture import record


class RecordingAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_partial_skipped_slots_and_crc_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            async def failed():raise AssertionError('Non-strict fixture should be retained')
            rec=Recording(directory,'model',{1:{'capture_id':7,'addresses':[0x20000100,0x20000104]}},failed,strict=False)
            await rec.start()
            for cursor,(sequence,flags) in enumerate(((0,0),(1,2),(4,1),(5,3))):
                frame=bytearray(record(sequence));struct.pack_into('<H',frame,52,flags)
                struct.pack_into('<I',frame,28,zlib.crc32(frame[32:]));rec.offer(cursor,frame)
            status=await rec.stop();parsed=audit_recording(rec.path,max_anomalies=1);a=parsed['analysis']
            self.assertEqual(parsed['sha256'],status['sha256'])
            self.assertEqual((a['bad_data_samples'],a['budget_overrun_samples']),(2,2))
            self.assertEqual((a['sequence_gap_events'],a['missed_sequence_slots']),(1,2))
            self.assertTrue(a['anomalies_truncated']);self.assertEqual(len(a['anomalies']),1)
            self.assertEqual(a['anomalies'][0]['sample']['sequence'],4)
            data=bytearray(rec.path.read_bytes());at=data.index(b'RML1');data[at+56]^=1;rec.path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'CRC'):audit_recording(rec.path)
