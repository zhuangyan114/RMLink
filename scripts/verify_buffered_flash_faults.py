"""Real F103C8 upload rejection, cancellation and fresh-operation recovery.

Uses the public on-probe protocol without the connector's Flash implementation.
Target-write opt-in is mandatory. Keeps the canonical 64KiB fixture on success.
Stop the connector before a direct TCP test; never steals an occupied target.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import struct
import time
import zlib

from rmlink_swd import SwdSession
from rmlink_tool import TcpClient, status
from rmlink_usb import UsbClient

ROOT=Path(__file__).resolve().parents[1]
SHA='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4'


class ProbeFlash:
    def __init__(self,client):self.client=client;self.operation=0
    def request(self,sub,payload=None,error=0):
        if payload is None:payload=struct.pack('<I',self.operation)
        reply=self.client.exchange(bytes([0x82,sub])+payload)
        assert reply[:3]==bytes([0x82,sub,error]),(sub,reply.hex(),error)
        return reply[3:]
    def begin(self,image,digest=None):
        self.operation=secrets.randbelow(0xffffffff)+1
        self.request(1,struct.pack('<III',self.operation,0x08000000,len(image))+
                     (hashlib.sha256(image).digest() if digest is None else digest))
    def chunk(self,offset,data,*,crc=None,error=0):
        payload=struct.pack('<IIHI',self.operation,offset,len(data),zlib.crc32(data) if crc is None else crc)+data
        return self.request(2,payload,error)
    def upload(self,image):
        for at in range(0,len(image),self.client.packet_size-16):self.chunk(at,image[at:at+self.client.packet_size-16])
    def status(self):
        values=struct.unpack('<BBBIIIIIIIQI',self.request(5))
        return dict(zip(('state','error','flags','operation','address','length','received','erased_pages',
                         'programmed_bytes','verified_bytes','elapsed_us','error_address'),values))
    def wait(self,states,timeout=15):
        deadline=time.monotonic()+timeout
        while True:
            result=self.status()
            if result['state'] in states:return result
            if result['state']>=10:raise RuntimeError(f'Unexpected terminal Flash state: {result}')
            if time.monotonic()>=deadline:raise TimeoutError(f'Flash state wait: {result}')
            time.sleep(.005)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write',action='store_true');p.add_argument('--transport',choices=('wifi','usb'),default='wifi')
    p.add_argument('--host',default='192.168.137.170');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not args.allow_target_write:p.error('Target writes require explicit opt-in')
    if args.output.exists():p.error('Preserve existing evidence; choose a new output')
    image=(ROOT/'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes()
    assert len(image)==65536 and hashlib.sha256(image).hexdigest()==SHA
    report={'capturedAt':datetime.now().astimezone().isoformat(),'transport':args.transport,'complete':False,'checks':{}}
    def record(name,value=True):report['checks'][name]=value;print(name,value,flush=True)
    try:
        with (TcpClient(args.host,3241) if args.transport=='wifi' else UsbClient()) as client:
            report['probe']=status(client);assert report['probe']['ownerSession']==0
            with SwdSession(client) as swd:
                flash=ProbeFlash(client)
                def readback():return struct.pack('<16384I',*swd.read_words(0x08000000,16384))
                assert hashlib.sha256(readback()).hexdigest()==SHA
                record('initialFixtureSha256',SHA)
                options=[swd.read32(0x1ffff800+i*4) for i in range(4)]
                small=image[:1024];piece=small[:client.packet_size-16]
                flash.begin(small)
                flash.chunk(1,piece,error=1)  # a gap cannot become accepted data
                flash.chunk(0,piece,crc=zlib.crc32(piece)^1,error=5)
                flash.chunk(0,piece)
                repeated=flash.chunk(0,piece)
                assert int.from_bytes(repeated,'little')==len(piece)
                conflict=bytes([piece[0]^1])+piece[1:];flash.chunk(0,conflict,error=1)
                flash.request(3,error=3);flash.request(4,error=3)
                flash.request(6);cancelled=flash.wait({12})
                assert cancelled['flags']==0 and not cancelled['erased_pages']
                record('invalidChunksIncompleteSealAndReadyCancel',cancelled)
                flash.begin(small,digest=bytes(32));flash.upload(small);flash.request(3,error=6)
                bad_hash=flash.status();assert bad_hash['state']==11 and bad_hash['error']==6 and bad_hash['flags']==0
                record('actualSha256Rejection',bad_hash)
                assert readback()==image and not swd.read32(0xe000edf0)&0x20000
                record('rejectedUploadsPreserveFlashAndRunningCore')
                flash.begin(image);flash.upload(image);flash.request(3);flash.request(4)
                reached=flash.wait({6,7,8});assert reached['flags']&1
                flash.request(4,error=3)  # no second erase/program transaction
                flash.request(6);cancelled=flash.wait({12})
                assert cancelled['flags']&1 and cancelled['flags']&4 and swd.read32(0xe000edf0)&0x20000
                record('cancelAfterEraseStopsCore',cancelled)
                flash.request(4,error=3)
                # This is a deliberately NEW operation after cancellation, never a replay.
                flash.begin(image);flash.upload(image);flash.request(3);flash.request(4)
                completed=flash.wait({10},timeout=30)
                assert completed['flags']&2 and completed['verified_bytes']==len(image)
                assert readback()==image and not swd.read32(0xe000edf0)&0x20000
                assert [swd.read32(0x1ffff800+i*4) for i in range(4)]==options
                record('freshOperationRecoveryAndIndependentReadback',completed)
                record('fixtureSha256',SHA);record('optionBytesUnchanged')
        report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
