/** Local Orbit integration foundation. One instance per physical target owner.
 * Keep Orbit's SessionTargetSelector/NativeScheduler; route CMSIS-DAP through
 * this client instead of creating a second physical probe connection.
 */
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

export class RMLinkClient {
  constructor({ port = 3243, stateDirectory = join(process.env.USERPROFILE, '.rmlink'), onLost = () => {} } = {}) {
    this.base = `http://127.0.0.1:${port}/v1`;
    this.stateDirectory = stateDirectory;
    this.onLost = onLost;
    this.session = null;
    this.timer = null;
    this.packetSize = 64;
  }
  async request(method, path, body) {
    const response = await fetch(this.base + path, {
      method, headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || `RMLink HTTP ${response.status}`);
      error.ownerLost = response.status === 503;
      throw error;
    }
    return result;
  }
  async init() {
    this.token = JSON.parse(await readFile(join(this.stateDirectory, 'connector.json'), 'utf8')).token;
    return this.request('GET', '/status');
  }
  async open(deviceId) {
    if (this.session) throw new Error('RMLink session already open');
    const result = await this.request('POST', '/sessions', { device_id: deviceId });
    this.session = result.session_id;
    this.packetSize = result.packet_size ?? 64;
    if (![64,128,256,512].includes(this.packetSize)) {
      await this.close(); throw new Error('Invalid negotiated RMLink packet size');
    }
    const session = this.session;
    this.timer = setInterval(() => {
      this.request('POST', `/sessions/${this.session}/heartbeat`).catch(error => {
        if (this.session === session) {
          clearInterval(this.timer); this.timer = null; this.session = null; this.onLost(error);
        }
      });
    }, result.heartbeat_seconds * 1000);
    this.timer.unref?.();
  }
  async exchange(packets, priority = 'control') {
    if (!this.session) throw new Error('RMLink owner lost or session closed');
    if (!Array.isArray(packets) || packets.length < 1 || packets.length > 8 ||
        packets.some(p => p.length < 1 || p.length > this.packetSize || p[0] === 7)) throw new Error('Invalid DAP batch');
    let result;
    const session = this.session;
    try {
      result = await this.request('POST', `/sessions/${session}/dap`, {
        packets: packets.map(p => Buffer.from(p).toString('hex')), priority,
      });
    } catch (error) {
      if (error.ownerLost && this.session === session) {
        clearInterval(this.timer); this.timer = null; this.session = null; this.onLost(error);
      }
      throw error;
    }
    if (!Array.isArray(result.responses) || result.responses.length !== packets.length ||
        result.responses.some(r => !new RegExp(`^(?:[0-9a-f]{2}){1,${this.packetSize}}$`).test(r))) throw new Error('Malformed RMLink response');
    return result.responses.map(r => Buffer.from(r, 'hex'));
  }
  async abort() { return this.request('POST', `/sessions/${this.session}/abort`); }
  async close() {
    clearInterval(this.timer); this.timer = null;
    const session = this.session; this.session = null;
    if (session) await this.request('DELETE', `/sessions/${session}`);
  }
}
