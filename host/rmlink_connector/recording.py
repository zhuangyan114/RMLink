"""Bounded service-side raw recording. Strict gaps are terminal, never hidden."""
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import uuid
from .capture_protocol import decode_record
from . import __version__


class Recording:
    def __init__(self,directory,device_id,configs,on_failure,strict=True,queue_capacity=2048):
        self.id=uuid.uuid4().hex;self.path=Path(directory)/(self.id+'.rmlrec')
        self.configs=json.loads(json.dumps(configs));self.on_failure=on_failure;self.strict=strict
        self.queue=asyncio.Queue(maxsize=queue_capacity);self.changed=asyncio.Event()
        self.task=None;self.failure_task=None;self.file=None;self.accepting=False;self.stopping=False;self.footer_offset=None
        self.last_sequence={};self.last_byte={};self.hash=hashlib.sha256()
        self.progress={'recording_id':self.id,'path':str(self.path),'strict':strict,'phase':'starting',
            'frames':0,'bytes':0,'gaps':0,'invalid_samples':0,
            'invalid_data_samples':0,'budget_overrun_samples':0,'error':None,'finalized':False}
        self.metadata={'version':1,'connector_version':__version__,'device_id':device_id,'created_at':datetime.now().astimezone().isoformat(),
            'strict':strict,'starts_mid_capture':True,'configs':self.configs}

    async def start(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('xb')
        metadata=json.dumps(self.metadata,separators=(',',':')).encode()
        try:await asyncio.to_thread(self.write,b'RMLR'+struct.pack('<I',len(metadata))+metadata)
        except BaseException:self.file.close();raise
        self.accepting=True;self.progress['phase']='recording';self.task=asyncio.create_task(self.writer())
        return self.status()

    def status(self):return dict(self.progress,queued_frames=self.queue.qsize())

    def fail(self,reason):
        if self.progress['error'] is not None:return
        self.progress.update(error=reason,phase='failed');self.accepting=False;self.stopping=True;self.changed.set()
        async def pause():
            try:await self.on_failure()
            except Exception as error:self.progress['pause_error']=str(error)
        self.failure_task=asyncio.create_task(pause())

    def offer(self,cursor,frame):
        if not self.accepting:return
        sample=decode_record(frame);sid=sample['subscription_id'];config=self.configs.get(str(sid))
        if config is None or sample['capture_id']!=config['capture_id']:return
        sequence=sample['sequence'];previous=self.last_sequence.get(sid)
        gap=previous is not None and sequence!=((previous+1)&0xffffffff)
        invalid=False
        if sample['kind']!=3:
            count=len(config['addresses'])
            bad_data=sample['valid_bitmap']!=(1<<count)-1 or bool(sample['flags']&1)
            budget=bool(sample['flags']&2)
            self.progress['invalid_data_samples']+=int(bad_data)
            self.progress['budget_overrun_samples']+=int(budget)
            # Keep the historical strict-mode aggregate for existing clients.
            invalid=bad_data or budget
        else:
            begin=sample['byte_sequence'];expected=self.last_byte.get(sid)
            gap=gap or expected is not None and begin!=expected
            self.last_byte[sid]=begin+len(sample['data'])
        self.last_sequence[sid]=sequence
        self.progress['gaps']+=int(gap);self.progress['invalid_samples']+=int(invalid)
        if self.strict and (gap or invalid):self.fail('sequence_gap' if gap else 'invalid_sample');return
        try:self.queue.put_nowait((cursor,bytes(frame)));self.changed.set()
        except asyncio.QueueFull:self.fail('recording_queue_overflow')

    def write(self,data):
        if self.file.write(data)!=len(data):raise OSError('Short recording write')
        self.hash.update(data)

    async def writer(self):
        try:
            while True:
                self.changed.clear();batch=[]
                while len(batch)<128:
                    try:batch.append(self.queue.get_nowait())
                    except asyncio.QueueEmpty:break
                if batch:
                    payload=b''.join(struct.pack('<QI',cursor,len(frame))+frame for cursor,frame in batch)
                    await asyncio.to_thread(self.write,payload)
                    self.progress['frames']+=len(batch);self.progress['bytes']+=len(payload)
                    continue
                if self.stopping:break
                await self.changed.wait()
            self.progress['phase']='failed' if self.progress['error'] else 'complete'
            if self.failure_task:await self.failure_task
            summary=json.dumps(dict(self.status(),finalized=True,window_end='last_received_record'),separators=(',',':')).encode()
            self.footer_offset=self.file.tell()
            await asyncio.to_thread(self.write,struct.pack('<QI',0,0)+struct.pack('<I',len(summary))+summary)
            await asyncio.to_thread(self.file.flush);await asyncio.to_thread(os.fsync,self.file.fileno())
            self.progress.update(finalized=True,sha256=self.hash.hexdigest())
        except Exception as error:
            self.fail('storage_error: '+str(error));self.progress.update(finalized=False,storage_error=str(error))
            if self.footer_offset is not None:
                try:await asyncio.to_thread(self.file.truncate,self.footer_offset)
                except Exception as invalidation:self.progress['invalidation_error']=str(invalidation)
        finally:
            self.accepting=False;self.file.close()

    async def stop(self):
        self.accepting=False;self.stopping=True;self.changed.set()
        if self.task:await self.task
        if self.failure_task:await self.failure_task
        return self.status()
