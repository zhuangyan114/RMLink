export interface CaptureSample {
  subscriptionId:number; kind:number; captureId:number; revision:number; sequence:number;
  timestampUs:bigint; durationUs:number; validBitmap:number; flags:number;
  values?:number[]; channel?:number; byteSequence?:bigint; data?:Uint8Array;
}
export interface SubscriptionConfig {
  addresses?:number[]; fields?:Array<{address:number;offsets?:number[];byte_length?:1|2|4}>;
  period_us?:number; sample_limit?:number; control_block_address?:number; channel?:number;
}
export function decodeCaptureFrame(frame:Uint8Array):CaptureSample;
export class RMLinkClient {
  constructor(options?:{port?:number;stateDirectory?:string;onLost?:(error:Error)=>void});
  session:string|null;
  init():Promise<unknown>;
  request<T=unknown>(method:string,path:string,body?:unknown,timeoutMs?:number):Promise<T>;
  open(deviceId:string):Promise<void>;
  target<T=Record<string,unknown>>(command:string,params?:Record<string,unknown>):Promise<T>;
  flashImage(data:Uint8Array,address?:number,signal?:AbortSignal):Promise<Record<string,unknown>>;
  subscribe(kind:'memory_series'|'memory_snapshot'|'rtt_stream',config?:SubscriptionConfig):Promise<{subscription_id:number;capture_id:number}>;
  subscription(id:number,action?:'status'|'stop'|'pause'|'resume'):Promise<Record<string,unknown>>;
  unsubscribe(id:number):Promise<unknown>;
  stream(options?:{signal?:AbortSignal}):AsyncGenerator<{cursor:bigint;frame:Uint8Array;sample:CaptureSample}>;
  abort():Promise<unknown>;
  timeSync(deviceId:string,samples?:number):Promise<Record<string,unknown>>;
  setTransport(deviceId:string,transport:'usb'|'wifi'):Promise<Record<string,unknown>>;
  close():Promise<void>;
}
