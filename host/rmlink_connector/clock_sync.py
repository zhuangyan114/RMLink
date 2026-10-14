"""Bounded probe/host clock mapping; delay asymmetry remains in uncertainty."""
import secrets
import struct
import time
import uuid
import asyncio


class ClockSync:
    def __init__(self):
        self.epochs={}
        self.last_probe={}
        self.locks={}

    async def measure(self,broker,samples=8):
        if type(samples) is not int or not 1<=samples<=16:raise ValueError('Clock sync samples must be 1..16')
        async with self.locks.setdefault(broker.device_id,asyncio.Lock()):
            return await self._measure(broker,samples)

    async def _measure(self,broker,samples):
        rows=[];previous=None
        for _ in range(samples):
            token=secrets.token_bytes(8)
            wall0=time.time_ns();host0=time.perf_counter_ns()
            reply=(await broker.submit('clock-sync',[b'\x81\x0d'+token],'background'))[0]
            host3=time.perf_counter_ns();wall3=time.time_ns()
            if len(reply)!=27 or reply[:3]!=b'\x81\x0d\x00' or reply[3:11]!=token:
                raise ConnectionError('TIME_SYNC reply invalid or firmware unsupported')
            probe1,probe2=struct.unpack('<QQ',reply[11:])
            if probe2<probe1 or previous is not None and probe1<previous:
                raise ConnectionError('Probe clock changed within measurement; measure again')
            previous=probe2
            residual=host3-host0-(probe2-probe1)*1000
            if residual<0:raise ConnectionError('Probe processing interval exceeds host round trip')
            probe=(probe1+probe2)//2;host=(host0+host3)//2
            rows.append({'probe_us':probe,'host_monotonic_ns':str(host),
                'host_wall_time_ns':str((wall0+wall3)//2) if abs((wall3-wall0)-(host3-host0))<5000000 else None,
                'offset_ns':str(host-probe*1000),'round_trip_ns':host3-host0,
                'uncertainty_ns':(residual+1)//2+1000}) # esp_timer quantization
        key=broker.device_id
        if key not in self.epochs or rows[0]['probe_us']<self.last_probe.get(key,0):self.epochs[key]=uuid.uuid4().hex
        self.last_probe[key]=rows[-1]['probe_us']
        best=min(rows,key=lambda r:r['uncertainty_ns'])
        return {'device_id':key,'clock_epoch':self.epochs[key],'host_clock':'perf_counter',
            'probe_clock':'esp_timer_us','samples':samples,'mapping':best,'measurements':rows,
            'one_way_delay_measured':False,'drift_compensated':False}
