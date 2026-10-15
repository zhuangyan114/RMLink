// Explicit real-target acceptance through the public SDK, not raw DAP.
import assert from 'node:assert/strict';
import { readFile,writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { RMLinkClient } from '../host/sdk/rmlink-client.mjs';

const args=process.argv.slice(2),output=args[args.indexOf('--output')+1];
if(!args.includes('--allow-target-write') || !args.includes('--output') || !output)
  throw new Error('Use --allow-target-write --output <new-evidence.json>');
try {await readFile(output);throw new Error('Preserve existing evidence');}catch(error){if(error.code!=='ENOENT')throw error;}
const image=await readFile(new URL('../artifacts/build/stm32-fixture/fixture-64k.bin',import.meta.url));
const sha=createHash('sha256').update(image).digest('hex');
assert.equal(sha,'2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4');
const report={capturedAt:new Date().toISOString(),complete:false,fixtureSha256:sha};
const c=new RMLinkClient();
try {
  const status=await c.init(),device=status.devices.find(d=>d.device_id==='50787d15a924');
  assert(device?.connected && device.owner===null);await c.open(device.device_id);
  report.target=await c.target('connect',{clock_hz:4000000});
  const path=`/sessions/${c.session}/flash`,controller=new AbortController();
  let settled=false;
  // Attach rejection handling immediately; polling runs while COMMIT is active.
  const job=c.flashImage(image,0x08000000,controller.signal,{backend:'probe'}).then(
    value=>{settled=true;return {value};},error=>{settled=true;return {error};});
  const deadline=Date.now()+15000;
  while(!settled) {
    const progress=await c.request('GET',path);
    if(progress.erased_pages>=1 && progress.busy){report.beforeCancel=progress;controller.abort();break;}
    if(Date.now()>deadline)throw new Error('Did not reach erased Flash before deadline');
    await delay(10);
  }
  const result=await job;
  assert(result.error,'Cancel must reach an active job in this acceptance case');
  report.cancelError={code:result.error.errorCode,status:result.error.httpStatus,details:result.error.details};
  assert.equal(result.error.errorCode,'FlashCancelled');assert.equal(result.error.httpStatus,409);
  report.cancelled=await c.request('GET',path);
  assert.equal(report.cancelled.phase,'cancelled');assert.equal(report.cancelled.busy,false);
  assert.equal(report.cancelled.halt_confirmed,true);
  assert.equal((await c.target('state')).state,'Halted');
  // A new public SDK operation restores the chosen fixture, never the old image.
  report.recovery=await c.flashImage(image,0x08000000,undefined,{backend:'probe'});
  assert.equal(report.recovery.verified,true);assert.equal(report.recovery.sha256,sha);
  report.lateCancel=await c.request('POST',path,{action:'cancel'});
  assert.equal(report.lateCancel.cancel_requested,false);assert.equal(report.lateCancel.already_terminal,true);
  assert.equal((await c.request('GET',path)).phase,'complete');
  report.finalState=await c.target('state');assert.equal(report.finalState.state,'Running');
  report.complete=true;
}catch(error){report.failure={message:error.message,code:error.errorCode,details:error.details};throw error;}
finally{await c.close();await writeFile(output,JSON.stringify(report,null,2)+'\n');}
console.log(JSON.stringify({complete:report.complete,output}));
