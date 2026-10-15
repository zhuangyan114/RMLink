import asyncio
import tempfile
import unittest
from pathlib import Path
from rmlink_connector.recording import Recording
from rmlink_connector.recording_reader import inspect_recording
from test_capture import record


class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.pauses=0
        self.config={1:{'capture_id':7,'addresses':[0x20000100,0x20000104]}}
    async def asyncTearDown(self):self.temp.cleanup()
    async def pause(self):self.pauses+=1
    async def test_raw_frames_footer_and_hash_survive_many_batches(self):
        rec=Recording(self.temp.name,'model',self.config,self.pause)
        await rec.start()
        for start in range(0,3000,100):
            for seq in range(start,start+100):rec.offer(seq,record(seq))
            await asyncio.sleep(.002)
        result=await rec.stop();seen=[]
        parsed=inspect_recording(rec.path,lambda cursor,sample:seen.append((cursor,sample['sequence'])))
        self.assertEqual(result['phase'],'complete');self.assertTrue(result['finalized'])
        self.assertEqual(parsed['sha256'],result['sha256']);self.assertEqual(parsed['frames'],3000)
        self.assertEqual(seen,[(i,i) for i in range(3000)]);self.assertEqual(self.pauses,0)
    async def test_strict_gap_is_failed_and_pauses_the_selected_capture(self):
        rec=Recording(self.temp.name,'model',self.config,self.pause);await rec.start()
        rec.offer(0,record(0));rec.offer(1,record(1));rec.offer(2,record(3))
        result=await rec.stop();parsed=inspect_recording(rec.path)
        self.assertEqual((result['error'],result['frames'],self.pauses),('sequence_gap',2,1))
        self.assertEqual(parsed['summary']['phase'],'failed');self.assertTrue(result['finalized'])
    async def test_queue_overflow_and_disk_failure_cannot_report_success(self):
        rec=Recording(self.temp.name,'model',self.config,self.pause,queue_capacity=2);await rec.start()
        for i in range(3):rec.offer(i,record(i))
        result=await rec.stop();self.assertEqual(result['error'],'recording_queue_overflow')
        self.assertEqual(inspect_recording(rec.path)['frames'],2)
        rec=Recording(self.temp.name,'model',self.config,self.pause);await rec.start()
        def full(data):raise OSError('disk full')
        rec.write=full;rec.offer(0,record(0));result=await rec.stop()
        self.assertFalse(result['finalized']);self.assertIn('storage_error',result['error'])
        with self.assertRaises(ValueError):inspect_recording(rec.path)
    async def test_truncation_and_crc_corruption_are_rejected(self):
        rec=Recording(self.temp.name,'model',self.config,self.pause);await rec.start();rec.offer(0,record(0));await rec.stop()
        data=rec.path.read_bytes()
        for suffix in (1,12,100):
            path=Path(self.temp.name)/f'truncated{suffix}';path.write_bytes(data[:-suffix])
            with self.assertRaises(ValueError):inspect_recording(path)
        payload=bytearray(data);offset=payload.index(b'RML1');payload[offset+56]^=1
        path=Path(self.temp.name)/'corrupt';path.write_bytes(payload)
        with self.assertRaises(ValueError):inspect_recording(path)
