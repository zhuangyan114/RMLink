"""Seal existing qualified evidence; never runs or modifies a physical target."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8-sig'))
    def identity(name):
        path=ROOT/name
        return dict(path=name,bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    names=['flash-default-five-0217.json','flash-extra-host-0217.json','flash-faults-usb-0217.json',
           'flash-disconnect-service-stopped-0217.json','node-sdk-cancel-028-0217.json','c-sdk-flash-028-0217.json',
           'accept-virtual-usb-0217.json','pyocd-virtual-0217.json','subscriptions-wifi-0217.json',
           'subscriptions-usb-0217.json','probe-diagnostics-clock-0217.json','probe-diagnostics-after-orbit-0217.json',
           'pointer-matrix-0217.json','orbit-flash-sdk028-0217.json','orbit-flash-matrix-status-sdk028-0217.json',
           'flash-resubscribe-sdk028-final-0217.json','soak-clock-0217-3600.json',
           'mixed-5ptr3-5direct-1k-5k-0217.json','mixed-5ptr3-5direct-5k-600.json']
    results={name:read('artifacts/logs/'+name) for name in names}
    for name,result in results.items():
        if result.get('complete') is not True:raise RuntimeError('Acceptance not complete: '+name)
    receipt=read('artifacts/hardware/rmlink-last-flash.json')
    firmware='artifacts/build/'+receipt['profile']
    binary=identity(firmware+'/rmlink.bin');config=identity(firmware+'/sdkconfig')
    if binary['sha256']!=receipt['binarySha256'].lower() or config['sha256']!=receipt['sdkconfigSha256'].lower():
        raise RuntimeError('Selected build differs from the physically flashed image')
    description=read(firmware+'/project_description.json')
    artifacts=[binary,config,identity(firmware+'/rmlink.elf'),identity('artifacts/hardware/rmlink-last-flash.json'),
               identity('artifacts/build/connector/dist/RMLinkConnector.exe'),identity('artifacts/build/sdk/rmlink.dll'),
               identity('artifacts/build/sdk/rmlink-client-0.2.8.tgz'),identity('firmware/dependencies.lock')]
    hour=results['soak-clock-0217-3600.json'];mixed=results['mixed-5ptr3-5direct-5k-600.json']
    for result in (hour,mixed):
        if result['consumerGap'] or result['live']['firstInvalidSample'] is not None:raise RuntimeError('Live stream validation failed')
        if any(s['failures'] or s['ring_dropped'] for s in result['finalStatuses'].values()):raise RuntimeError('Board capture loss')
        if not all(result['liveDynamicFieldChanges']['1']):raise RuntimeError('Frozen dynamic leaf')
    def soak(result):return dict(seconds=result['elapsedSeconds'],effectiveHz=result['live']['kinds']['1']['effectiveHz'],
        dynamicFieldChanges=result['liveDynamicFieldChanges']['1'],frames=result['recording']['frames'],
        recordingSha256=result['recording']['sha256'],invalidDataSamples=result['recording']['invalid_data_samples'],
        budgetOverrunSamples=result['recording']['budget_overrun_samples'],healthAfter=result['healthAfter'])
    data=dict(capturedAt=datetime.now().astimezone().isoformat(),
        sourceCommit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip(),
        firmwareVersion=description['project_version'],connectorVersion='0.2.8',sdkVersion='0.2.8',
        profile=receipt['profile'],requestedSwjClockHz=4000000,
        fixtureSha256='2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4',
        internalClockEvidence=identity('artifacts/logs/probe-diagnostics-clock-0217.json'),externalInstrument=False,
        flashDefaultMedians=results['flash-default-five-0217.json']['medians'],
        flashExtraHostSingleMs=results['flash-extra-host-0217.json']['rows'][0]['totalMs'],
        openOcdSingleMs=results['accept-virtual-usb-0217.json']['openocdProgram']['elapsedMs'],
        independentDepthFourHour=soak(hour),mixedIndependentDepthThree5kTenMinutes=soak(mixed),
        mixedShortMedians=results['mixed-5ptr3-5direct-1k-5k-0217.json']['medians'],
        artifacts=artifacts,evidence=[dict(identity('artifacts/logs/'+name),complete=True) for name in names],
        validation=dict(hostPythonTests=106,nodeSdkTests=7,captureAndFlashCModels=True,
            firmwareBuilds=['TCP64/USB64 CPU0 PSRAM','TCP512/USB64 CPU0 PSRAM'],
            orbitIsolatedTestsPassed=1055,orbitSkipped=1,orbitInstalledUi=False),
        orbitImplementationCommit=subprocess.check_output(['git','-C',str(ROOT/'artifacts/worktrees/orbit-rmlink'),'rev-parse','HEAD']).decode().strip(),
        boundaries=['F103C8 enhanced Flash page images only','Physical NRST missing',
            'Keil/IAR and formal Microsoft driver signing pending','External analog timing not measured',
            '5000 Hz is a requested period; skipped deadlines are retained',
            '0.2.13 one partial frame has no confirmed root cause; final tests did not reproduce it'],
        package='artifacts/packages/RMLink-0.2.8-windows-x64-dev.zip')
    output=ROOT/'docs/flash-subscription-checkpoint-028.json'
    output.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8');print(output)


if __name__=='__main__':main()
