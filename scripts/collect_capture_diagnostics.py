"""Observe the existing owner; never connect, start/stop capture, or touch target RAM."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.request


def collect(port, state_dir, output, duration, interval, device_id=None):
    token=json.loads((Path(state_dir)/'connector.json').read_text(encoding='utf-8-sig'))['token']
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        request=urllib.request.Request(f'http://127.0.0.1:{port}/v1/{path}',headers={'Authorization':'Bearer '+token})
        with opener.open(request,timeout=5) as reply:return json.load(reply)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    began=time.monotonic();deadline=began+duration
    with output.open('x',encoding='utf-8') as log:
        while time.monotonic()<deadline:
            tick=time.monotonic()
            row={'time':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':tick-began}
            try:
                status=get('status')
                devices=[d for d in status['devices'] if d.get('paired') and (not device_id or d['device_id']==device_id)]
                if len(devices)!=1:raise ValueError('Select exactly one paired device with --device-id')
                device=devices[0];row['device']=device
                row['health']=get('devices/'+device['device_id']+'/health')
                owner=device.get('owner')
                if isinstance(owner,str) and owner.startswith('api-'):
                    # Read only the existing Timeline. This query renews its lease.
                    try:row['subscription']=get('sessions/'+owner+'/subscriptions/1')
                    except Exception as exc:row['subscription_error']=f'{type(exc).__name__}: {exc}'
            except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
            row['poll_elapsed_ms']=(time.monotonic()-tick)*1000
            log.write(json.dumps(row,separators=(',',':'))+'\n');log.flush()
            sub=row.get('subscription',{});stream=row.get('health',{}).get('capture_stream',{})
            print(json.dumps({'time':row['time'],'completed':sub.get('completed'),'ring_dropped':sub.get('ring_dropped'),
                'read_idle_ms':(sub.get('host_stream') or {}).get('read_idle_ms'),
                'blocked_send_us':stream.get('blocked_send_us'),'tcp':stream.get('tcp'),'error':row.get('error') or row.get('subscription_error')}),flush=True)
            time.sleep(max(0,min(interval-(time.monotonic()-tick),deadline-time.monotonic())))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=3243)
    parser.add_argument('--state-dir',type=Path,default=Path.home()/'.rmlink')
    parser.add_argument('--device-id')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--duration',type=float,default=120)
    parser.add_argument('--interval',type=float,default=2)
    args=parser.parse_args()
    if not 0<args.duration<=86400 or not .5<=args.interval<=60:parser.error('duration: (0,86400], interval: [0.5,60]')
    collect(args.port,args.state_dir,args.output,args.duration,args.interval,args.device_id)


if __name__=='__main__':main()
