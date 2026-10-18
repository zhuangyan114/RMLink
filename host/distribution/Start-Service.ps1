$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'bin\rmlink.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'RMLink client missing; reinstall the component.' }
$process = Start-Process -FilePath $exe -ArgumentList @('service','start') -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'RMLink startup failed; run rmlink doctor.' }
