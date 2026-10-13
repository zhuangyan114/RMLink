param(
    [string]$Port = 'COM13',
    [string]$DeviceHost = 'rmlink-15a924.local',
    [int]$Cycles = 3,
    [string]$PythonPath = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
if ($Cycles -lt 1 -or $Cycles -gt 10) { throw 'Cycles must be 1..10.' }
$root = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $root 'artifacts\logs'
$env:PYTHONPYCACHEPREFIX = Join-Path $root 'artifacts\cache\python'
[System.IO.Directory]::CreateDirectory($logs) | Out-Null
function Get-RmlinkNativeDevices {
    @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.InstanceId -match '^USB\\VID_303A&PID_1002\\50787D15A924$'
    } | Select-Object Status,FriendlyName,InstanceId)
}
if (@(Get-RmlinkNativeDevices).Count) { throw 'RMLink OTG is still enumerated; unplug its OTG cable first.' }
$serial = @(Get-PnpDevice -PresentOnly | Where-Object {
    $_.FriendlyName -match "CH343.*\($Port\)" -and $_.Status -eq 'OK'
} | Select-Object Status,FriendlyName,InstanceId)
if ($serial.Count -ne 1) { throw "Expected one connected CH343 at $Port." }
$results = @()
for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
    $bootLog = Join-Path $logs "boot-wireless-$cycle.log"
    & $PythonPath (Join-Path $PSScriptRoot 'read_boot_log.py') --port $Port --seconds 8 --output $bootLog *> (Join-Path $logs "boot-wireless-$cycle-capture.log")
    if ($LASTEXITCODE -ne 0) { throw "Serial reset/capture failed on cycle $cycle." }
    $boot = Get-Content -LiteralPath $bootLog -Raw
    if ($boot -notmatch 'Calling app_main\(\)' -or $boot -notmatch 'STA address ') {
        throw "Firmware startup or hotspot join missing from boot log $cycle."
    }
    if ($boot -match 'bridge_main: Mounted|usb_send_task is ready|EP_OUT:') {
        throw "Native USB mount/endpoint activity appeared in wireless boot $cycle."
    }
    if ($boot -match 'Guru Meditation|panic\(|abort\(\) was called' -or
        [regex]::Matches($boot,'ESP-ROM:esp32s3-').Count -ne 1) {
        throw "Crash or unexpected reset observed in wireless boot $cycle."
    }
    $verifyText = & $PythonPath (Join-Path $PSScriptRoot 'rmlink_tool.py') verify --host $DeviceHost
    if ($LASTEXITCODE -ne 0) { throw "Wireless DAP verification failed on cycle $cycle." }
    $verify = ($verifyText -join "`n") | ConvertFrom-Json
    $networkText = & $PythonPath (Join-Path $PSScriptRoot 'rmlink_tool.py') network --host $DeviceHost
    if ($LASTEXITCODE -ne 0) { throw "Network status failed on cycle $cycle." }
    $network = ($networkText -join "`n") | ConvertFrom-Json
    if (-not $network.configured -or -not $network.connected) { throw 'Saved hotspot parameters were not applied.' }
    $native = @(Get-RmlinkNativeDevices)
    if ($native.Count) { throw "Native RMLink USB appeared on cycle $cycle." }
    $ipLog = [regex]::Match($boot,'I \((\d+)\) rmlink_wifi: STA address ([0-9.]+)')
    $result = [ordered]@{
        cycle = $cycle
        resetMethod = 'CH343 RTS; not a physical power cycle'
        serialBootCaptured = $true
        nativeUsbPresent = $false
        usbMountInBootLog = $false
        ipAssignedAtBootMs = if ($ipLog.Success) { [int]$ipLog.Groups[1].Value } else { $null }
        network = $network
        tcpVerification = $verify
        bootLog = "artifacts/logs/boot-wireless-$cycle.log"
    }
    $results += $result
    Write-Output "PASS: wireless startup $cycle/$Cycles, IP=$($network.ip), 100 queries=$($verify.elapsedMs) ms, native USB absent"
}
$report = [ordered]@{
    capturedAt = (Get-Date).ToString('o')
    userReportedCabling = 'OTG unplugged; CH343 retained for power and logs'
    deviceHost = $DeviceHost
    serialDevice = $serial
    cycles = $results
    evidence = 'Physical ESP32 restart without native USB enumeration; saved STA configuration and TCP verified; target SWD not tested; power cycle not tested'
}
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $logs 'verify-wireless-startup.json') -Encoding utf8
