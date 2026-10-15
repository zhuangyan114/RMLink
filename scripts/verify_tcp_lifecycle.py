"""Real probe idle/STA recovery, while a native USB SWD owner stays active.

Stop Connector first: this diagnostic holds its one physical TCP connection.
This does not simulate a silent remote-host crash or turn off the PC hotspot.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_native_usb import UsbClient
from rmlink_tool import TcpClient,status
from rmlink_swd import SwdSession


def network(usb):
    r=usb.exchange(b'\x80\x04')
    assert r[:3]==b'\x80\x04\0' and len(r)>=10
    return bool(r[4]),socket.inet_ntoa(r[6:10])


def uptime(usb):
    r=usb.exchange(b'\x80\x06')
    assert r[:3]==b'\x80\x06\0'
    return int.from_bytes(r[3:11],'little')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924')
    p.add_argument('--idle-seconds',type=float,default=20)
    p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not 15<=args.idle_seconds<=60 or not 1<=args.repetitions<=20:p.error('idle 15..60 seconds, repetitions 1..20')
    # Avoid stealing the single physical transport from an active Connector.
    try:busy=socket.create_connection(('127.0.0.1',3243),timeout=.2)
    except OSError:pass
    else:busy.close();p.error('Stop Connector before this direct-TCP diagnostic')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
        'requestedClockHz':4000000,'actualClockMeasured':False,'fixtureRetained':True,
        'scope':'healthy TCP idle, then native USB SWD stays active across probe STA reconnects; no silent-host-crash simulation',
        'rows':[]}
    try:
        with UsbClient(bcd_device=0x0002,serial=args.device_id.upper()) as usb:
            up,ip=network(usb);assert up,'Probe WiFi is offline'
            start_uptime=uptime(usb);report['firmware']=status(usb)
            with TcpClient(ip,3241) as tcp:
                with SwdSession(tcp,4000000) as swd:
                    assert swd.read32(0xe000ed00)==0x411fc231
                    owner=status(tcp);assert owner['ownerTransport']==2
                    begin=time.perf_counter();time.sleep(args.idle_seconds)
                    report['idleSecondsActual']=time.perf_counter()-begin
                    assert swd.read32(0xe000ed00)==0x411fc231
                    after=status(tcp);assert after['ownerSession']==owner['ownerSession']
                    assert usb.exchange(b'\x02\x01')==b'\x02\x00','Idle TCP lost target ownership'
                    report['healthyIdleOwnerPreserved']=True
            # Reverse ownership: USB now stays active throughout network faults.
            with SwdSession(usb,4000000) as wired:
                assert wired.read32(0x20000000)==0x524d4c4b,'Expected running fixture'
                for i in range(args.repetitions):
                    up,ip=network(usb);assert up
                    counter_before=wired.read32(0x20000008)
                    with TcpClient(ip,3241) as old:
                        assert old.exchange(b'\x80\x09')==b'\x80\x09\0'+bytes.fromhex(args.device_id)
                        begin=time.perf_counter();assert usb.exchange(b'\x80\x05')==b'\x80\x05\0'
                        old.sock.settimeout(.05);closed=None;restored=None;reads=0;max_read_ms=0
                        row={'repetition':i+1};report['rows'].append(row)
                        deadline=begin+30
                        while time.perf_counter()<deadline:
                            start=time.perf_counter();assert wired.read32(0xe000ed00)==0x411fc231
                            max_read_ms=max(max_read_ms,(time.perf_counter()-start)*1000);reads+=1
                            if closed is None:
                                try:
                                    data=old.sock.recv(1)
                                    assert data==b'','Unexpected old TCP payload'
                                    closed=(time.perf_counter()-begin)*1000
                                except (ConnectionResetError,ConnectionAbortedError):closed=(time.perf_counter()-begin)*1000
                                except TimeoutError:pass
                            up,ip=network(usb)
                            if up and closed is None:
                                # FIN sent while WiFi was down may never reach
                                # the old peer. A read-only probe makes its TCP
                                # stack observe the invalid tuple after return.
                                # No target command is replayed.
                                old.sock.settimeout(.25)
                                try:old.exchange(b'\x00\xff')
                                except (ConnectionResetError,ConnectionAbortedError,ConnectionError):
                                    closed=(time.perf_counter()-begin)*1000
                                except TimeoutError:pass
                                else:raise AssertionError('Old TCP session survived STA reassociation')
                            row.update(oldTcpClosedMs=closed,usbCpuidReadsDuringFault=reads,maxUsbCpuidReadMs=max_read_ms,
                                       networkUp=up)
                            if closed is not None and up:
                                try:
                                    with TcpClient(ip,3241) as fresh:
                                        assert fresh.exchange(b'\x80\x09')==b'\x80\x09\0'+bytes.fromhex(args.device_id)
                                        assert fresh.exchange(b'\x02\x01')==b'\x02\x00'
                                        assert fresh.exchange(b'\x03')==b'\x03\xff'
                                    restored=(time.perf_counter()-begin)*1000;break
                                except (OSError,ConnectionError):pass
                            time.sleep(.025)
                        assert restored is not None and closed is not None,'TCP did not recover'
                    assert wired.read32(0xe000ed00)==0x411fc231
                    counter_after=wired.read32(0x20000008);assert counter_after>counter_before
                    row.update({'oldTcpClosedMs':closed,'newTcpReadyMs':restored,
                        'usbCpuidReadsDuringFault':reads,'maxUsbCpuidReadMs':max_read_ms,
                        'targetCounterBeforeAfter':[counter_before,counter_after],'usbOwnerPreserved':True})
                    print(json.dumps(report['rows'][-1]),flush=True)
            report['finalStatus']=status(usb);assert report['finalStatus']['ownerTransport']==0
            report['uptimeBeforeAfterUs']=[start_uptime,uptime(usb)]
            assert report['uptimeBeforeAfterUs'][1]>start_uptime,'ESP32 restarted'
            report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'failure':report.get('failure')}),flush=True)


if __name__=='__main__':main()
