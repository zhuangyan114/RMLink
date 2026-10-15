"""No-target TCP upload and short/full reply burst diagnostic.

Checks every response. This measures the DAP framing path, not SWD or raw TCP.
Run with the connector stopped; a second control socket is not multiplexed.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import struct
import time

from benchmark_transport import bench_response
from rmlink_tool import TcpClient


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='192.168.137.170');p.add_argument('--request-bytes',type=int,default=512)
    p.add_argument('--reply',choices=('short','full'),default='short');p.add_argument('--window',type=int,choices=(1,2,4,8),default=8)
    p.add_argument('--batches',type=int,default=200);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():p.error('Choose a new evidence file')
    if not 6<=args.request_bytes<=512 or not 1<=args.batches<=100000:p.error('Request/batch budget')
    report=dict(capturedAt=datetime.now().astimezone().isoformat(),complete=False,window=args.window,
                requestBytes=args.request_bytes,reply=args.reply,batchesRequested=args.batches,batchesCompleted=0)
    latencies=[];begin=time.perf_counter()
    try:
        with TcpClient(args.host,3241) as c:
            if args.request_bytes>c.packet_size:raise ValueError('Request exceeds negotiated capacity')
            short=c.exchange(b'\x80\x09');assert len(short)==9 and short[:3]==b'\x80\x09\0'
            for batch in range(args.batches):
                sequence=batch*args.window;packets=[];expected=[]
                for i in range(args.window):
                    packet=b'\x80\x07'+struct.pack('<I',sequence+i) if args.reply=='full' else b'\x80\x09'
                    packets.append(packet+bytes(args.request_bytes-len(packet)))
                    expected.append(bench_response(sequence+i,c.packet_size) if args.reply=='full' else short)
                start=time.perf_counter();actual=c.exchange_many(packets);latencies.append((time.perf_counter()-start)*1000)
                if actual!=expected:raise ValueError('Burst reply order, count or content mismatch')
                report['batchesCompleted']+=1
            report['replyBytes']=len(expected[0]);report['packetSize']=c.packet_size
        report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        report['elapsedMs']=(time.perf_counter()-begin)*1000
        report['batchLatencyMs']=latencies
        if latencies:report['medianBatchMs']=statistics.median(latencies)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='batchLatencyMs'}),flush=True)


if __name__=='__main__':main()
