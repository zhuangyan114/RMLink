"""Only five independent depth-three pointers plus five direct variables.

Target runs throughout. 4 MHz requested SWD, 200 us requested Timeline,
four Watch fields at 100 ms and RTT at 5 ms. No other combinations.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import time
from benchmark_mixed_subscriptions import ApiClient,mixed_layout,collect,require
from fixture_clock import require_fixture_72mhz,discard_pretest_rtt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=float,default=15)
    p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--profile-swd',action='store_true',help='Enable costly per-transaction SWD counters')
    a=p.parse_args()
    if a.output.exists():p.error('Preserve existing evidence')
    report=dict(at=datetime.now().astimezone().isoformat(),complete=False,rows=[],
        pointerFields=5,pointerDepth=3,directFields=5,shared=False,coexist=True,
        requestedClockHz=4000000,periodUs=200)
    try:
        deadline=time.monotonic()+60
        while not any(d['device_id']=='50787d15a924' and d.get('connected')
                      for d in ApiClient().request('GET','/v1/status')['devices']):
            if time.monotonic()>=deadline:raise TimeoutError('Probe did not reconnect')
            time.sleep(.5)
        with ApiClient().open('50787d15a924') as c:
            profile_reply=c.exchange(bytes([0x80,14,int(a.profile_swd)]))
            report['swdProfileRequested']=a.profile_swd;report['swdProfileSwitchReply']=profile_reply.hex()
            report['target']=c.target('connect')
            report['targetClock']=require_fixture_72mhz(c)
            require(c.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52','Fixture missing')
            image,fields=mixed_layout(False)
            c.target('write_memory',address=0x20001000,data=image.hex())
            for repetition in range(a.repetitions):
                report['rttSetup']=discard_pretest_rtt(c)
                before=c.device_health('50787d15a924');started=time.perf_counter();ids=[]
                try:
                    s=c.subscribe('memory_series',fields=fields,period_us=200);ids.append(s['subscription_id'])
                    ids.append(c.subscribe('memory_snapshot',fields=fields[:4],period_us=100000)['subscription_id'])
                    ids.append(c.subscribe('rtt_stream',control_block_address=0x20000400,period_us=5000)['subscription_id'])
                    row=collect(c,s,a.seconds)
                    row['statuses']={str(i):c.subscription(i) for i in ids}
                    report['rows'].append(row)
                    require(not row['invalidFrames'] and not row['consumerGap'],'Invalid stream')
                    require(all(v['completed']>0 and not v['failures'] and not v['ring_dropped']
                                for v in row['statuses'].values()),f'A coexist subscription stalled or failed: {row["statuses"]}')
                except BaseException:
                    report['failedStatuses']={str(i):c.subscription(i) for i in ids}
                    report['failureHealth']=c.device_health('50787d15a924')
                    raise
                finally:
                    for sid in reversed(ids):c.unsubscribe(sid)
                elapsed=time.perf_counter()-started;after=c.device_health('50787d15a924')
                row.update(repetition=repetition+1,healthBefore=before,healthAfter=after,elapsed=elapsed)
                if before.get('performance',{}).get('supported') and after.get('performance',{}).get('supported'):
                    b=before['performance'];d={k:v-b[k] for k,v in after['performance'].items()
                        if isinstance(v,int) and not isinstance(v,bool) and k not in ('cpu_hz','max_capture_dap_us')}
                    if after['performance'].get('swd_metrics_active',True):
                        d['swdCpuUs']=d['swd_cpu_cycles']/(after['performance']['cpu_hz']/1e6)
                        d['idealWireUsAtRequestedClock']=d['swd_wire_clocks']/4
                    d['captureDapDuty']=d['capture_dap_us']/elapsed/1e6
                    row['performanceDelta']=d
                print(json.dumps({k:row[k] for k in ('repetition','effectiveHz','captureSpanUs','sampleIntervalUs','performanceDelta') if k in row}),flush=True)
            report['medianHz']=statistics.median(r['effectiveHz'] for r in report['rows']);report['complete']=True
    except BaseException as e:report['failure']=f'{type(e).__name__}: {e}';raise
    finally:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
