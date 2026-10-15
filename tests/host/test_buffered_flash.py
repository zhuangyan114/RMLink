"""Host integration against an independent probe upload/job oracle."""
import asyncio
import hashlib
import math
import struct
import unittest
import zlib
from test_flash_service import AlgorithmTarget,upload
from rmlink_connector.flash import FlashProgrammer,FlashJobError


class ProbeTarget(AlgorithmTarget):
    capture_features=32;source='test-owner'
    def __init__(self,capacity=512):
        super().__init__();self.packet_size=capacity;self.broker=self;self.packet_count=0
        self.commits=0;self.cancel_calls=0;self.chunk_groups=[];self.steps=[];self.operation=0
        self.upload_bad_ack=False;self.lost_commit_response=False;self.swd_failure=False
        self.commit_received=asyncio.Event()
        self.finish_before_cancel=False
        self.out_of_memory=False;self.mass_feature=False
    async def submit(self,source,packets,priority):
        assert source==self.source and priority=='control' and 1<=len(packets)<=8
        self.chunk_groups.append(len(packets));return [await self.packet(p) for p in packets]
    async def packet(self,p,priority='control'):
        assert p[0]==0x82;sub=p[1];reply=bytes([0x82,sub,0])
        if sub==0:return reply+struct.pack('<BBIHHB',1,1,65536,1024,self.packet_size-16,15 if self.mass_feature else 7)
        operation=int.from_bytes(p[2:6],'little')
        if sub==1:
            if self.out_of_memory:return b'\x82\x01\x04'
            self.operation=operation;self.address,self.length=struct.unpack_from('<II',p,6)
            self.expected=p[14:46];self.staged=bytearray();self.steps=[1]
        else:assert operation==self.operation
        if sub==2:
            op,offset,n,crc=struct.unpack_from('<IIHI',p,2);data=p[16:]
            assert len(data)==n and n<=self.packet_size-16 and len(self.staged)==offset and zlib.crc32(data)==crc
            self.staged.extend(data)
            return b'\x82\x02\x05' if self.upload_bad_ack else reply+struct.pack('<I',len(self.staged))
        if sub==3:
            assert len(self.staged)==self.length and hashlib.sha256(self.staged).digest()==self.expected
            self.steps=[2]
        if sub==4:
            assert self.steps==[2];self.commits+=1;self.commit_received.set()
            self.steps=[3,4,5,6,7,8,9,10] if not self.swd_failure else [5,11]
            if self.lost_commit_response:raise ConnectionError('Lost commit acknowledgement')
        if sub==6:
            self.cancel_calls+=1
            if self.finish_before_cancel:self.steps=[10];return b'\x82\x06\x03'
            self.steps=[12]
        if sub==5:
            state=self.steps.pop(0) if len(self.steps)>1 else self.steps[0]
            if state==10:self.flash[self.address-0x08000000:self.address-0x08000000+self.length]=self.staged
            error=8 if state==11 else 11 if state==12 else 0
            flags=3 if state==10 else int(self.commits>0)
            return reply+struct.pack('<BBBIIIIIIIQI',state,error,flags,self.operation,self.address,self.length,
                len(self.staged),self.length//1024 if state>=6 else 0,
                self.length if state>=7 and state not in (11,12) else 0,self.length if state==10 else 0,100000,0)
        if sub==9:return reply+struct.pack('<QQQQQQQ',1000,1000,1000,20000,10000,500,30000)
        return reply


class BufferedFlashTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_and_tcp_capacity_upload_batches_and_independent_verify(self):
        for capacity in (64,512):
            with self.subTest(capacity=capacity):
                target=ProbeTarget(capacity);f=FlashProgrammer(target,b'\0'*0x600);data=bytes(range(256))*8
                upload(f,data);result=await f.commit()
                self.assertTrue(result['verified']);self.assertTrue(result['probe_verified'])
                self.assertEqual(result['backend'],'probe');self.assertEqual(target.flash[:len(data)],data)
                self.assertEqual(result['upload_packets'],math.ceil(len(data)/(capacity-16)))
                self.assertEqual(target.entries,[]);self.assertEqual(target.commits,1)
                self.assertEqual(max(target.chunk_groups),8 if capacity==64 else 5)
                with self.assertRaises(ValueError):await f.commit()
                self.assertEqual(target.commits,1)
    async def test_old_firmware_falls_back_and_explicit_probe_is_rejected(self):
        target=AlgorithmTarget();f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        self.assertEqual((await f.commit())['backend'],'host')
        target=AlgorithmTarget();f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024);f.backend='probe'
        with self.assertRaisesRegex(ValueError,'does not support'):await f.commit()
        self.assertEqual(target.entries,[])
    async def test_force_host_on_new_probe_keeps_legacy_path(self):
        target=ProbeTarget();f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024);f.backend='host'
        self.assertEqual((await f.commit())['backend'],'host');self.assertEqual(target.commits,0)
        self.assertGreater(len(target.entries),0)
    async def test_bad_upload_ack_never_commits_and_is_not_replayed(self):
        target=ProbeTarget();target.upload_bad_ack=True;f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        with self.assertRaisesRegex(ConnectionError,'upload incomplete'):await f.commit()
        self.assertEqual(target.commits,0);self.assertEqual(target.cancel_calls,1)
        self.assertEqual(target.chunk_groups,[3]);self.assertFalse(f.progress['write_completion_may_be_unknown'])
    async def test_lost_commit_response_is_never_retried(self):
        target=ProbeTarget();target.lost_commit_response=True;f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        with self.assertRaisesRegex(ConnectionError,'Lost commit'):await f.commit()
        self.assertEqual(target.commits,1);self.assertEqual(target.cancel_calls,1)
        self.assertTrue(f.progress['write_completion_may_be_unknown'])
        with self.assertRaises(ValueError):await f.commit()
    async def test_board_failure_and_user_cancellation(self):
        target=ProbeTarget();target.swd_failure=True;f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        with self.assertRaisesRegex(RuntimeError,'error=8'):await f.commit()
        self.assertEqual(target.commits,1);self.assertTrue(f.progress['write_completion_may_be_unknown'])
        target=ProbeTarget();f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'y'*1024)
        task=asyncio.create_task(f.commit());await target.commit_received.wait();f.cancelled=True
        with self.assertRaisesRegex(RuntimeError,'cancelled'):await task
        self.assertEqual(target.commits,1);self.assertEqual(target.cancel_calls,1)
        self.assertEqual(f.progress['phase'],'cancelled')

    async def test_terminal_cancel_is_idempotent_and_preserves_success(self):
        target=ProbeTarget();f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        await f.commit();before=dict(f.progress)
        for _ in range(2):self.assertFalse(f.cancel()['cancel_requested'])
        self.assertEqual(f.progress,before);self.assertFalse(f.cancelled)
        error=FlashJobError(RuntimeError('cancelled'),{'phase':'cancelled','programmed_bytes':1024})
        self.assertEqual(error.status,409);self.assertEqual(error.progress['programmed_bytes'],1024)

    async def test_board_completion_race_is_not_reported_as_protocol_failure(self):
        target=ProbeTarget();target.finish_before_cancel=True;f=FlashProgrammer(target,b'\0'*0x600);upload(f,b'x'*1024)
        task=asyncio.create_task(f.commit());await target.commit_received.wait();f.cancel()
        with self.assertRaisesRegex(RuntimeError,'Flash cancelled'):await task
        self.assertEqual(f.progress['probe_state'],10);self.assertTrue(f.progress['probe_verified'])
        self.assertFalse(f.progress['write_completion_may_be_unknown']);self.assertEqual(target.commits,1)

    async def test_only_explicit_begin_oom_allows_auto_host_fallback(self):
        for backend in ('auto','probe'):
            target=ProbeTarget();target.out_of_memory=True;f=FlashProgrammer(target,b'\0'*0x600)
            upload(f,b'x'*1024);f.backend=backend
            if backend=='auto':
                result=await f.commit();self.assertEqual(result['backend'],'host')
                self.assertEqual(result['probe_fallback'],'image_cache_memory_unavailable')
                self.assertGreater(len(target.entries),0)
            else:
                with self.assertRaisesRegex(RuntimeError,'cache memory'):await f.commit()
                self.assertEqual(target.entries,[])
            self.assertEqual(target.commits,0);self.assertEqual(target.cancel_calls,0)

    async def test_invalid_staging_keeps_captures_and_prepare_is_exclusive(self):
        target=ProbeTarget();f=FlashProgrammer(target,b'\0'*0x600);called=[]
        async def prepare():
            called.append(True);self.assertTrue(f.busy)
            with self.assertRaises(ValueError):upload(f,b'y'*1024)
            f.cancel()
        with self.assertRaises(ValueError):await f.commit(before_commit=prepare)
        self.assertEqual(called,[])
        upload(f,b'x'*1024)
        with self.assertRaisesRegex(RuntimeError,'Flash cancelled'):await f.commit(before_commit=prepare)
        self.assertEqual(called,[True]);self.assertEqual(target.commits,0);self.assertFalse(f.busy)

    async def test_erase_strategy_follows_capability_and_complete_range(self):
        for length,supported in ((2048,True),(65536,False),(65536,True)):
            target=ProbeTarget();target.mass_feature=supported;f=FlashProgrammer(target,b'\0'*0x600)
            data=b'x'*length;f.stage(dict(action='begin',address=0x08000000,byte_length=length,sha256=hashlib.sha256(data).hexdigest()))
            for at in range(0,length,4096):f.stage(dict(action='chunk',offset=at,data=data[at:at+4096].hex()))
            result=await f.commit()
            self.assertEqual(result['erase_strategy'],'mass' if supported and length==65536 else 'page')


if __name__=='__main__':unittest.main()
