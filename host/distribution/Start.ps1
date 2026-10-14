param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'bin\RMLinkConnector.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'RMLinkConnector.exe missing' }
if (Get-NetTCPConnection -LocalPort 3243 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'Port 3243 already has a service; use bin\RMLinkConnector.exe status to inspect it.'
    exit 0
}
$state = Join-Path $env:USERPROFILE '.rmlink'
New-Item -ItemType Directory -Force $state | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
& icacls.exe $state /inheritance:r /grant:r ("*$($sid.Value):(OI)(CI)F") '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect connector state permissions' }
if ($Foreground) { & $exe serve; exit $LASTEXITCODE }
$logs = Join-Path $env:LOCALAPPDATA 'RMLink\logs'
New-Item -ItemType Directory -Force $logs | Out-Null
Start-Process -FilePath $exe -ArgumentList @('serve') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'service.log') -RedirectStandardError (Join-Path $logs 'service-error.log') | Out-Null
Write-Output 'RMLink component started; API: http://127.0.0.1:3243/v1'
