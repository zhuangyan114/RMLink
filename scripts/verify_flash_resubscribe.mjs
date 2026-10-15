// Real public SDK: preserve a local binary-stream cursor across Flash COMMIT.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {RMLinkClient} from '../host/sdk/rmlink-client.mjs';

const args=process.argv.slice(2),output=args[args.indexOf('--output')+1];
if(!args.includes('--allow-target-write') || !args.includes('--output') || !output)throw new Error('Use --allow-target-write --output <new.json>');
try{await readFile(output);throw new Error('Preserve evidence');}catch(error){if(error.code!=='ENOENT')throw error;}
const image=await readFile(new URL('../artifacts/build/stm32-fixture/fixture-64k.bin',import.meta.url));
assert.equal(createHash('sha256').update(image).digest('hex'),'2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4');
const report={capturedAt:new Date().toISOString(),complete:false},client=new RMLinkClient(),controllers=[];
const config={addresses:Array.from({length:10},(_,i)=>0x20000100+i*64),period_us:1000};
async function stop(stream,controller){
  controller.abort();
  try{await stream.return();}catch(error){if(error.name!=='AbortError')throw error;}
}
try{
  report.service=await client.init();await client.open('50787d15a924');await client.target('connect');
  report.before=await client.subscribe('memory_series',config);
  const first=new AbortController();controllers.push(first);
  const stream=client.stream({signal:AbortSignal.any([first.signal,AbortSignal.timeout(5000)])});
  let last;
  for(let i=0;i<100;i++){const row=await stream.next();assert(!row.done);last=row.value;}
  report.lastCursorBefore=last.cursor;await stop(stream,first);
  report.flash=await client.flashImage(image,0x08000000,undefined,{backend:'probe'});
  assert(report.flash.verified);report.after=await client.subscribe('memory_series',config);
  assert(BigInt(report.after.stream_cursor)>=last.cursor,'Local cursor rolled back across Flash');
  assert.notEqual(report.after.capture_id,report.before.capture_id);
  const second=new AbortController();controllers.push(second);
  const resumed=client.stream({signal:AbortSignal.any([second.signal,AbortSignal.timeout(5000)])});
  const row=await resumed.next();assert(!row.done);
  report.firstAfter={cursor:row.value.cursor,captureId:row.value.sample.captureId,values:row.value.sample.values};
  assert(row.value.cursor>last.cursor);assert.equal(row.value.sample.captureId,report.after.capture_id);
  assert.equal(row.value.sample.validBitmap,1023);assert.equal(row.value.sample.flags&1,0);
  for(let i=0;i<10;i++)assert.equal(row.value.sample.values[i]>>>24,i);
  await stop(resumed,second);await client.unsubscribe(report.after.subscription_id);
  report.state=await client.target('state');assert.equal(report.state.state,'Running');report.complete=true;
}catch(error){report.failure={message:error.message,code:error.errorCode};throw error;}
finally{
  for(const controller of controllers)controller.abort();await client.close();
  await writeFile(output,JSON.stringify(report,(_,value)=>typeof value==='bigint'?value.toString():value,2)+'\n');
}
console.log(JSON.stringify({complete:report.complete,output}));
