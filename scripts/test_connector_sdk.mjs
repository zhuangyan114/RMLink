// Physical read-only SDK check through the same API used by a future Orbit backend.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { RMLinkClient } from '../host/sdk/rmlink-client.mjs';

const client = new RMLinkClient();
const report = { capturedAt: new Date().toISOString(), complete: false, sdk: 'Node local API' };
try {
  const status = await client.init();
  const online = status.devices.filter(d => d.paired && d.connected);
  assert.equal(online.length, 1, 'Select exactly one online development device');
  report.deviceId = online[0].device_id;
  await client.open(report.deviceId);
  report.packetSize = client.packetSize;
  const sizeReply = (await client.exchange([Buffer.from([0, 0xff])]))[0];
  assert.equal(sizeReply.readUInt16LE(2), client.packetSize);
  const full = Buffer.alloc(client.packetSize); full.set([0x80,7,17,0,0,0]);
  const large = await client.exchange(Array.from({length:8}, () => full), 'background');
  const expected = Buffer.alloc(client.packetSize); expected.set([0x80,7,0,17,0,0,0]);
  for (let i=7;i<expected.length;i++) expected[i]=(17+(i-7)*31)&255;
  assert(large.every(r=>r.equals(expected)));
  report.fullPacketsValidated=large.length;
  assert.equal((await client.exchange([Buffer.from([2, 1])]))[0].toString('hex'), '0201');
  const responses = await client.exchange(Array.from({ length: 8 }, () => Buffer.from([0, 4])), 'watch');
  assert(responses.every(r => r[0] === 0 && r[1] > 0 && r.equals(responses[0])));
  report.dapProtocolVersion = responses[0].subarray(2, 2 + responses[0][1]).toString().replace(/\0/g, '');
  report.firmwareVersion = online[0].firmware;
  const identity = (await client.exchange([Buffer.from([0x80, 9])], 'background'))[0];
  assert.equal(identity.toString('hex'), '800900' + report.deviceId);
  assert.equal((await client.exchange([Buffer.from([3])]))[0].toString('hex'), '0300');
  report.complete = true;
} finally {
  await client.close();
  if (process.argv[2]) await writeFile(process.argv[2], JSON.stringify(report, null, 2) + '\n');
}
console.log(JSON.stringify(report, null, 2));
