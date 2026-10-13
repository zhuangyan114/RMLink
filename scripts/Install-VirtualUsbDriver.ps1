param([switch]$Install)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$installer = Join-Path $root 'artifacts\tools\usbip-win2\USBip-0.9.8.1-x64.exe'
$expected = '38CAD6D4432B52D5BB9409D9AD03B72FDFFC4ADA4CD3A48FBECA1A2752A8518A'
$url = 'https://github.com/vadimgrn/usbip-win2/releases/download/v.0.9.8.1/USBip-0.9.8.1-x64.exe'
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'This pinned package is for Windows x64 only'
}
if (-not (Test-Path -LiteralPath $installer)) { throw "Download the reviewed package from $url to $installer" }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($hash -ne $expected) { throw 'Installer SHA256 does not match the reviewed package' }
if ($signature.Status -ne 'Valid') { throw "Invalid installer signature: $($signature.Status)" }
[pscustomobject]@{
    Package = $installer
    Version = '0.9.8.1'
    SHA256 = $hash
    Signature = $signature.Status
    Publisher = $signature.SignerCertificate.Subject
    InstallRequested = [bool]$Install
    Impact = 'Adds USBip system drivers; requires UAC; restarts USB 3.0 hubs'
} | Format-List
if (-not $Install) { return }
$log = Join-Path $root 'artifacts\logs\install-usbip-win2.log'
New-Item -ItemType Directory -Force (Split-Path $log -Parent) | Out-Null
# Approval is obtained before invoking -Install. Keep the UAC prompt visible
# to Windows while hiding any unnecessary installer window.
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/LOG="' + $log + '"'))
$process = Start-Process -FilePath $installer -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -notin @(0, 3010)) { throw "Driver installer failed ($($process.ExitCode)); see $log" }
if (-not (Test-Path -LiteralPath 'C:\Program Files\USBip\usbip.exe')) { throw "Installed CLI not found; inspect $log" }
Write-Output "Driver installed. Exit code $($process.ExitCode); installer log: $log"
if ($process.ExitCode -eq 3010 -or (Select-String -LiteralPath $log -Pattern 'Need to restart Windows\? Yes' -Quiet)) {
    Write-Output 'Installer reports a restart is required; no automatic reboot was requested.'
}
