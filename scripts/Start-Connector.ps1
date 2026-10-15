param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root 'artifacts\build\connector\dist\RMLinkConnector.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Run Build-Connector.ps1 first' }
$state = Join-Path $env:USERPROFILE '.rmlink'
New-Item -ItemType Directory -Force $state | Out-Null
# Protect the per-user bearer token and pairing state with an explicit Windows ACL.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
# icacls changes the DACL only; Set-Acl in some packaged hosts also requests
# SACL privileges that a normal user-mode connector should not require.
& icacls.exe $state /inheritance:r /grant:r ("*$($sid.Value):(OI)(CI)F") '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect connector state permissions' }
if ($Foreground) { & $exe serve; exit $LASTEXITCODE }
$out = Join-Path $root 'artifacts\logs\connector-service.log'
$err = Join-Path $root 'artifacts\logs\connector-service-error.log'
$process = Start-Process -FilePath $exe -ArgumentList @('serve') -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
& (Join-Path $root 'host\distribution\Wait-Ready.ps1') -ServiceProcessId $process.Id
Write-Output "RMLink connector started, PID $($process.Id); log $out"
