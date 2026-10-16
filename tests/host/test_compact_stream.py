"""Independent wire oracle plus the actual firmware's in-place C packer."""
import asyncio
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from rmlink_connector.capture_protocol import CompactFrameDecoder,HEADER,SAMPLE
from rmlink_connector.subscriptions import Subscriptions


def frame(seq,kind=1,sid=1,capture=7,revision=2,words=3):
    data=struct.pack('<'+'I'*words,*[(seq*13+i)&0xffffffff for i in range(words)])
    if kind==3:data=struct.pack('<IQI',0,seq*3,3)+b'a\x00b'
    payload=SAMPLE.pack(seq,2**45+seq*211,97,(1<<words)-1 if kind!=3 else 0,seq&7,len(data))+data
    return HEADER.pack(b'RML1',1,kind,sid,0,capture,revision,seq,len(payload),1,32,zlib.crc32(payload))+payload


def compact_oracle(frames):
    groups=[]
    for f in frames:
        h=HEADER.unpack_from(f);key=(h[2],h[3],h[5],h[6])
        if not groups or groups[-1][0]!=key:groups.append((key,[]))
        groups[-1][1].append(f[28:]) # preserved CRC + sample payload
    return b''.join(struct.pack('<4sBBHIII',b'RMC2',k[0],k[1],len(rows),k[2],k[3],sum(map(len,rows)))+b''.join(rows) for k,rows in groups)


def delta_oracle(frames):
    groups=[]
    for f in frames:
        h=HEADER.unpack_from(f);key=(h[2],h[3],h[5],h[6])
        if not groups or groups[-1][0]!=key:groups.append((key,[]))
        groups[-1][1].append(f)
    result=bytearray()
    for key,rows in groups:
        body=bytearray();previous=b''
        for row in rows:
            payload=row[32:];delta=bytes(a^b for a,b in zip(payload,previous.ljust(len(payload),b'\0')))
            runs=[];start=0
            while start<len(delta):
                end=start+1;zero=delta[start]==0
                while end<len(delta) and (delta[end]==0)==zero and end-start<128:end+=1
                runs.append(bytes([(128 if zero else 0)+end-start-1])+(b'' if zero else delta[start:end]));start=end
            encoded=b''.join(runs);small=len(encoded)<len(payload)
            body+=row[28:32]+struct.pack('<HH',len(payload),len(encoded) if small else 0)+(encoded if small else payload)
            previous=payload
        result+=struct.pack('<4sBBHIII',b'RMC3',key[0],key[1],len(rows),key[2],key[3],len(body))+body
    return bytes(result)


class CompactDecoderTests(unittest.TestCase):
    def test_delta_fragmentation_reset_crc_and_negotiation_boundary(self):
        frames=[frame(0xfffffffe,words=32),frame(0,words=1),frame(7,words=32),frame(99,kind=3,sid=4),frame(100,revision=8)]
        wire=delta_oracle(frames)
        for split in range(len(wire)+1):
            decoder=CompactFrameDecoder(delta=True)
            self.assertEqual(decoder.feed(wire[:split])+decoder.feed(wire[split:]),frames)
        with self.assertRaises(ValueError):CompactFrameDecoder().feed(wire)
        self.assertEqual(CompactFrameDecoder(delta=True).feed(compact_oracle(frames)),frames)
        for offset,value in ((24,b'\xff\xff'),(26,b'\xff\xff'),(20,b'\0\0\0\0'),(28,b'\xff')):
            corrupt=bytearray(wire);corrupt[offset:offset+len(value)]=value
            with self.assertRaises(ValueError):CompactFrameDecoder(delta=True).feed(corrupt)

    def test_fragmentation_mixed_kinds_and_sequence_gaps(self):
        expected=[frame(9),frame(11),frame(0xffffffff,2,2),frame(0,3,4),frame(83,revision=9)]
        data=compact_oracle(expected)
        for split in range(len(data)+1):
            d=CompactFrameDecoder();self.assertEqual(d.feed(data[:split])+d.feed(data[split:]),expected);self.assertFalse(d.pending)
        for chunk in (1,7,19,20,23,63,4096):
            d=CompactFrameDecoder();actual=[]
            for offset in range(0,len(data),chunk):actual+=d.feed(data[offset:offset+chunk])
            self.assertEqual(actual,expected)

    def test_bounds_corruption_and_trailing_bytes(self):
        valid=compact_oracle([frame(1),frame(4)])
        for offset,value in ((0,b'FAIL'),(4,b'\x00'),(5,b'\x05'),(6,b'\x00\x00'),(16,struct.pack('<I',0xffffffff)),(46,b'\xff\xff'),(19,b'\x01'),(len(valid)-1,b'\xff')):
            data=bytearray(valid);data[offset:offset+len(value)]=value
            with self.assertRaises(ValueError):CompactFrameDecoder().feed(data)
        data=bytearray(valid);data[16:20]=struct.pack('<I',len(valid)-19);data+=b'\x00'
        with self.assertRaisesRegex(ValueError,'trailing'):CompactFrameDecoder().feed(data)
        d=CompactFrameDecoder();self.assertEqual(d.feed(valid[:-1]),[]);self.assertLess(len(d.pending),4116)
        self.assertEqual(d.feed(valid[-1:]),[frame(1),frame(4)])


class ActualFirmwareCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc=shutil.which('gcc') or (r'C:\mingw64\bin\gcc.exe' if Path(r'C:\mingw64\bin\gcc.exe').exists() else None)
        if not gcc:raise unittest.SkipTest('C compiler unavailable')
        cls.temp=tempfile.TemporaryDirectory();cls.exe=Path(cls.temp.name)/'codec.exe'
        root=Path(__file__).resolve().parents[2]
        subprocess.run([gcc,'-std=c11','-Wall','-Wextra','-Werror','-I'+str(root/'firmware/main'),str(root/'tests/capture/stream_codec_peer.c'),str(root/'firmware/main/rmlink_stream_codec.c'),'-o',str(cls.exe)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_packer_byte_exact_and_decodable_with_mixed_maximal_payloads(self):
        rng=random.Random(81637)
        for turn in range(40):
            frames=[];size=0
            for i in range(73):
                kind=3 if turn%2 and rng.randrange(8)==0 else 1
                f=frame((2**32-41+2*i)&0xffffffff,kind,3 if kind==3 else 1,capture=1+turn,revision=2+i//17,words=rng.choice((1,3,10,32)))
                if size+len(f)>4096:break
                frames.append(f);size+=len(f)
            r=subprocess.run([str(self.exe)],input=b''.join(frames),capture_output=True,check=True)
            self.assertEqual(r.stdout,compact_oracle(frames));self.assertLess(len(r.stdout),size)
            self.assertEqual(CompactFrameDecoder().feed(r.stdout),frames)
        frames=[frame(i,words=1) for i in range(68)]
        self.assertEqual(CompactFrameDecoder().feed(subprocess.run([str(self.exe)],input=b''.join(frames),capture_output=True,check=True).stdout),frames)

    def test_invalid_internal_batch_never_emits_partial_output(self):
        f=frame(3)
        invalid=[b'',f[:-1],f+b'\x00',f*70]
        for offset,value in ((0,b'FAIL'),(4,b'\x02'),(5,b'\x04'),(6,b'\x00'),(7,b'\x01'),(16,b'\x04'),(20,b'\xff\xff\xff\xff'),(24,b'\x02'),(26,b'\x20\x01'),(54,b'\xff\xff')):
            bad=bytearray(f);bad[offset:offset+len(value)]=value;invalid.append(f+bad)
        for data in invalid:
            r=subprocess.run([str(self.exe)],input=data,capture_output=True)
            self.assertEqual((r.returncode,r.stdout),(2,b''))

    def test_delta_actual_c_against_oracle_with_random_and_repeated_values(self):
        rng=random.Random(19337)
        total_raw=total_delta=0
        for turn in range(50):
            frames=[];size=0
            for i in range(73):
                f=frame((0xfffffff0+i*3)&0xffffffff,kind=3 if turn%7==0 else 1,revision=i//23,words=rng.choice((1,10,32)))
                if turn%3==0 and turn%7!=0:
                    f=bytearray(f);f[56:]=rng.randbytes(len(f)-56)
                    struct.pack_into('<I',f,28,zlib.crc32(f[32:]));f=bytes(f)
                if size+len(f)>4096:break
                frames.append(f);size+=len(f)
            wire=subprocess.run([str(self.exe),'delta'],input=b''.join(frames),capture_output=True,check=True).stdout
            self.assertEqual(wire,delta_oracle(frames));self.assertLess(len(wire),size)
            self.assertEqual(CompactFrameDecoder(delta=True).feed(wire),frames)
            total_raw+=size;total_delta+=len(wire)
        self.assertLess(total_delta,total_raw*.85)
        for bad in (b'',frame(1)[:-1],frame(1)+b'\0',frame(1)*74):
            run=subprocess.run([str(self.exe),'delta'],input=bad,capture_output=True)
            self.assertEqual((run.returncode,run.stdout),(2,b''))


class StreamNegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_advertised_capability_uses_rms2(self):
        for features,magic in ((0,b'RMS1'),(128,b'RMS1'),(256,b'RMS2'),(512,b'RMS3'),(768,b'RMS3')):
            reader=SimpleNamespace(readexactly=AsyncMock(return_value=b'OKAY'))
            writer=SimpleNamespace(write=lambda data:written.append(data),drain=AsyncMock(),get_extra_info=lambda name:'probe-socket')
            target=SimpleNamespace(capture_features=features,vendor=AsyncMock(return_value=b'x'*16),broker=SimpleNamespace(transport=SimpleNamespace(host='127.0.0.1')))
            captures=Subscriptions(target);captures._stream=AsyncMock();written=[]
            with patch('asyncio.open_connection',AsyncMock(return_value=(reader,writer))), patch('rmlink_connector.subscriptions.configure_probe_tcp') as configure:
                await captures.ensure_stream()
                configure.assert_called_once_with('probe-socket')
            self.assertEqual(written,[magic+b'x'*16]);await captures.task
