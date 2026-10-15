"""Exercise the built experimental Orbit DAP adapter against the real RMLink.

This checks a child debug adapter over stdio, not installed VS Code or its UI.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from rmlink_connector.client import ApiClient


class Adapter:
    def __init__(self,worktree,node_report_directory=None):
        self.stderr=bytearray();self.messages=queue.Queue();self.events=[];self.seq=0
        self.request_trace=[]
        command=['node']
        if node_report_directory:
            node_report_directory=node_report_directory.resolve()
            node_report_directory.mkdir(parents=True,exist_ok=True)
            command+=['--report-on-fatalerror','--report-exclude-env',f'--report-directory={node_report_directory}']
        self.child=subprocess.Popen(command+[str(worktree/'dist/debugadapter.js')],cwd=worktree,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def reader():
            try:
                while True:
                    header=self.child.stdout.readline()
                    if not header:break
                    if not header.startswith(b'Content-Length: '):raise ValueError('Adapter stdout framing')
                    size=int(header.split(b':',1)[1]);assert 0<size<=4*1024*1024
                    assert self.child.stdout.readline()==b'\r\n'
                    payload=bytearray()
                    while len(payload)<size:
                        part=self.child.stdout.read(size-len(payload))
                        if not part:raise EOFError('Adapter truncated response')
                        payload.extend(part)
                    self.messages.put(json.loads(payload))
            except BaseException as error:self.messages.put(error)
            finally:self.messages.put(EOFError('Adapter exited'))
        def errors():
            while True:
                part=self.child.stderr.read(1024)
                if not part:break
                self.stderr.extend(part)
        threading.Thread(target=reader,daemon=True).start();threading.Thread(target=errors,daemon=True).start()

    def next(self,timeout):
        message=self.messages.get(timeout=timeout)
        if isinstance(message,BaseException):raise message
        if message.get('type')=='event':self.events.append(message)
        return message

    def request(self,command,args=None,timeout=30):
        self.seq+=1;seq=self.seq
        trace={'seq':seq,'command':command,'completed':False};self.request_trace.append(trace)
        data=json.dumps({'type':'request','seq':seq,'command':command,'arguments':args or {}},ensure_ascii=True).encode()
        self.child.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);self.child.stdin.flush()
        end=time.perf_counter()+timeout
        while time.perf_counter()<end:
            message=self.next(max(.01,end-time.perf_counter()))
            if message.get('type')=='response' and message.get('request_seq')==seq:
                if not message.get('success'):raise RuntimeError(f'{command}: {message}')
                trace['completed']=True
                return message.get('body')
        raise TimeoutError(command)

    def collect(self,seconds):
        deadline=time.perf_counter()+seconds
        while time.perf_counter()<deadline:
            try:self.next(min(.5,deadline-time.perf_counter()))
            except queue.Empty:pass

    def close(self):
        try:
            if self.child.poll() is None:self.request('disconnect',{'terminateDebuggee':False},timeout=15)
            self.child.wait(timeout=10)
        except Exception:
            self.child.kill();self.child.wait(timeout=5)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worktree',type=Path,default=ROOT/'artifacts/worktrees/orbit-rmlink')
    p.add_argument('--device-id',default='50787d15a924');p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--period-us',type=int,default=2000)
    p.add_argument('--matrix',action='store_true',help='Use all ten original discrete 2D-array fields')
    p.add_argument('--pointers',action='store_true',help='Use ten fields through the existing typed three-pointer chain')
    p.add_argument('--node-report-directory',type=Path,help='Optional Node fatal-error reports, excluding environment variables')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not 2<=args.seconds<=300:p.error('seconds must be 2..300')
    if not 200<=args.period_us<=10000000:p.error('period-us must be 200..10000000')
    if args.matrix and args.pointers:p.error('Choose matrix or pointers')
    report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,
        'scope':'real Orbit debug adapter child + public SDK + connector + real STM32; VS Code UI excluded',
        'fixtureRetained':True,'requestedClockHz':4000000,'actualClockMeasured':False,'periodUs':args.period_us,
        'fieldSet':'ten_three_level_pointer_fields' if args.pointers else 'ten_discrete_matrix_words' if args.matrix else 'three_struct_scalars'}
    deadline=time.perf_counter()+30
    while True:
        try:
            status=ApiClient().request('GET','/v1/status')
            if any(d['device_id']==args.device_id and d['connected'] for d in status['devices']):break
        except OSError:pass
        if time.perf_counter()>deadline:raise ConnectionError('RMLink did not become ready in 30 seconds')
        time.sleep(.2)
    report['nodeVersion']=subprocess.check_output(['node','--version']).decode().strip()
    adapter=Adapter(args.worktree,args.node_report_directory)
    try:
        report['initialize']=adapter.request('initialize',{'adapterID':'ozone','clientID':'rmlink-acceptance','linesStartAt1':True})
        report['launch']=adapter.request('launch',{'device':'STM32F103C8','probe':'cmsis-dap',
            'rmlinkDeviceId':args.device_id,'program':str(ROOT/'artifacts/build/stm32-fixture/fixture.elf'),
            'flashBeforeDebug':False,'runToEntryPoint':False,'speedKHz':4000,'loggingEnabled':False,
            'rttLogEnabled':True,'rttControlBlockAddress':'0x20000400','rttStripAnsi':False})
        adapter.request('configurationDone');adapter.request('continue',{'threadId':1})
        expressions=[f'capture_root->next->leaf->values[{i}]' for i in range(10)] if args.pointers else [f'capture_values[{i}][0]' for i in range(10)] if args.matrix else [
            'fixture_state.counter','fixture_state.boots','fixture_state.guard']
        report['samplingStart']=adapter.request('dataSamplingStart',{'expressions':expressions,'sampleIntervalMs':args.period_us/1000,'sendIntervalMs':16})
        assert len(report['samplingStart']['activeExpressions'])==len(expressions),report['samplingStart']
        adapter.request('watchEvaluate',{'expressions':expressions[:4]});adapter.collect(args.seconds)
        report['watch']=adapter.request('watchEvaluate',{'expressions':expressions[:4]})
        for i,value in enumerate(report['watch']['results']):
            assert not value.get('error'),value
            if args.pointers:assert int(value['value'])>>24==9 if not i else int(value['value'])==(9<<16)|i,value
            elif args.matrix:assert int(value['value'])>>24==i,value
            else:assert int(value['value'])>0 if i==0 else int(value['value'])==[0,1,0x13579bdf][i],value
        report['samplingStop']=adapter.request('dataSamplingStop')
        rows={expression:[] for expression in expressions}
        for event in adapter.events:
            if event['event']=='ozoneDataSamples':
                for row in event['body']['snapshots']:rows[row['expression']].extend(row['data'])
        for i,expression in enumerate(expressions):
            assert rows[expression],f'No samples: {expression}'
            if args.pointers:assert all(int(point['value'])>>24==9 if not i else int(point['value'])==(9<<16)|i for point in rows[expression]),f'Pointer field identity: {expression}'
            elif args.matrix:assert all(int(point['value'])>>24==i for point in rows[expression]),f'Field identity: {expression}'
            elif i:assert all(int(point['value'])==[0,1,0x13579bdf][i] for point in rows[expression]),f'Field identity: {expression}'
        assert int(rows[expressions[0]][-1]['value'])>int(rows[expressions[0]][0]['value']),'Counter did not run'
        first=rows[expressions[0]];span=first[-1]['timestamp']-first[0]['timestamp']
        report['timeline']={'samplesPerExpression':{key:len(value) for key,value in rows.items()},
            'effectiveHz':(len(first)-1)*1000/span,'timestampSpanMs':span,
            'events':sum(event['event']=='ozoneDataSamples' for event in adapter.events)}
        report['rttOutputEvents']=sum(event['event']=='ozoneRttOutput' for event in adapter.events)
        assert report['rttOutputEvents']>0,'No RTT output in the actual adapter'
        adapter.request('pause',{'threadId':1});adapter.request('continue',{'threadId':1})
        report['pauseContinue']=True;report['complete']=True
    except BaseException as error:report['failure']=f'{type(error).__name__}: {error}';raise
    finally:
        adapter.close();report['adapterExitCode']=adapter.child.returncode
        report['requestTrace']=adapter.request_trace
        report['stderrTail']=adapter.stderr[-2000:].decode(errors='replace')
        report['eventKinds']=sorted(set(event['event'] for event in adapter.events))
        report['outputTail']=[event.get('body',{}).get('output','')[-1000:] for event in adapter.events
            if event['event']=='output'][-12:]
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
