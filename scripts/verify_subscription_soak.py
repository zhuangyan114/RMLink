"""Bounded-memory real-target subscription/recording soak, including pointer chains."""
import argparse
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient
from rmlink_connector.recording_reader import inspect_recording
from benchmark_pointer_subscriptions import layout,require
from fixture_clock import require_fixture_72mhz,discard_pretest_rtt


class Measurements:
    def __init__(self):
        self.kinds={k:{'frames':0,'gaps':0,'budgetFlags':0,'firstUs':None,'lastUs':None,
                       'lastSequence':None} for k in (1,2,3)}
        self.recent_intervals=deque(maxlen=8192)
        self.rtt_pending=bytearray();self.rtt_next=None;self.rtt_records=0;self.rtt_bytes=0
        self.rtt_last_sequence=None;self.rtt_producer_skipped=0
        self.first_invalid=None
        self.dynamic_last={};self.dynamic_changes={}

    def require_dynamic_timeline(self):
        require(len(self.dynamic_changes.get('1',[]))==10 and all(self.dynamic_changes['1']),
                'A dynamic Timeline fixture field never changed')

    def accept(self,cursor,sample):
        kind=sample['kind'];m=self.kinds[kind];seq=sample['sequence'];stamp=sample['timestamp_us']
        if sample['flags']&1:
            self.first_invalid={'cursor':cursor,'sample':{k:v.hex() if isinstance(v,bytes) else v for k,v in sample.items()}}
            raise AssertionError(f'Invalid/partial sample kind={kind} seq={seq} flags={sample["flags"]} span={sample.get("duration_us")}')
        if m['lastSequence'] is not None:
            delta=(seq-m['lastSequence'])&0xffffffff
            require(0<delta<0x80000000,'Repeated/reordered sequence')
            m['gaps']+=delta-1
            if kind==1:self.recent_intervals.append(stamp-m['lastUs'])
        if m['firstUs'] is None:m['firstUs']=stamp
        m['lastUs']=stamp;m['lastSequence']=seq;m['frames']+=1;m['budgetFlags']+=bool(sample['flags']&2)
        if kind!=3:
            values=sample['values']
            require(sample['valid_bitmap']==(1<<len(values))-1,'Invalid field bitmap')
            require(len(values)==(10 if kind==1 else 4),'Fixture field count')
            for i,value in enumerate(values):require(value>>24==i,f'Wrong field identity {i}')
            key=str(kind)
            if key not in self.dynamic_last:self.dynamic_changes[key]=[0]*len(values)
            else:
                for i,value in enumerate(values):self.dynamic_changes[key][i]+=value!=self.dynamic_last[key][i]
            self.dynamic_last[key]=list(values)
            return
        require(self.rtt_next is None or sample['byte_sequence']==self.rtt_next,'RTT byte stream gap')
        data=sample['data'];self.rtt_next=sample['byte_sequence']+len(data);self.rtt_bytes+=len(data)
        self.rtt_pending.extend(data)
        while len(self.rtt_pending)>=16:
            magic,seq,counter,check=struct.unpack_from('<IIII',self.rtt_pending)
            require(magic==0x524d5254 and check==magic^seq^counter,'RTT fixture checksum')
            if self.rtt_last_sequence is not None:
                delta=(seq-self.rtt_last_sequence)&0xffffffff
                require(0<delta<0x80000000,'RTT fixture repeated/reordered record')
                # The fixture may skip publication if its nonblocking ring is
                # full. Keep producer skips separate from transport byte gaps.
                self.rtt_producer_skipped+=delta-1
            self.rtt_last_sequence=seq
            del self.rtt_pending[:16];self.rtt_records+=1

    def summary(self):
        result={}
        for kind,m in self.kinds.items():
            span=(m['lastUs'] or 0)-(m['firstUs'] or 0)
            result[str(kind)]={**m,'effectiveHz':(m['frames']-1)*1e6/span if span>0 else None}
        intervals=sorted(self.recent_intervals)
        def q(p):return intervals[int((len(intervals)-1)*p)] if intervals else None
        return {'kinds':result,'rttBytes':self.rtt_bytes,'rttRecordsChecked':self.rtt_records,
                'rttProducerSkipped':self.rtt_producer_skipped,
                'rttPartialBytes':len(self.rtt_pending),'intervalWindow':len(intervals),
                'recentTimelineIntervalP50Us':q(.5),'recentTimelineIntervalP99Us':q(.99),
                'firstInvalidSample':self.first_invalid}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--seconds',type=float,default=600)
    p.add_argument('--pointer-depth',type=int,choices=range(5),default=3)
    p.add_argument('--independent',action='store_true');p.add_argument('--period-us',type=int,default=1000)
    p.add_argument('--direct-fields',type=int,choices=range(11),default=0,help='Replace the final N fields with direct dynamic fixture leaves')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not 10<=args.seconds<=86400:p.error('seconds must be 10..86400')
    if not 200<=args.period_us<=10000000:p.error('Invalid period')
    if args.output.exists():p.error('Output already exists; preserve the previous evidence')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
            'requestedSeconds':args.seconds,'pointerDepth':args.pointer_depth,'shared':not args.independent,
            'requestedPeriodUs':args.period_us,'requestedClockHz':4000000,'actualClockMeasured':False,
            'directFields':args.direct_fields,'pointerFields':10-args.direct_fields if args.pointer_depth else 0,
            'scope':'public service + real F103 fixture + independent recording; installed VS Code UI excluded',
            'fixtureFlashRetained':True,'progress':[]}
    try:
        with ApiClient().open(args.device_id) as client:
            report['device']=next(d for d in client.request('GET','/v1/status')['devices'] if d['device_id']==args.device_id)
            # Raw DAP is allowed only before the high-level target session owns
            # target state. Read the probe-only status before that transition.
            network=client.exchange(b'\x80\x04')
            require(len(network)>=10 and network[:3]==b'\x80\x04\x00','Network status response')
            report['wirelessAtStart']={'connected':bool(network[4]),
                'rssiDbm':struct.unpack('b',network[5:6])[0],
                'scope':'probe snapshot before capture; not continuous RF measurement'}
            report['target']=client.target('connect')
            report['targetClock']=require_fixture_72mhz(client)
            report['healthBefore']=client.device_health(args.device_id)
            require(client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52','Fixture missing')
            image,fields=layout(args.pointer_depth,not args.independent)
            if args.direct_fields:
                for i in range(10-args.direct_fields,10):fields[i]={'address':0x20000100+i*64,'offsets':[]}
            report['fields']=fields
            client.target('write_memory',address=0x20001000,data=image.hex())
            report['rttSetup']=discard_pretest_rtt(client)
            ids=[];recording=None;begin=None;live=Measurements();after=-1;consumer_gap=0
            try:
                sub=client.subscribe('memory_series',fields=fields,period_us=args.period_us)
                ids.append(sub['subscription_id']);after=sub['stream_cursor']
                ids.append(client.subscribe('memory_snapshot',fields=fields[:4],period_us=100000)['subscription_id'])
                ids.append(client.subscribe('rtt_stream',control_block_address=0x20000400,period_us=5000)['subscription_id'])
                recording=client.start_recording(strict=False);report['recordingStarted']=recording;begin=time.perf_counter()
                next_state=begin;next_progress=begin+30
                while time.perf_counter()-begin<args.seconds:
                    batch=client.events(after,256,500);after=batch['cursor'];consumer_gap+=batch['consumer_gap']
                    require(not batch['error'],f'Stream error: {batch["error"]}')
                    require(not consumer_gap,'Live consumer fell behind')
                    for row in batch['records']:live.accept(row['cursor'],row['sample'])
                    now=time.perf_counter()
                    if now>=next_state:
                        require(client.target('state')['state']=='Running','Target stopped')
                        statuses={str(i):client.subscription(i) for i in ids}
                        report['statuses']=statuses
                        report['healthLatest']=client.device_health(args.device_id)
                        require(all(s['state']==2 and not s['failures'] for s in statuses.values()),f'Probe stopped: {statuses}')
                        require(all(not s['ring_dropped'] for s in statuses.values()),f'Probe data loss: {statuses}')
                        next_state=now+2
                    if now>=next_progress:
                        progress={'elapsedSeconds':now-begin,**live.summary(),'statuses':statuses}
                        report['progress'].append(progress);print(json.dumps(progress),flush=True);next_progress=now+30
                report['elapsedSeconds']=time.perf_counter()-begin
                report['statuses']={str(i):client.subscription(i) for i in ids}
                report['recording']=client.recording(recording['recording_id'],stop=True);recording=None
                require(report['recording']['phase']=='complete' and report['recording']['finalized'],'Recording failed')
                disk=Measurements();parsed=inspect_recording(report['recording']['path'],disk.accept)
                # Keep validated measurements even if the acceptance assertions
                # below reject this run; a failed run is still useful evidence.
                report.update(live=live.summary(),disk=disk.summary(),consumerGap=consumer_gap,
                              independentFileValidation=parsed)
                require(parsed['sha256']==report['recording']['sha256'],'Independent recording hash mismatch')
                require(all(m['frames'] for m in disk.kinds.values()),'A subscription produced no records')
                require(not disk.rtt_pending,'RTT ended inside a fixture record')
                require(all(not s['ring_dropped'] and not s['failures'] for s in report['statuses'].values()),'Probe data loss')
                live.require_dynamic_timeline();disk.require_dynamic_timeline()
                report['diskDynamicFieldChanges']=disk.dynamic_changes
                report['complete']=True
            finally:
                report['finalStatuses']={}
                try:report['healthAfter']=client.device_health(args.device_id)
                except Exception as error:report['healthAfter']={'queryError':str(error)}
                for sid in ids:
                    try:report['finalStatuses'][str(sid)]=client.subscription(sid)
                    except Exception as error:report['finalStatuses'][str(sid)]={'queryError':str(error)}
                if begin is not None:
                    report.setdefault('elapsedSeconds',time.perf_counter()-begin)
                report.setdefault('live',live.summary())
                report['liveDynamicFieldChanges']=live.dynamic_changes
                report.setdefault('consumerGap',consumer_gap)
                if recording:
                    try:report['failedRecording']=client.recording(recording['recording_id'],stop=True)
                    except Exception as error:report['cleanupError']=str(error)
                    else:
                        try:
                            failed=report['failedRecording']
                            require(failed['phase']=='complete' and failed['finalized'],'Failed recording not finalized')
                            disk=Measurements();parsed=inspect_recording(failed['path'],disk.accept)
                            report['disk']=disk.summary();report['independentFileValidation']=parsed
                            require(parsed['sha256']==failed['sha256'],'Independent failed recording hash mismatch')
                        except Exception as error:report['failedRecordingValidationError']=str(error)
                for sid in reversed(ids):
                    try:client.unsubscribe(sid)
                    except Exception:pass
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'complete':report['complete'],'output':str(args.output),'failure':report.get('failure')}),flush=True)


if __name__=='__main__':main()
