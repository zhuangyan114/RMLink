"""Bounded progress evidence, including when a capture read never returns."""
import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import time
import uuid

from .tcp_options import probe_tcp_info
from .diagnostics import decode_stream_diagnostics


class CaptureStreamDiagnostics:
    def __init__(self, device_id, *, clock=time.perf_counter):
        self.device_id=device_id
        self.stream_id=uuid.uuid4().hex[:12]
        self.clock=clock
        self.started=self.last_read=self.last_sample=clock()
        self.bytes=self.reads=self.frames=0
        self.max_read_gap=self.max_decode=self.max_publish=self.max_loop_lag=0
        self.last_decode=self.last_publish=0
        self.pending_bytes=0
        self.subscriptions={}
        self.probe=None;self.probe_observed=None;self.probe_poll_started=None;self.probe_error=None
        self.enabled=os.environ.get('RMLINK_STREAM_DIAGNOSTICS')!='0'

    def received(self, size, frames, decode_seconds, publish_seconds, pending_bytes):
        now=self.clock()
        self.max_read_gap=max(self.max_read_gap,now-self.last_read)
        self.last_read=now
        self.bytes+=size;self.reads+=1;self.frames+=len(frames)
        self.last_decode=decode_seconds;self.last_publish=publish_seconds
        self.max_decode=max(self.max_decode,decode_seconds)
        self.max_publish=max(self.max_publish,publish_seconds)
        self.pending_bytes=pending_bytes
        if frames:self.last_sample=now
        for frame in frames:
            self.subscriptions[frame[6]]={
                'capture_id':int.from_bytes(frame[8:12],'little'),
                'sequence':int.from_bytes(frame[32:36],'little'),
                'timestamp_us':int.from_bytes(frame[36:44],'little'),
            }

    def snapshot(self):
        now=self.clock()
        return dict(stream_id=self.stream_id,bytes=self.bytes,reads=self.reads,frames=self.frames,
            elapsed_ms=(now-self.started)*1000,read_idle_ms=(now-self.last_read)*1000,
            sample_idle_ms=(now-self.last_sample)*1000,max_read_gap_ms=self.max_read_gap*1000,
            max_decode_ms=self.max_decode*1000,max_publish_ms=self.max_publish*1000,
            last_decode_ms=self.last_decode*1000,last_publish_ms=self.last_publish*1000,
            max_loop_lag_ms=self.max_loop_lag*1000,pending_bytes=self.pending_bytes,
            subscriptions={sid:dict(row) for sid,row in self.subscriptions.items()})

    def emit(self, captures, event='progress'):
        # Socket diagnostics and stderr are observers: neither may stop capture.
        try:
            sock=captures.writer.get_extra_info('socket') if captures.writer else None
            try:tcp=probe_tcp_info(sock) if sock else None
            except Exception as exc:tcp={'unavailable':type(exc).__name__}
            row=dict(event='capture_'+event,time=datetime.now(timezone.utc).isoformat(),
                device_id=self.device_id,**self.snapshot(),tcp=tcp,
                probe=self.probe,probe_error=self.probe_error,
                probe_snapshot_age_ms=None if self.probe_observed is None else (self.clock()-self.probe_observed)*1000,
                probe_poll_age_ms=None if self.probe_poll_started is None else (self.clock()-self.probe_poll_started)*1000,
                host_history_evicted=captures.host_dropped,error=captures.error,
                configured_ids=sorted(captures.configs),paused_ids=sorted(captures.paused_for_control))
            print('RMLink stream '+json.dumps(row,separators=(',',':')),file=sys.stderr,flush=True)
        except Exception:
            pass

    async def report(self, captures, *, interval=1.0, report_seconds=5.0):
        """A separate task keeps reporting while read() is blocked. No target IO."""
        last=self.clock()
        broker=getattr(getattr(captures,'target',None),'broker',None)
        poller=asyncio.create_task(self.poll_probe(broker,report_seconds)) if hasattr(broker,'submit') else None
        try:
            while True:
                before=self.clock()
                await asyncio.sleep(interval)
                now=self.clock()
                self.max_loop_lag=max(self.max_loop_lag,max(0,now-before-interval))
                if now-last>=report_seconds:
                    self.emit(captures);last=now
        finally:
            if poller:
                poller.cancel();await asyncio.gather(poller,return_exceptions=True)

    async def poll_probe(self, broker, interval):
        # A queued/broken control connection must never block progress logging.
        # These four probe-only pages do not claim ownership or renew a capture lease.
        while True:
            await asyncio.sleep(interval)
            self.probe_poll_started=self.clock()
            try:
                replies=await broker.submit('capture-diagnostics',
                    [b'\x80\x0f'+bytes([page]) for page in range(4)],'background')
                self.probe=decode_stream_diagnostics(replies)
                self.probe_observed=self.clock();self.probe_error=None
                if not self.probe['supported']:return
            except Exception as exc:
                self.probe_error=f'{type(exc).__name__}: {exc}'[:200]
            finally:self.probe_poll_started=None
