"""No Orbit: public HTTP control plus a real binary socket and stream response."""
import asyncio
import json
import struct
import tempfile
import ctypes
from pathlib import Path
import unittest
import time
from rmlink_connector.broker import Broker
from rmlink_connector.service import Connector
from rmlink_connector.state import State
from test_capture import record


class ProbeModel:
    packet_size=512
    host='127.0.0.1'
    def __init__(self):
        self.started=asyncio.Event();self.requests=[]
        self.capture_ids={};self.next_capture_id=6
    async def connect(self): pass
    async def close(self): pass
    async def abort(self): pass
    async def exchange_many(self,packets):
        out=[]
        for p in packets:
            self.requests.append(p)
            if p[0]==0x81:
                if p[1]==13:
                    now=time.perf_counter_ns()//1000
                    out.append(b'\x81\x0d\x00'+p[2:10]+struct.pack('<QQ',now,now))
                elif p[1]==16:
                    fid,offset=struct.unpack('<IH',p[2:])
                    data=record(fid-1) if self.started.is_set() and 1<=fid<=5 else b''
                    chunk=data[offset:offset+52]
                    out.append(b'\x81\x10\x00'+struct.pack('<IHHB',fid,len(data),offset,len(chunk))+chunk)
                elif p[1]==11: out.append(b'\x81\x0b\x00'+b'X'*16)
                elif p[1]==7: out.append(bytes([0x81,7,0])+struct.pack('<BBBIIQQQQIB',2,1,2,self.capture_ids.get(p[2],7),5,5,0,0,0,0,0))
                else:
                    if p[1]==3:
                        self.next_capture_id+=1;self.capture_ids[p[2]]=self.next_capture_id
                        self.started.set()
                    out.append(bytes([0x81,p[1],0]))
            elif p[0]==5:
                pos=3; reads=[];address=0
                for _ in range(p[2]):
                    flags=p[pos];pos+=1
                    if flags&2: reads.append({0xe000ed00:0x411fc231,0xe0042000:0x410}.get(address,0xf0000040 if flags==6 else 0x1ba01477))
                    else:
                        if flags==5:address=struct.unpack_from('<I',p,pos)[0]
                        pos+=4
                out.append(bytes([5,p[2],1])+struct.pack('<'+'I'*len(reads),*reads))
            else: out.append(bytes([p[0],1 if p[0]==2 else 0]))
        return out


class CaptureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_retains_subscription_then_continue_resumes_same_capture(self):
        from unittest.mock import AsyncMock
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/target',{'command':'connect'})
        _,config=await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000]})
        owned=self.connector.sessions[session['session_id']]
        owned['target'].execute=AsyncMock(return_value={'state':'Halted'})
        status,_=await self.http('POST',root+'/target',{'command':'reset'})
        self.assertEqual(status,200)
        self.assertEqual(owned['captures'].configs[1]['capture_id'],config['capture_id'])
        self.assertIn(1,owned['captures'].paused_for_control)
        await self.http('POST',root+'/target',{'command':'run'})
        self.assertFalse(owned['captures'].paused_for_control)
        self.assertIn(b'\x81\x06\x01',self.probe.requests)
        self.assertNotIn(b'\x81\x04\x01',self.probe.requests)
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.connector=Connector(State(self.tmp.name))
        self.probe=ProbeModel();self.broker=Broker(self.probe,'model');await self.broker.start()
        self.connector.brokers['model']=self.broker
        self.peers=[];self.reject_binding=False
        self.server=await asyncio.start_server(self.peer,'127.0.0.1',3244)
        self.api=await self.connector.start(0)
        # Avoid discovery/mounting real devices during a host-only test.
        self.connector.background.cancel();await asyncio.gather(self.connector.background,return_exceptions=True)
        self.connector.background=None
    async def asyncTearDown(self):
        await self.connector.close();self.server.close();await self.server.wait_closed();self.tmp.cleanup()
    async def peer(self,reader,writer):
        try:
            self.peers.append(writer)
            previous_capture=self.probe.capture_ids.get(1)
            self.assertEqual(await reader.readexactly(20),b'RMS1'+b'X'*16)
            if self.reject_binding:
                writer.write(b'NOPE');await writer.drain();await reader.read();return
            writer.write(b'OKAY');await writer.drain();await self.probe.started.wait()
            # A replacement stream starts a fresh decoder, even if the old
            # stream ended halfway through a frame with another capture ID.
            while self.probe.capture_ids.get(1)==previous_capture:await asyncio.sleep(.001)
            data=bytearray(b''.join(record(i) for i in range(5)))
            for offset in range(0,len(data),64):struct.pack_into('<I',data,offset+8,self.probe.capture_ids[1])
            for i in range(0,len(data),7):writer.write(data[i:i+7]);await writer.drain()
            await reader.read()
        finally:writer.close();await writer.wait_closed()
    async def http(self,method,path,body=None):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.api)
        data=json.dumps(body or {}).encode()
        writer.write(f'{method} {path} HTTP/1.1\r\nAuthorization: Bearer {self.connector.state.data["token"]}\r\nContent-Length: {len(data)}\r\n\r\n'.encode()+data)
        await writer.drain();header=await reader.readuntil(b'\r\n\r\n')
        length=int(next(line.split(b':')[1] for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')))
        result=json.loads(await reader.readexactly(length));writer.close();await writer.wait_closed()
        return int(header.split()[1]),result

    async def fail_stream(self,captures):
        self.peers[-1].write(record(99)[:17]);await self.peers[-1].drain()
        self.peers[-1].close();await self.peers[-1].wait_closed()
        await asyncio.wait_for(asyncio.shield(captures.task),1)
        self.assertIn('Probe capture stream closed',captures.error)

    async def test_new_capture_recovers_data_stream_without_replacing_owner_or_replaying_rtt(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/target',{'command':'connect'})
        _,initial=await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        _,events=await self.http('POST',root+'/events',{'wait_ms':1000})
        self.assertEqual(len(events['records']),5)
        await self.http('POST',root+'/subscriptions',{'kind':'rtt_stream','control_block_address':0x20000400})
        await self.http('POST',root+'/recordings',{'strict':False})
        owned=self.connector.sessions[session['session_id']];captures=owned['captures'];target=owned['target']
        captures.accept_frames([record(5)])
        await self.fail_stream(captures)
        sealed=await captures.recording.stop();sealed_hash=sealed['sha256']
        self.assertEqual((sealed['phase'],sealed['error']),('failed','capture_stream_failed'))
        self.assertEqual((await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000]}))[0],400)
        self.assertEqual(len(self.peers),1) # An invalid duplicate cannot reconnect.
        await self.http('DELETE',root+'/subscriptions/1')
        before=len(self.probe.requests);cursor=captures.next_sequence-1
        code,replacement=await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000008,0x2000000c]})
        self.assertEqual(code,200)
        self.assertNotEqual(replacement['capture_id'],initial['capture_id'])
        _,events=await self.http('POST',root+'/events',{'after':cursor,'wait_ms':1000})
        self.assertEqual(len(events['records']),5);self.assertIsNone(events['error'])
        self.assertTrue(all(int.from_bytes(bytes.fromhex(row['frame'])[8:12],'little')==replacement['capture_id'] for row in events['records']))
        self.assertEqual(events['records'][0]['cursor'],cursor+1)
        self.assertIs(owned['target'],target);self.assertIs(owned['captures'],captures)
        self.assertEqual(self.broker.owner,session['session_id']);self.assertEqual(len(self.peers),2)
        self.assertFalse(any(p[:2]==b'\x81\x06' or p[:3]==b'\x81\x03\x03' for p in self.probe.requests[before:]))
        self.assertEqual((captures.recording.status()['phase'],captures.recording.status()['sha256']),('failed',sealed_hash))
        from rmlink_connector.recording_reader import inspect_recording
        self.assertEqual(inspect_recording(sealed['path'])['sha256'],sealed_hash)

    async def test_rejected_recovery_closes_socket_and_allows_another_explicit_start(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        await self.http('POST',root+'/events',{'wait_ms':1000})
        captures=self.connector.sessions[session['session_id']]['captures']
        await self.fail_stream(captures);error=captures.error
        await self.http('DELETE',root+'/subscriptions/1')
        self.reject_binding=True
        code,result=await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000]})
        self.assertEqual((code,result['error']),(503,'Stream binding rejected'))
        self.assertIsNone(captures.writer);self.assertIsNone(captures.reader);self.assertIsNone(captures.task)
        self.assertEqual(captures.error,error)
        self.reject_binding=False
        self.assertEqual((await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000]}))[0],200)
        self.assertIsNone(captures.error)

    async def test_bulk_usb_failure_can_restart_without_reopening_control_transport(self):
        from unittest.mock import AsyncMock
        self.probe.native_usb=True
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/target',{'command':'connect'})
        captures=self.connector.sessions[session['session_id']]['captures']
        captures.target.capture_features|=1024
        pieces=asyncio.Queue()
        async def read_stream():
            item=await pieces.get()
            if isinstance(item,Exception):raise item
            return item
        self.probe.open_capture_stream=AsyncMock();self.probe.close_capture_stream=AsyncMock()
        self.probe.read_capture_stream=read_stream
        await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        await pieces.put(ConnectionError('USB stream lost'))
        await asyncio.wait_for(asyncio.shield(captures.task),1)
        self.assertIn('USB stream lost',captures.error)
        await self.http('DELETE',root+'/subscriptions/1')
        self.assertEqual((await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]}))[0],200)
        await pieces.put(record(0))
        _,events=await self.http('POST',root+'/events',{'wait_ms':1000})
        self.assertEqual(len(events['records']),1);self.assertIsNone(events['error'])
        self.assertEqual(self.probe.open_capture_stream.await_count,2)
        self.assertEqual(self.probe.close_capture_stream.await_count,1)
        self.assertFalse(self.peers);self.assertEqual(self.broker.owner,session['session_id'])
    async def test_full_public_subscription_path(self):
        code,r=await self.http('POST','/v1/sessions',{'device_id':'model'});self.assertEqual(code,200)
        root='/v1/sessions/'+r['session_id']
        code,r=await self.http('POST',root+'/target',{'command':'connect'});self.assertEqual(code,200)
        self.assertEqual(r['clock_hz'],4000000)
        code,r=await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        self.assertEqual((code,r['subscription_id']),(200,1))
        code,events=await self.http('POST',root+'/events',{'wait_ms':1000});self.assertEqual(code,200)
        self.assertEqual(len(events['records']),5)
        self.assertEqual((await self.http('POST',root+'/dap',{'packets':['0201']}))[0],400)
        code,r=await self.http('GET',root+'/subscriptions/1');self.assertEqual((code,r['completed']),(200,5))
        reader,writer=await asyncio.open_connection('127.0.0.1',self.api)
        writer.write(f'GET {root}/stream HTTP/1.1\r\nAuthorization: Bearer {self.connector.state.data["token"]}\r\n\r\n'.encode());await writer.drain()
        header=await reader.readuntil(b'\r\n\r\n');self.assertIn(b'application/vnd.rmlink.stream',header)
        size=int((await reader.readline()).strip(),16);body=await reader.readexactly(size)
        self.assertEqual(struct.unpack_from('<QI',body),(0,64))
        self.assertEqual(body[12:16],b'RML1')
        writer.close();await writer.wait_closed()
        self.assertEqual((await self.http('DELETE',root+'/subscriptions/1'))[0],200)
        self.assertEqual((await self.http('DELETE',root))[0],200)
        self.assertIsNone(self.broker.owner)

    async def test_native_usb_subscriptions_use_the_same_api_and_never_open_a_tcp_data_peer(self):
        self.probe.native_usb=True
        code,r=await self.http('POST','/v1/sessions',{'device_id':'model'});self.assertEqual(code,200)
        root='/v1/sessions/'+r['session_id']
        self.assertEqual((await self.http('POST',root+'/target',{'command':'connect'}))[0],200)
        self.assertEqual((await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]}))[0],200)
        code,events=await self.http('POST',root+'/events',{'wait_ms':1000})
        self.assertEqual(code,200);received=events['records']
        while len(received)<5:
            code,events=await self.http('POST',root+'/events',{'after':events['cursor'],'wait_ms':1000})
            self.assertEqual(code,200);self.assertTrue(events['records']);received.extend(events['records'])
        self.assertEqual(len(received),5)
        self.assertFalse(any(p[:2]==b'\x81\x0b' for p in self.probe.requests))
        self.assertTrue(any(p[:2]==b'\x81\x10' for p in self.probe.requests))
        self.assertEqual((await self.http('DELETE',root))[0],200)
        self.assertIsNone(self.broker.owner)

    async def test_bulk_usb_records_cross_reads_without_fragment_pull_or_target_lock(self):
        self.probe.native_usb=True
        code,r=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+r['session_id']
        self.assertEqual((await self.http('POST',root+'/target',{'command':'connect'}))[0],200)
        owned=self.connector.sessions[r['session_id']]
        owned['target'].capture_features|=1024
        pieces=asyncio.Queue();opened=[];closed=[]
        async def open_stream(ticket):opened.append(ticket)
        async def read_stream():return await pieces.get()
        async def close_stream():closed.append(True)
        self.probe.open_capture_stream=open_stream
        self.probe.read_capture_stream=read_stream
        self.probe.close_capture_stream=close_stream
        self.assertEqual((await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]}))[0],200)
        raw=b''.join(record(i) for i in range(5))
        # Split both the header and a record body; include several records in
        # one USB read. The DAP lock stays held while the independent IN runs.
        async with owned['target'].lock:
            for start,end in ((0,3),(3,77),(77,len(raw))):await pieces.put(raw[start:end])
            for _ in range(20):
                if owned['captures'].next_sequence==5:break
                await asyncio.sleep(.01)
            self.assertEqual(owned['captures'].next_sequence,5)
        self.assertEqual(opened,[b'X'*16])
        self.assertFalse(any(p[:2]==b'\x81\x10' for p in self.probe.requests))
        self.assertEqual((await self.http('DELETE',root))[0],200)
        self.assertEqual(closed,[True]);self.assertIsNone(self.broker.owner)

    async def test_native_c_sdk_uses_public_api_without_orbit(self):
        dll_path=Path(__file__).resolve().parents[2]/'artifacts/build/sdk/rmlink.dll'
        if not dll_path.exists():self.skipTest('Build-Sdk.ps1 required for native ABI test')
        self.connector.brokers['50787d15a924']=self.broker
        lib=ctypes.CDLL(str(dll_path))
        lib.rm_open.argtypes=[ctypes.c_char_p,ctypes.c_uint16,ctypes.c_char_p];lib.rm_open.restype=ctypes.c_void_p
        lib.rm_close.argtypes=[ctypes.c_void_p]
        lib.rm_devices.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        lib.rm_capabilities.argtypes=lib.rm_devices.argtypes
        lib.rm_time_sync.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        lib.rm_target_connect.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_size_t]
        lib.rm_subscribe.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t]
        cb_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p)
        lib.rm_stream.argtypes=[ctypes.c_void_p,cb_type,ctypes.c_void_p]
        token=self.connector.state.data['token'].encode();received=[]
        def run():
            discovery=lib.rm_open(None,self.api,token);self.assertTrue(discovery)
            try:
                out=ctypes.create_string_buffer(4096)
                required=ctypes.c_size_t()
                self.assertEqual(lib.rm_capabilities(discovery,out,len(out),ctypes.byref(required)),200)
                self.assertEqual(json.loads(out.value)['default_clock_hz'],4000000)
                self.assertEqual(lib.rm_devices(discovery,out,len(out),ctypes.byref(required)),200)
                self.assertEqual(json.loads(out.value),[])
                self.assertEqual(lib.rm_time_sync(discovery,b'50787d15a924',4,out,len(out),ctypes.byref(required)),200)
                self.assertEqual(json.loads(out.value)['samples'],4)
                self.assertEqual(lib.rm_target_connect(discovery,0,out,len(out)),-87)
                self.assertFalse(self.connector.sessions)
                self.assertIsNone(self.broker.owner)
            finally:lib.rm_close(discovery)
            client=lib.rm_open(b'50787d15a924',self.api,token);self.assertTrue(client)
            try:
                out=ctypes.create_string_buffer(4096)
                self.assertEqual(lib.rm_target_connect(client,4000000,out,len(out)),200)
                config=json.dumps({'kind':'memory_series','addresses':[0x20000000,0x20000004]}).encode()
                self.assertEqual(lib.rm_subscribe(client,config,out,len(out)),200)
                @cb_type
                def callback(cursor,frame,size,context):
                    received.append((cursor,ctypes.string_at(frame,size)));return 1
                self.assertEqual(lib.rm_stream(client,callback,None),200)
            finally:lib.rm_close(client)
        await asyncio.to_thread(run)
        self.assertEqual(received[0][0],0);self.assertEqual(received[0][1][:4],b'RML1')

    async def test_session_close_fences_requests_and_waits_for_cleanup_before_release(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        sid=session['session_id'];root='/v1/sessions/'+sid
        await self.http('POST',root+'/dap',{'packets':['0201']})
        entered=asyncio.Event();finish=asyncio.Event();order=[]
        class Target:
            async def cleanup(self):
                entered.set();await finish.wait();order.append('target')
        class Captures:
            async def close(self):order.append('captures')
        class Flash:cancelled=False
        flash=Flash()
        self.connector.sessions[sid].update(target=Target(),captures=Captures(),flash=flash)
        close=asyncio.create_task(self.connector.close_session(sid))
        await entered.wait()
        self.assertTrue(flash.cancelled)
        self.assertEqual(self.broker.owner,sid)
        self.assertEqual((await self.http('POST',root+'/heartbeat'))[0],503)
        finish.set();await close
        self.assertEqual(order,['captures','target'])
        self.assertIsNone(self.broker.owner)
        await self.connector.close_session(sid) # repeated close is harmless

    async def test_public_recording_api_writes_an_independently_validated_container(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/target',{'command':'connect'})
        await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        await self.http('POST',root+'/events',{'wait_ms':1000})
        code,recording=await self.http('POST',root+'/recordings',{})
        self.assertEqual(code,200)
        captures=self.connector.sessions[session['session_id']]['captures']
        captures.accept_frames([record(i) for i in range(10,15)])
        code,status=await self.http('POST',root+'/recordings/'+recording['recording_id'],{'action':'stop'})
        self.assertEqual((code,status['frames'],status['phase']),(200,5,'complete'))
        from rmlink_connector.recording_reader import inspect_recording
        self.assertEqual(inspect_recording(status['path'])['sha256'],status['sha256'])
        self.assertEqual((await self.http('DELETE',root))[0],200)

    async def test_delayed_storage_failure_cannot_pause_replacement_capture_or_start_a_new_recording(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('POST',root+'/target',{'command':'connect'})
        await self.http('POST',root+'/subscriptions',{'kind':'memory_series','addresses':[0x20000000,0x20000004]})
        await self.http('POST',root+'/events',{'wait_ms':1000})
        await self.http('POST',root+'/recordings',{})
        captures=self.connector.sessions[session['session_id']]['captures'];rec=captures.recording
        def full(data):raise OSError('disk full')
        rec.write=full
        await captures.target.lock.acquire()
        try:
            captures.accept_frames([record(10)])
            await asyncio.wait_for(asyncio.shield(rec.task),1)
            self.assertFalse(rec.progress['finalized'])
            self.assertFalse(rec.failure_task.done())
            with self.assertRaises(ValueError):await captures.start_recording(self.tmp.name,{})
            before=len(self.probe.requests)
            captures.configs[1]=dict(captures.configs[1],capture_id=8)
        finally:captures.target.lock.release()
        await rec.stop()
        self.assertFalse(any(p[:2]==b'\x81\x05' for p in self.probe.requests[before:]))

    async def test_flash_replacement_preserves_owner_stream_cursor(self):
        _,session=await self.http('POST','/v1/sessions',{'device_id':'model'})
        root='/v1/sessions/'+session['session_id']
        await self.http('GET',root+'/flash')
        owner=self.connector.sessions[session['session_id']]
        previous=owner['captures'];previous.accept_frames([record(i) for i in range(5)])
        previous.host_dropped=7
        class CompletedFlash:
            busy=False
            async def commit(self,before_commit):
                await before_commit();return {'phase':'complete','verified':True}
        owner['flash']=CompletedFlash()
        code,result=await self.http('POST',root+'/flash',{'action':'commit'})
        self.assertEqual((code,result['phase']),(200,'complete'))
        replacement=owner['captures'];self.assertIsNot(replacement,previous)
        replacement.accept_frames([record(0)])
        code,result=await self.http('POST',root+'/events',{'after':4,'wait_ms':0})
        self.assertEqual((code,result['cursor'],result['consumer_gap']),(200,5,0))
        self.assertEqual(result['host_history_evicted'],12)
        self.assertEqual(len(result['records']),1)

if __name__=='__main__':unittest.main()
