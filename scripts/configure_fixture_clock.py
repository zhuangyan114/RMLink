"""Identify F103 fixture HSE safely at direct-clock speed, then select 72 MHz.

Only for the standalone RMLink fixture. Does not erase or program Flash.
DWT cycles / host monotonic time estimate frequency, not an external counter.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import struct
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from rmlink_connector.client import ApiClient

RCC=0x40021000


def read(c,address):return int.from_bytes(bytes.fromhex(c.target('read_memory',address=address,byte_length=4)['data']),'little')
def write(c,address,value):c.target('write_memory',address=address,data=struct.pack('<I',value).hex())
def poll(c,address,mask,value):
    end=time.monotonic()+3
    while read(c,address)&mask!=value:
        if time.monotonic()>end:raise TimeoutError(f'Clock readiness {address:#x}')
        time.sleep(.01)


def measure(c):
    points=[]
    for _ in range(8):
        a=time.perf_counter();cycles=read(c,0xe0001004);b=time.perf_counter()
        points.append(dict(time=(a+b)/2,cycles=cycles,roundTrip=b-a));time.sleep(.15)
    slopes=[((b['cycles']-a['cycles'])&0xffffffff)/(b['time']-a['time'])
            for i,a in enumerate(points) for b in points[i+1:] if b['time']-a['time']>.4]
    return dict(estimatedHz=statistics.median(slopes),points=points,
                method='DWT cycle counter / host monotonic time; midpoint timing; no external frequency counter')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.output.exists():p.error('Preserve existing evidence')
    report=dict(at=datetime.now().astimezone().isoformat(),complete=False)
    try:
        with ApiClient().open('50787d15a924') as c:
            report['target']=c.target('connect')
            if read(c,0x20000000)!=0x524d4c4b:raise ValueError('RMLink fixture required')
            old_demcr=read(c,0xe000edfc);old_ctrl=read(c,0xe0001000)
            if old_ctrl&(1<<25):raise ValueError('DWT cycle counter unavailable')
            write(c,0xe000edfc,old_demcr|(1<<24));write(c,0xe0001000,old_ctrl|1)
            try:
                report['before']=dict(cr=read(c,RCC),cfgr=read(c,RCC+4),flash_acr=read(c,0x40022000),clock=measure(c))
                if not a.verify_only:
                    if report['before']['cfgr']&12:raise ValueError('Expected reset HSI clock before HSE identification')
                    write(c,0x40022000,(report['before']['flash_acr']&~7)|0x12)
                    write(c,RCC,read(c,RCC)|(1<<16));poll(c,RCC,1<<17,1<<17)
                    write(c,RCC+4,(read(c,RCC+4)&~3)|1);poll(c,RCC+4,12,4)
                    report['hseDirect']=measure(c);hz=report['hseDirect']['estimatedHz']
                    crystal=next((m for m in (8,12,16) if abs(hz-m*1e6)<m*1e6*.04),None)
                    if crystal is None:raise ValueError(f'HSE frequency not identified: {hz}')
                    divide=crystal==16;multiplier=72//(crystal//(2 if divide else 1))
                    report['crystalMHz']=crystal;report['pllMultiplier']=multiplier;report['hseDiv2']=divide
                    write(c,RCC,read(c,RCC)&~(1<<24));poll(c,RCC,1<<25,0)
                    cfg=read(c,RCC+4)&~0x7fffff
                    cfg|=1|(4<<8)|(2<<14)|(1<<16)|(int(divide)<<17)|((multiplier-2)<<18)
                    write(c,RCC+4,cfg);write(c,RCC,read(c,RCC)|(1<<24));poll(c,RCC,1<<25,1<<25)
                    write(c,RCC+4,(read(c,RCC+4)&~3)|2);poll(c,RCC+4,12,8)
                report['after']=dict(cr=read(c,RCC),cfgr=read(c,RCC+4),flash_acr=read(c,0x40022000),clock=measure(c))
                if abs(report['after']['clock']['estimatedHz']-72000000)>720000:
                    raise AssertionError('Measured CPU clock differs from 72 MHz by more than 1%')
                if report['after']['cfgr']&12!=8:raise AssertionError('PLL was not selected')
                report['stateAtEnd']=c.target('state');report['complete']=True
            except BaseException:
                if not a.verify_only:
                    write(c,RCC,read(c,RCC)|1);poll(c,RCC,2,2)
                    write(c,RCC+4,read(c,RCC+4)&~3);poll(c,RCC+4,12,0)
                raise
            finally:
                write(c,0xe0001000,old_ctrl);write(c,0xe000edfc,old_demcr)
    except BaseException as e:report['failure']=f'{type(e).__name__}: {e}';raise
    finally:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
