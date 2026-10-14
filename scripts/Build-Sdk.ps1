param([string]$Gcc = 'C:\mingw64\bin\gcc.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$out = Join-Path $root 'artifacts\build\sdk'
New-Item -ItemType Directory -Force $out | Out-Null
& $Gcc -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc (Join-Path $root 'host\sdk\c\rmlink.c') -lwinhttp -o (Join-Path $out 'rmlink.dll') "-Wl,--out-implib,$out\librmlink.a" "-Wl,--output-def,$out\rmlink.def"
if ($LASTEXITCODE -ne 0) { throw 'SDK build failed' }
Copy-Item -LiteralPath (Join-Path $root 'host\sdk\c\rmlink.h') -Destination $out
Get-FileHash -LiteralPath (Join-Path $out 'rmlink.dll')
