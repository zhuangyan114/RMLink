"""F103 fixture-only timing matrix; writes reserved scratch RAM, retains Flash.

Run on the qualified baseline and on capture-path-profile.patch separately.
Pass a new JSON output path; firmware identity is recorded with each matrix.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'host'),str(root/'scripts')]
from rmlink_connector.client import ApiClient
from benchmark_pointer_subscriptions import capture

output=Path(sys.argv[1])
assert not output.exists(),output
report={'capturedAt':datetime.now().astimezone().isoformat(),'complete':False,'rows':[],
        'periodUs':1000,'secondsPerRow':5,'repetitions':3,'requestedClockHz':4000000,
        'actualClockMeasured':False,'devices':ApiClient().request('GET','/v1/devices'),
        'flashReceipt':json.loads((root/'artifacts/hardware/rmlink-last-flash.json').read_text(encoding='utf-8-sig'))}
try:
    with ApiClient().open('50787d15a924') as client:
        client.target('connect')
        assert client.target('read_memory',address=0x20000000,byte_length=4)['data']=='4b4c4d52'
        for repetition in range(3):
            for depth,shared in [(0,False),(2,True),(3,True),(3,False),(4,False)]:
                row=capture(client,depth,shared,5,1000,True)
                row['repetition']=repetition+1
                report['rows'].append(row)
                print(json.dumps(row),flush=True)
        report['complete']=True
except BaseException as error:
    report['failure']=str(error)
    raise
finally:
    output.write_text(json.dumps(report,indent=2)+'\n')
