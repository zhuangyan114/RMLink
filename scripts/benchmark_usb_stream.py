"""Physical USB bulk throughput; immutable PSRAM pattern, no STM32 accesses.

Report application bytes rather than the 12 Mbit/s USB signalling rate.
Alternate zero is restored on success, failure and interruption.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import struct
import sys
import time
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from rmlink_native_usb import UsbClient
from rmlink_connector.client import ApiClient

PATTERN=bytes((i*31+(i>>8)*17+77)&255 for i in range(65536))


@contextmanager
def idle_connector(serial):
    """Keep the installed service's health polling off the raw DAP endpoints.

    The benchmark owns its own WinUSB handle; two control readers cannot safely
    share IN83. Refuse a live owner and temporarily move an idle connector to
    Wi-Fi. Restore its preference only after the benchmark USB handle closes.
    """
    state=Path(os.environ.get('USERPROFILE',str(Path.home())))/'.rmlink/connector.json'
    previous=None;api=None
    if state.is_file():
        api=ApiClient(timeout=3)
        try:devices=api.request('GET','/v1/status')['devices']
        except (urllib.error.URLError,ConnectionError,TimeoutError):devices=[]
        device=next((d for d in devices if d['device_id'].lower()==serial.lower() and d.get('paired')),None)
        if device:
            if device.get('owner'):
                raise RuntimeError('Close target sessions before the raw USB benchmark')
            if device.get('transport')=='usb':
                api.timeout=30
                previous=device['device_id']
    try:
        if previous:api.set_transport(previous,'wifi')
        yield bool(previous)
    finally:
        if previous:api.set_transport(previous,'usb')


def expected_pattern(offset,length):
    start=offset&65535
    data=bytearray((PATTERN+PATTERN)[start:start+length])
    for block in range(offset//4096,(offset+length+4095)//4096):
        header=struct.pack('<I',block)
        position=block*4096-offset
        for i,value in enumerate(header):
            if 0<=position+i<length:data[position+i]=value
    return data


def run(client,mib,*,stall_seconds=0):
    expected=mib*1024*1024
    begin=time.perf_counter();hello=client.open_stream(benchmark_mib=mib)
    count=reads=0;latencies=[];stalled=False
    deadline=begin+max(30,mib*4)+stall_seconds
    try:
        if hello!=struct.pack('<II',expected,65536):raise AssertionError('USB benchmark metadata')
        while count<expected:
            if time.perf_counter()>deadline:raise TimeoutError(f'USB stream benchmark deadline, received {count}/{expected} bytes')
            data=client.read_stream()
            if not data:continue
            if data!=expected_pattern(count,len(data)):raise AssertionError(f'USB pattern/order mismatch at {count}')
            count+=len(data);reads+=1
            if count>expected:raise AssertionError('Excess USB data')
            if reads%64==0:
                start=time.perf_counter()
                if client.exchange(b'\x00\xfe')!=b'\x00\x01\x08':raise AssertionError('DAP control reply during stream')
                latencies.append((time.perf_counter()-start)*1000)
            if stall_seconds and not stalled and count>=expected//4:
                time.sleep(stall_seconds);stalled=True
        elapsed=time.perf_counter()-begin
        return dict(bytes=count,seconds=elapsed,payloadMbps=count*8/elapsed/1e6,
                    payloadBytesPerSecond=count/elapsed,fullSpeedBulkCeilingFraction=count/elapsed/1216000,
                    reads=reads,mismatches=0,controlSamples=len(latencies),
                    controlMedianMs=statistics.median(latencies) if latencies else None,
                    controlMaxMs=max(latencies) if latencies else None,hostPauseSeconds=stall_seconds)
    finally:client.close_stream()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',default='50787D15A924')
    parser.add_argument('--mib',type=int,default=8)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--stall-seconds',type=float,default=0)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.mib<=128 or not 1<=args.rounds<=20 or not 0<=args.stall_seconds<=3:parser.error('Invalid size/rounds/pause')
    if args.output.exists():parser.error('Preserve existing benchmark evidence')
    report=dict(startedAt=datetime.now().astimezone().isoformat(),completed=False,rounds=[],
                evidence='Physical ESP32 PSRAM to PC; no SWD or target access',
                usbSignallingMbps=12,theoreticalFullSpeedBulkPayloadBytesPerSecond=1216000)
    try:
        with idle_connector(args.serial) as changed,UsbClient(bcd_device=2,serial=args.serial) as client:
            report['idleConnectorTemporarilyMovedToWifi']=changed
            for _ in range(args.rounds):
                row=run(client,args.mib,stall_seconds=args.stall_seconds)
                report['rounds'].append(row);print(json.dumps(row),flush=True)
            report['medianPayloadMbps']=statistics.median(r['payloadMbps'] for r in report['rounds'])
        report['completed']=True
    except BaseException as exc:report['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        report['finishedAt']=datetime.now().astimezone().isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
