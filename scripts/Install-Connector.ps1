param([switch]$Install,[switch]$StartAtLogon,[switch]$RemoveStartAtLogon)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$target = Join-Path $env:LOCALAPPDATA 'RMLink'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ($RemoveStartAtLogon) {
    Remove-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector' -ErrorAction SilentlyContinue
    Write-Output 'RMLink logon startup removed; binaries and pairing state retained.'
    exit 0
}
if (-not $Install) {
    Write-Output "Prepared per-user component install: $target"
    Write-Output 'Use -Install to copy the built service/SDK. Add -StartAtLogon to enable hidden startup.'
    Write-Output 'This script does not install or replace the virtual USB kernel driver.'
    exit 0
}
$source = Join-Path $root 'artifacts\build\connector\dist'
if (-not (Test-Path -LiteralPath (Join-Path $source 'RMLinkConnector.exe'))) { throw 'Build-Connector.ps1 required' }
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'RMLinkConnector.exe') -Destination $target
Get-ChildItem -LiteralPath $source -Filter '*.txt' | Copy-Item -Destination $target
$sdkSource = Join-Path $root 'artifacts\build\sdk'
$sdkTarget = Join-Path $target 'sdk'
New-Item -ItemType Directory -Force $sdkTarget | Out-Null
foreach ($name in @('rmlink.dll','rmlink.h','librmlink.a','rmlink.def','rmlink-client-0.2.1.tgz')) {
    $path = Join-Path $sdkSource $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $sdkTarget }
}
$startScript = @'
$ErrorActionPreference = 'Stop'
$directory = Split-Path $PSScriptRoot -Leaf
$exe = Join-Path $PSScriptRoot 'RMLinkConnector.exe'
if (Get-NetTCPConnection -LocalPort 3243 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
Start-Process -FilePath $exe -ArgumentList @('serve') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'service.log') -RedirectStandardError (Join-Path $logs 'service-error.log') | Out-Null
'@
$entry = Join-Path $target 'Start-Service.ps1'
[IO.File]::WriteAllText($entry,$startScript,[Text.UTF8Encoding]::new($false))
if ($StartAtLogon) {
    New-Item -Path $runKey -Force | Out-Null
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $command = '"' + $powershell + '" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $entry + '"'
    New-ItemProperty -Path $runKey -Name 'RMLinkConnector' -Value $command -PropertyType String -Force | Out-Null
}
Write-Output "Per-user service and SDK installed: $target"
Write-Output ('Logon startup: ' + [bool]$StartAtLogon)
