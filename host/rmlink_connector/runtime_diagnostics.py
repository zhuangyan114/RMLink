"""Low-volume event-loop lag evidence; optional stack dump for active diagnosis."""
import asyncio
from datetime import datetime
import faulthandler
import os
import sys


async def monitor_event_loop():
    stack_watchdog=os.environ.get('RMLINK_DIAGNOSTICS_WATCHDOG')=='1'
    stream_diagnostics=os.environ.get('RMLINK_STREAM_DIAGNOSTICS')=='1'
    loop=asyncio.get_running_loop()
    window=loop.time();maximum=0
    try:
        while True:
            if stack_watchdog:faulthandler.dump_traceback_later(1.5,file=sys.stderr)
            began=loop.time();await asyncio.sleep(.25);lag=loop.time()-began-.25
            maximum=max(maximum,lag)
            if lag>.1:
                print(f'{datetime.now().astimezone().isoformat()} RMLink event loop lag={lag:.3f}s',file=sys.stderr,flush=True)
            if stream_diagnostics and loop.time()-window>=10:
                print(f'RMLink service loop max_delay_ms={maximum*1000:.3f}',file=sys.stderr,flush=True)
                window=loop.time();maximum=0
    finally:
        if stack_watchdog:faulthandler.cancel_dump_traceback_later()
