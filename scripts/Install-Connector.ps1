param([switch]$Install,[switch]$StartAtLogon,[switch]$RemoveStartAtLogon)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$target = Join-Path $env:USERPROFILE '.rmlink\app'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ($RemoveStartAtLogon) {
    Remove-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector' -ErrorAction SilentlyContinue
    Write-Output 'RMLink logon startup removed; binaries and pairing state retained.'
    exit 0
}
if (-not $Install) {
    Write-Output "Prepared per-user component install: $target"
    Write-Output 'Use -Install to install rmlink, its connection service. Add -StartAtLogon for hidden startup.'
    exit 0
}
$source = Join-Path $root 'artifacts\build\connector\dist'
foreach ($name in @('rmlink.exe','RMLinkConnector.exe','Hotspot.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name))) { throw 'Run Build-Connector.ps1 first.' }
}
$running = Get-Process -Name RMLinkConnector,rmlink -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($target + '\',[StringComparison]::OrdinalIgnoreCase)
}
if ($running) { throw 'Close the installed client and stop its service before upgrading.' }
$bin = Join-Path $target 'bin'
New-Item -ItemType Directory -Force $bin | Out-Null
foreach ($name in @('rmlink.exe','RMLinkConnector.exe','Hotspot.ps1')) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination $bin -Force
}
Get-ChildItem -LiteralPath $source -Filter '*.txt' | Copy-Item -Destination $bin -Force
Copy-Item -LiteralPath (Join-Path $root 'host\distribution\Start-Service.ps1') -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $root 'host\distribution\Register-Command.ps1') -Destination $target -Force
$docs = Join-Path $target 'docs'
New-Item -ItemType Directory -Force $docs | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'docs\terminal-client.md') -Destination $docs -Force
Copy-Item -LiteralPath (Join-Path $root 'docs\ota-upgrade.md') -Destination $docs -Force
Copy-Item -LiteralPath (Join-Path $root 'docs\ota-acceptance-20261017.md') -Destination $docs -Force
Copy-Item -LiteralPath (Join-Path $root 'docs\test-release-0.3.2.md') -Destination $docs -Force
Copy-Item -LiteralPath (Join-Path $root 'docs\test-release-0.3.3.md') -Destination $docs -Force
Copy-Item -LiteralPath (Join-Path $root 'docs\usb-throughput.md') -Destination $docs -Force
$sdkSource = Join-Path $root 'artifacts\build\sdk'
$sdkTarget = Join-Path $target 'sdk'
New-Item -ItemType Directory -Force $sdkTarget | Out-Null
if (Test-Path -LiteralPath $sdkSource) {
    Get-ChildItem -LiteralPath $sdkSource -File | Where-Object { $_.Extension -in @('.dll','.h','.a','.def','.tgz') } | Copy-Item -Destination $sdkTarget -Force
}
$entry = Join-Path $target 'Start-Service.ps1'
& (Join-Path $target 'Register-Command.ps1') -BinDirectory $bin
$existingStartup = (Get-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector' -ErrorAction SilentlyContinue).RMLinkConnector
if ($StartAtLogon -or $existingStartup) {
    New-Item -Path $runKey -Force | Out-Null
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $command = '"' + $powershell + '" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $entry + '"'
    New-ItemProperty -Path $runKey -Name 'RMLinkConnector' -Value $command -PropertyType String -Force | Out-Null
}
Write-Output "Installed: $bin\rmlink.exe"
Write-Output 'Run rmlink in a new terminal. The current PowerShell session can also run it immediately.'
Write-Output 'Wireless CMSIS-DAP uses OpenOCD TCP. Kernel drivers and Windows boot settings were not changed.'
Write-Output ('Logon startup: ' + [bool]$StartAtLogon)
