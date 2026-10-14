param([string]$GccPath = 'C:\mingw64\bin\gcc.exe',
      [ValidateSet(64,128,256,512)][int]$PacketSize = 64)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$outputPath = Join-Path $root 'artifacts\tests'
[System.IO.Directory]::CreateDirectory($outputPath) | Out-Null
$executable = Join-Path $outputPath "rmlink-core-test-$PacketSize.exe"
& $GccPath -std=c11 -Wall -Wextra -Werror "-DRMLINK_PACKET_SIZE=$PacketSize" -I (Join-Path $root 'firmware\components\debug_probe\include') (Join-Path $root 'firmware\components\debug_probe\rmlink_core.c') (Join-Path $root 'firmware\test\rmlink_core_test.c') -o $executable
if ($LASTEXITCODE -ne 0) { throw 'Host C test compilation failed.' }
& $executable
if ($LASTEXITCODE -ne 0) { throw 'Host C test failed.' }
