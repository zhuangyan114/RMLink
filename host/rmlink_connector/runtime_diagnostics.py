"""Low-volume event-loop lag evidence; optional stack dump for active diagnosis."""
import asyncio
from datetime import datetime
import faulthandler
import os
import sys


async def monitor_event_loop():
    stack_watchdog=os.environ.get('RMLINK_DIAGNOSTICS_WATCHDOG')=='1'
    loop=asyncio.get_running_loop()
    try:
        while True:
            if stack_watchdog:faulthandler.dump_traceback_later(1.5,file=sys.stderr)
            began=loop.time();await asyncio.sleep(.25);lag=loop.time()-began-.25
            if lag>.1:
                print(f'{datetime.now().astimezone().isoformat()} RMLink event loop lag={lag:.3f}s',file=sys.stderr,flush=True)
    finally:
        if stack_watchdog:faulthandler.cancel_dump_traceback_later()
