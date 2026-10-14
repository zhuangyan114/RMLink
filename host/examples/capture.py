"""Run without Orbit: python capture.py DEVICE_ID --seconds 10."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rmlink_connector.client import ApiClient

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('device_id'); p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--period-us',type=int,default=1000)
    p.add_argument('--addresses',nargs='+',type=lambda s:int(s,0),default=[0x20000000,0x20000008])
    args=p.parse_args()
    with ApiClient().open(args.device_id) as client:
        print(json.dumps(client.target('connect')))
        sub=client.subscribe('memory_series',addresses=args.addresses,period_us=args.period_us)
        begin=time.perf_counter(); count=0
        try:
            for sample in client.stream():
                count+=1
                if count%1000==1: print(json.dumps(sample),flush=True)
                if time.perf_counter()-begin>=args.seconds: break
        finally:
            print(json.dumps(client.subscription(sub['subscription_id'])))
            client.unsubscribe(sub['subscription_id'])
        print(json.dumps({'received':count,'seconds':time.perf_counter()-begin}))

if __name__=='__main__': main()
