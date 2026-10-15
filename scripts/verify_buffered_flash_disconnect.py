"""Real TCP loss during programming, foreign-owner rejection and fresh recovery.

Disconnect is an actual socket close without DAP_Disconnect, not a mocked error.
It does not emulate a silent network blackhole or loss of target power.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from rmlink_swd import SwdSession
from rmlink_tool import TcpClient,status
from rmlink_usb import UsbClient
from verify_buffered_flash_faults import ProbeFlash,ROOT,SHA


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-target-write',action='store_true');p.add_argument('--host',default='192.168.137.170')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not args.allow_target_write:p.error('Target-write opt-in required')
    if args.output.exists():p.error('Choose a new evidence file')
    image=(ROOT/'artifacts/build/stm32-fixture/fixture-64k.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest()==SHA
    report=dict(capturedAt=datetime.now().astimezone().isoformat(),complete=False,checks={})
    client=swd=None
    try:
        client=TcpClient(args.host,3241);assert status(client)['ownerSession']==0
        swd=SwdSession(client).start();flash=ProbeFlash(client)
        options=[swd.read32(0x1ffff800+i*4) for i in range(4)]
        flash.begin(image);flash.upload(image);flash.request(3)
        old_operation=flash.operation
        with UsbClient() as foreign:
            for sub in (4,5,6):
                assert foreign.exchange(bytes([0x82,sub])+struct.pack('<I',old_operation))==bytes([0x82,sub,2])
            assert foreign.exchange(b'\x02\x01')==b'\x02\x00'
            report['checks']['foreignUsbCannotCommitQueryCancelOrConnect']=True
        flash.request(4);report['beforeClose']=flash.wait({6,7,8})
        assert report['beforeClose']['flags']&1
        begin=time.perf_counter();client.close();client=None;swd.connected=False
        # USB information is still available while the TCP owner is cleaned up.
        with UsbClient() as native:
            deadline=time.monotonic()+5
            while status(native)['ownerSession']:
                if time.monotonic()>deadline:raise TimeoutError('TCP owner was not released')
                time.sleep(.005)
            report['releaseMs']=(time.perf_counter()-begin)*1000
            with SwdSession(native) as observer:
                assert observer.read32(0xe000edf0)&0x20000
                assert native.exchange(b'\x82\x04'+struct.pack('<I',old_operation))==b'\x82\x04\x03'
                report['checks']['closedJobHaltedAndCannotReplayFromUsb']=True
        with TcpClient(args.host,3241) as fresh:
            with SwdSession(fresh) as target:
                assert target.read32(0xe000edf0)&0x20000
                assert fresh.exchange(b'\x82\x04'+struct.pack('<I',old_operation))==b'\x82\x04\x03'
                new=ProbeFlash(fresh);new.begin(image);assert new.operation!=old_operation
                new.upload(image);new.request(3);new.request(4)
                report['recovery']=new.wait({10},timeout=30)
                data=struct.pack('<16384I',*target.read_words(0x08000000,16384))
                assert data==image;assert not target.read32(0xe000edf0)&0x20000
                assert [target.read32(0x1ffff800+i*4) for i in range(4)]==options
                report['checks']['newTcpSessionDoesNotResumeOldJob']=True
                report['checks']['newOperationVerifiedRunningFixture']=True
                report['fixtureSha256']=hashlib.sha256(data).hexdigest()
        report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        if swd and swd.connected:swd.close()
        if client:client.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
