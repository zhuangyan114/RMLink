"""Disconnect only the probe STA while recording; no PC hotspot changes or write replay."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient,RMLinkError
from rmlink_connector.recording_reader import inspect_recording
from rmlink_native_usb import UsbClient
from accept_virtual_usb import wait_virtual_usb


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'requestedClockHz':4000000,
        'actualClockMeasured':False,'fixtureRetained':True,'fault':'probe STA disconnect via native USB 80/05; PC hotspot stays on'}
    old=ApiClient();deadline=None
    try:
        old.open(args.device_id);old.target('connect')
        before=int.from_bytes(bytes.fromhex(old.target('read_memory',address=0x20000008,byte_length=4)['data']),'little')
        sub=old.subscribe('memory_series',addresses=[0x20000100+i*64 for i in range(10)],period_us=2000)
        recording=old.start_recording(strict=False);time.sleep(1)
        assert old.recording(recording['recording_id'])['frames']>0
        begin=time.perf_counter();deadline=begin+30
        with UsbClient(bcd_device=0x0002,serial=args.device_id.upper()) as physical:
            assert physical.exchange(b'\x80\x05')==b'\x80\x05\x00'
        while time.perf_counter()<deadline:
            try:old.target('state')
            except RMLinkError as error:
                assert error.status==503;report['oldSessionFailedMs']=(time.perf_counter()-begin)*1000;break
            time.sleep(.05)
        else:raise TimeoutError('Old session did not fail')
        sealed=None
        while time.perf_counter()<deadline:
            try:
                candidate=inspect_recording(recording['path'])
                if candidate['summary']['finalized']:sealed=candidate;break
            except (OSError,ValueError):pass
            time.sleep(.05)
        assert sealed and sealed['frames']>0 and sealed['summary']['phase']=='failed',sealed
        report['failedRecording']={'frames':sealed['frames'],'sha256':sealed['sha256'],'summary':sealed['summary']}
        discovery=ApiClient()
        while time.perf_counter()<deadline:
            status=discovery.request('GET','/v1/status')
            connected=next((d for d in status['devices'] if d['device_id']==args.device_id and d['connected']),None)
            if connected and connected['owner'] is None and args.device_id in status['driver']['managed_ports']:break
            time.sleep(.2)
        else:raise TimeoutError('New broker/virtual device did not recover')
        with wait_virtual_usb(timeout=max(1,deadline-time.perf_counter())) as virtual:
            assert virtual.serial=='RM'+args.device_id.upper()
            assert virtual.exchange(b'\x00\xff')==b'\x00\x02\x00\x02'
        report['automaticRecoveryMs']=(time.perf_counter()-begin)*1000
        try:old.target('state')
        except RMLinkError as error:assert error.status==503
        else:raise AssertionError('Lost lease revived')
        with ApiClient().open(args.device_id) as fresh:
            fresh.target('connect')
            try:fresh.subscription(1)
            except RMLinkError as error:assert error.status==400
            else:raise AssertionError('Old subscription replayed')
            assert fresh.target('read_memory',address=0xe000ed00,byte_length=4)['data']=='31c21f41'
            after=int.from_bytes(bytes.fromhex(fresh.target('read_memory',address=0x20000008,byte_length=4)['data']),'little')
            assert after>before;report['targetCounterBeforeAfter']=[before,after]
            new=fresh.subscribe('memory_series',addresses=[0x20000100],period_us=2000)
            assert new['capture_id']!=sub['capture_id']
            batch=fresh.events(new['stream_cursor'],128,1000)
            assert batch['records'] and all(row['sample']['capture_id']==new['capture_id'] and
                row['sample']['valid_bitmap']==1 for row in batch['records'])
            fresh.unsubscribe(1);report['newCaptureId']=new['capture_id']
        assert inspect_recording(recording['path'])['sha256']==sealed['sha256']
        report['oldLeaseTerminal']=True;report['oldPlanNotReplayed']=True;report['oldFileImmutableAfterRecovery']=True
        report['ownerAtEnd']=next(d for d in discovery.request('GET','/v1/status')['devices'] if d['device_id']==args.device_id)['owner']
        assert report['ownerAtEnd'] is None;report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        try:old.close()
        except RMLinkError as error:
            if error.status!=503:report['cleanupError']=str(error)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
