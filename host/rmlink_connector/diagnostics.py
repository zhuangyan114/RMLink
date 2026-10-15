"""Strict decoding of probe-only health snapshots and raw RMT clock evidence."""
import statistics
import struct


def decode_health(reply):
    if len(reply)>=3 and reply[:2]==b'\x80\x0a' and reply[2]:return {'supported':False}
    if len(reply)!=64 or reply[:4]!=b'\x80\x0a\0\x01':raise ValueError('Malformed runtime health snapshot')
    values=struct.unpack_from('<6I',reply,4)
    memory={name:dict(zip(('free_bytes','largest_block_bytes','minimum_free_bytes'),values[i*3:i*3+3]))
            for i,name in enumerate(('internal','psram'))}
    count,at,elapsed,size,requested,completed,status,abort,write,reserved=struct.unpack_from('<IQIH6B',reply,40)
    if reserved or write>1 or abort>2:raise ValueError('Malformed SWD failure diagnostics')
    failure=None if not count else dict(count=count,at_uptime_us=at,elapsed_us=elapsed,response_bytes=size,
        requested_operations=requested,completed_operations=completed,dap_status=status,
        abort_reason={0:'none',1:'explicit_abort',2:'wall_clock_deadline'}[abort],target_write=bool(write))
    return dict(supported=True,memory=memory,requested_swj_clock_hz=int.from_bytes(reply[28:32],'little'),
                snapshot_uptime_us=int.from_bytes(reply[32:40],'little'),last_capture_io_failure=failure)


def decode_clock_header(data):
    if len(data)!=26:raise ValueError('Clock trace header length')
    version,mode,count,reserved,identity,resolution,requested,delay,value,fast,ack=struct.unpack('<BBBBIIIIIBB',data)
    if version!=1 or mode>1 or not 4<=count<=64 or reserved or not identity or resolution!=80000000 or fast>1:
        raise ValueError('Clock trace header fields')
    return dict(trace_id=identity,mode='sequence' if mode==0 else 'dp_read',symbol_count=count,
                reference_hz=resolution,requested_clock_hz=requested,delay_iterations=delay,
                fast_clock=bool(fast),dap_status=ack,dp_idcode=value if mode else None)


def summarize_clock(header,words):
    symbols=[dict(duration0_ticks=w&0x7fff,level0=(w>>15)&1,duration1_ticks=(w>>16)&0x7fff,level1=w>>31) for w in words]
    result={**header,'method':'ESP32-S3 RMT input on existing SWCLK pad',
            'external_instrument':False,'raw_symbols':words,'symbols':symbols,'measurement_valid':False}
    # First/last symbols may contain arm/stop idle time. Keep them in evidence,
    # while deriving bit periods only from complete interior high/low pairs.
    periods=[s['duration0_ticks']+s['duration1_ticks'] for s in symbols[1:-1]
             if s['duration0_ticks'] and s['duration1_ticks'] and s['level0']!=s['level1']]
    if len(words)!=header['symbol_count'] or len(periods)<4:
        return dict(result,error='Insufficient complete interior clock cycles')
    periods.sort();median=statistics.median(periods)
    result.update(measurement_valid=True,complete_cycles=len(periods),reference_tick_ns=1e9/header['reference_hz'],
                  median_clock_hz=header['reference_hz']/median,
                  period_ticks={'minimum':periods[0],'median':median,'p95':periods[int((len(periods)-1)*.95)],'maximum':periods[-1]},
                  timing_scope='digital pulse periods in this diagnostic; clock stretching retained; analog signal quality excluded')
    return result
