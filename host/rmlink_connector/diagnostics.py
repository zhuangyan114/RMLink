"""Strict decoding of probe-only health snapshots and raw RMT clock evidence."""
import statistics
import struct


def decode_stream_diagnostics(replies):
    if len(replies)!=4:raise ValueError('Stream diagnostic page count')
    if len(replies[0])>=3 and replies[0][:2]==b'\x80\x0f' and replies[0][2]:
        return {'supported':False}
    for page,reply in enumerate(replies):
        if len(reply)!=63 or reply[:5]!=b'\x80\x0f\0\x01'+bytes([page]) or reply[5]&~15 or reply[6]:
            raise ValueError('Malformed stream diagnostic page')
    generation,batches,calls,partial,errors,last_errno,at,dequeued,sent,wire=struct.unpack_from('<6I4Q',replies[0],7)
    timings=struct.unpack_from('<10I2Q',replies[1],7)
    tcp=struct.unpack_from('<12IQ',replies[2],7)
    ring=struct.unpack_from('<14I',replies[3],7)
    flags=replies[0][5]
    if tcp[1]>1 or ring[12]>1:raise ValueError('Malformed stream diagnostic flags')
    result=dict(supported=True,stream_id=generation,active=bool(flags&1),
        tcp_diagnostics_enabled=bool(flags&2),send_in_progress=bool(flags&4),network_wait_in_progress=bool(flags&8),
        batches=batches,send_calls=calls,partial_sends=partial,send_errors=errors,last_errno=last_errno,
        snapshot_uptime_us=at,last_dequeued_uptime_us=dequeued,last_send_completed_uptime_us=sent,wire_bytes=wire)
    result.update(zip(('last_send_us','max_send_us','blocked_send_us','send_stalls_over_50ms',
        'network_wait_us','max_network_wait_us','last_encode_us','max_encode_us','max_wake_us','last_batch_bytes',
        'raw_bytes','tcp_request_uptime_us'),timings))
    result['tcp']=dict(zip(('stream_id','available','rto_ms','retries','duplicate_acks','cwnd_bytes','window_bytes',
        'send_buffer_free_bytes','unacked_segments','unsent_segments','inflight_bytes','queued_bytes','snapshot_uptime_us'),tcp))
    result['tcp']['available']=bool(tcp[1]) and tcp[0]==generation
    result['tcp']['same_stream']=tcp[0]==generation
    # NOSPLIT's GetCurFreeSize reports the largest admissible item (at most
    # half a ring), not total free storage. Do not mislabel an empty ring as half full.
    result['buffers']=[dict(subscription_id=i+1,capacity_bytes=ring[2*i],max_item_bytes=ring[2*i+1]) for i in range(4)]
    result.update(internal_free_bytes=ring[8],internal_minimum_free_bytes=ring[9],
        rssi_dbm=ring[10] if ring[10]<2**31 else ring[10]-2**32,channel=ring[11],
        network_up=bool(ring[12]),tcp_callback_wait_us=ring[13])
    return result


def decode_fault_recovery(reply):
    if len(reply)>=3 and reply[:2]==b'\x80\x0d' and reply[2]:return {'supported':False}
    if len(reply)!=32 or reply[:4]!=b'\x80\x0d\0\x01':raise ValueError('Malformed read fault recovery snapshot')
    attempts,recovered,exhausted,writes,status,at=struct.unpack_from('<5IQ',reply,4)
    return dict(supported=True,attempts=attempts,recovered=recovered,exhausted=exhausted,
        write_faults_not_replayed=writes,last_dp_ctrl_stat=status,last_fault_uptime_us=at)


def decode_read_recovery(reply):
    if len(reply)>=3 and reply[:2]==b'\x80\x0c' and reply[2]:return {'supported':False}
    if len(reply)!=24 or reply[:4]!=b'\x80\x0c\0\x01':raise ValueError('Malformed read recovery snapshot')
    attempts,recovered,exhausted,at=struct.unpack_from('<3IQ',reply,4)
    return dict(supported=True,attempts=attempts,recovered=recovered,exhausted=exhausted,last_retry_uptime_us=at)


def decode_performance(reply):
    if len(reply)>=3 and reply[:2]==b'\x80\x0b' and reply[2]:return {'supported':False}
    if len(reply)!=63 or reply[:3]!=b'\x80\x0b\0' or reply[3] not in (1,2):raise ValueError('Malformed performance snapshot')
    if reply[3]==2 and reply[60]>1:raise ValueError('Malformed performance flags')
    calls,waits,errors,cycles,clocks,commands,io_us,words,frames,maximum=struct.unpack_from('<3I2QIQ3I',reply,4)
    return dict(supported=True,swd_metrics_active=reply[3]==1 or bool(reply[60]),
        swd_transfers=calls,swd_waits=waits,swd_errors=errors,
        swd_cpu_cycles=cycles,swd_wire_clocks=clocks,capture_dap_commands=commands,
        capture_dap_us=io_us,capture_words=words,capture_frames=frames,max_capture_dap_us=maximum,
        cpu_hz=int.from_bytes(reply[56:60],'little'))


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
