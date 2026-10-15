import assert from 'node:assert/strict';
import test from 'node:test';
import { RMLinkClient } from '../../host/sdk/rmlink-client.mjs';

function client(handler) {
  const c=new RMLinkClient({stateDirectory:'unused'});c.session='owner';
  c.calls=[];c.request=async(method,path,body,timeout)=>{
    c.calls.push({method,path,...body,timeout});return handler?.(body,c) ?? {};
  };
  return c;
}

test('stages exact image and backend, then awaits one commit',async()=>{
  const c=client(body=>body.action==='commit'?{verified:true}:{});
  assert.equal((await c.flashImage(Buffer.alloc(8192,17),0x08000400,undefined,{backend:'probe',verifyOnHost:true})).verified,true);
  assert.deepEqual(c.calls.map(x=>x.action),['begin','chunk','chunk','commit']);
  assert.equal(c.calls[0].backend,'probe');assert.equal(c.calls[0].address,0x08000400);
  assert.equal(c.calls[0].verify_on_host,true);
  assert.equal(c.calls[2].offset,4096);assert.equal(c.calls[2].data.length,8192);
  assert.equal(c.calls.at(-1).timeout,180000);assert.equal(c.flashPending,false);
});

test('pre-aborted request does not begin or cancel another job',async()=>{
  const c=client(),controller=new AbortController();controller.abort();
  await assert.rejects(c.flashImage(Buffer.alloc(1024),undefined,controller.signal),{name:'AbortError'});
  assert.equal(c.calls.length,0);assert.equal(c.flashPending,false);
});

test('abort during upload sends one cancellation and never commits',async()=>{
  const controller=new AbortController();
  const c=client(body=>{if(body.action==='chunk')controller.abort();return {};});
  await assert.rejects(c.flashImage(Buffer.alloc(8192),undefined,controller.signal),{name:'AbortError'});
  assert.deepEqual(c.calls.map(x=>x.action),['begin','chunk','cancel']);
  assert.equal(c.flashPending,false);
});

test('abort during programming keeps commit response and its target evidence',async()=>{
  const controller=new AbortController();let finish;
  const c=client(body=>{
    if(body.action==='commit')return new Promise((resolve,reject)=>{finish=reject;queueMicrotask(()=>controller.abort());});
    if(body.action==='cancel'){
      const error=new Error('Flash cancelled');error.details={progress:{phase:'cancelled',programmed_bytes:1024,halt_confirmed:true}};
      finish(error);return {cancel_requested:true};
    }
    return {};
  });
  await assert.rejects(c.flashImage(Buffer.alloc(1024),undefined,controller.signal),error=>error.details.progress.halt_confirmed);
  assert.equal(c.calls.filter(x=>x.action==='commit').length,1);
  assert.equal(c.calls.filter(x=>x.action==='cancel').length,1);assert.equal(c.flashPending,false);
});

test('completion can win cancellation without replacing verified result',async()=>{
  const controller=new AbortController();
  const c=client(body=>{
    if(body.action==='commit'){controller.abort();return {verified:true,phase:'complete'};}
    if(body.action==='cancel')return {cancel_requested:false,already_terminal:true};
    return {};
  });
  assert.equal((await c.flashImage(Buffer.alloc(1024),undefined,controller.signal)).phase,'complete');
  assert.equal(c.calls.filter(x=>x.action==='cancel').length,1);
});

test('parallel SDK upload cannot replace the current staged image',async()=>{
  let release;const c=client(body=>body.action==='begin'?new Promise(resolve=>{release=resolve;}):{});
  const pending=c.flashImage(Buffer.alloc(1024));
  await assert.rejects(c.flashImage(Buffer.alloc(1024)),/already running/);
  release({});await pending;assert.equal(c.calls.filter(x=>x.action==='begin').length,1);
});

test('cancel transport failure is retained without replay or unhandled rejection',async()=>{
  const controller=new AbortController();
  const c=client(body=>{
    if(body.action==='commit'){controller.abort();throw new Error('Commit connection lost');}
    if(body.action==='cancel')throw new Error('Cancel connection lost');
    return {};
  });
  await assert.rejects(c.flashImage(Buffer.alloc(1024),undefined,controller.signal),error=>
    error.message==='Commit connection lost' && error.cancelRequestError.message==='Cancel connection lost');
  assert.equal(c.calls.filter(x=>x.action==='commit').length,1);
});
