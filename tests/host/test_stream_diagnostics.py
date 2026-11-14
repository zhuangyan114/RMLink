import asyncio
from contextlib import redirect_stderr
import io
import json
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rmlink_connector.diagnostics import decode_stream_diagnostics
from rmlink_connector.stream_diagnostics import CaptureStreamDiagnostics
from rmlink_connector.subscriptions import Subscriptions
from rmlink_connector.broker import Broker
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from test_capture import record


def pages():
    # Independent USB64 wire fixture: each body is 60 bytes, response is 63.
    bodies=[
        struct.pack('<6I4Q',17,100,102,2,1,11,2**40,2**40-100,2**40-500,9999999999),
        struct.pack('<10I2Q',100,4000000,2000000,3,0,10,9,11,12,4096,8888888888,2**40-50),
        struct.pack('<12IQ',17,1,3000,2,3,1440,2000,0,4,5,6000,7000,2**40-20),
        struct.pack('<14I',2097152,100,4096,4000,6144,6000,6144,5990,90000,50000,(-65)&0xffffffff,6,1,25),
    ]
    return [b'\x80\x0f\0\x01'+bytes([i,7,0])+body for i,body in enumerate(bodies)]


class StreamWireTests(unittest.TestCase):
    def test_blocked_sender_retransmission_and_full_ring_are_distinguishable(self):
        result=decode_stream_diagnostics(pages())
        self.assertTrue(result['active']);self.assertTrue(result['send_in_progress'])
        self.assertEqual(result['snapshot_uptime_us'],2**40)
        self.assertEqual(result['wire_bytes'],9999999999)
        self.assertEqual(result['raw_bytes'],8888888888)
        self.assertEqual(result['blocked_send_us'],2000000)
        self.assertEqual(result['tcp']['retries'],2)
        self.assertEqual(result['tcp']['send_buffer_free_bytes'],0)
        self.assertEqual(result['buffers'][0]['max_item_bytes'],100)
        self.assertEqual(result['rssi_dbm'],-65)

    def test_unsupported_firmware_and_stale_tcp_snapshot_are_explicit(self):
        self.assertEqual(decode_stream_diagnostics([b'\x80\x0f\x01']*4),{'supported':False})
        packets=pages();packet=bytearray(packets[2]);struct.pack_into('<I',packet,7,16);packets[2]=bytes(packet)
        result=decode_stream_diagnostics(packets)
        self.assertFalse(result['tcp']['available']);self.assertFalse(result['tcp']['same_stream'])

    def test_truncation_wrong_page_and_reserved_bits_are_rejected(self):
        for page in range(4):
            packets=pages();packets[page]=packets[page][:-1]
            with self.assertRaises(ValueError):decode_stream_diagnostics(packets)
        for offset,value in ((4,9),(5,128),(6,1)):
            packets=pages();packet=bytearray(packets[2]);packet[offset]=value;packets[2]=bytes(packet)
            with self.assertRaises(ValueError):decode_stream_diagnostics(packets)


class StreamProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_probe_diagnostics_do_not_block_progress_reports(self):
        waiting=asyncio.Event();reported=asyncio.Event()
        async def submit(source,packets,priority):
            self.assertEqual(source,'capture-diagnostics');self.assertEqual(priority,'background')
            self.assertEqual(packets,[b'\x80\x0f'+bytes([i]) for i in range(4)])
            waiting.set();await asyncio.Event().wait()
        captures=Subscriptions(SimpleNamespace(broker=SimpleNamespace(submit=submit)))
        diagnostics=CaptureStreamDiagnostics('fixture')
        def emit(*args):
            if waiting.is_set():reported.set()
        with patch.object(diagnostics,'emit',emit):
            task=asyncio.create_task(diagnostics.report(captures,interval=.005,report_seconds=.01))
            try:
                await asyncio.wait_for(reported.wait(),1)
                self.assertIsNotNone(diagnostics.probe_poll_started)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_health_reads_cross_foreign_owner_without_target_access(self):
        class Transport:
            packet_size=512
            def __init__(self):self.requests=[]
            async def connect(self):pass
            async def close(self):pass
            async def abort(self):pass
            async def exchange_many(self,packets):
                self.requests.extend(packets)
                return [b'\x02\x01' if p==b'\x02\x01' else b'\x03\0' if p==b'\x03' else
                    pages()[p[2]] if p[:2]==b'\x80\x0f' else p[:2]+b'\x02' for p in packets]
        transport=Transport();broker=Broker(transport,'fixture')
        await broker.start()
        try:
            await broker.submit('existing-owner',[b'\x02\x01'])
            with tempfile.TemporaryDirectory() as directory:
                connector=Connector(State(directory));connector.brokers['fixture']=broker
                connector.state.data['paired']['fixture']={}
                result=await connector.route('GET','/v1/devices/fixture/health',{})
            self.assertEqual(result['capture_stream']['tcp']['retries'],2)
            self.assertEqual(broker.owner,'existing-owner')
            self.assertEqual(transport.requests[1:],[b'\x80'+bytes([i]) for i in (10,11,12,13)]+
                [b'\x80\x0f'+bytes([i]) for i in range(4)])
        finally:await broker.close()

    async def test_independent_reporter_emits_while_probe_read_is_blocked(self):
        target=SimpleNamespace(broker=SimpleNamespace(device_id='fixture',transport=SimpleNamespace()))
        captures=Subscriptions(target);captures.reader=asyncio.StreamReader()
        reported=asyncio.Event()
        original=CaptureStreamDiagnostics.report
        async def fast_report(diagnostics, capture):
            await original(diagnostics,capture,interval=.005,report_seconds=.01)
        def emitted(diagnostics,capture,event='progress'):
            if event=='progress' and diagnostics.snapshot()['read_idle_ms']>=5:reported.set()
        with patch.object(CaptureStreamDiagnostics,'report',fast_report),patch.object(CaptureStreamDiagnostics,'emit',emitted):
            task=asyncio.create_task(captures._stream())
            try:
                await asyncio.wait_for(reported.wait(),1)
                self.assertFalse(task.done())
                self.assertIsNone(captures.error)
                self.assertEqual(captures.stream_diagnostics.frames,0)
                captures.reader.feed_data(record(123))
                for _ in range(100):
                    if captures.next_sequence:break
                    await asyncio.sleep(.001)
                self.assertEqual(captures.next_sequence,1)
                self.assertEqual(captures.stream_diagnostics.snapshot()['subscriptions'][1]['sequence'],123)
            finally:
                task.cancel();await task
        self.assertFalse(any('CaptureStreamDiagnostics.report' in t.get_coro().__qualname__ for t in asyncio.all_tasks() if t is not asyncio.current_task()))

    async def test_fragment_progress_does_not_hide_sample_stall(self):
        now=[10.0];diagnostics=CaptureStreamDiagnostics('fixture',clock=lambda:now[0])
        now[0]+=2;diagnostics.received(3,[],.001,.002,3)
        snapshot=diagnostics.snapshot()
        self.assertEqual(snapshot['read_idle_ms'],0)
        self.assertEqual(snapshot['sample_idle_ms'],2000)
        self.assertEqual(snapshot['pending_bytes'],3)

    async def test_failed_tcp_observer_does_not_disrupt_logging_or_capture(self):
        diagnostics=CaptureStreamDiagnostics('fixture');captures=Subscriptions(None)
        captures.writer=SimpleNamespace(get_extra_info=lambda key:object())
        output=io.StringIO()
        with patch('rmlink_connector.stream_diagnostics.probe_tcp_info',side_effect=OSError('unavailable')),redirect_stderr(output):
            diagnostics.emit(captures)
        row=json.loads(output.getvalue().split('RMLink stream ',1)[1])
        self.assertEqual(row['tcp'],{'unavailable':'OSError'})
        self.assertNotIn('token',row)
        self.assertEqual(row['event'],'capture_progress')
