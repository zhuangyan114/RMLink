param([string]$Gcc='C:\mingw64\bin\gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$exe=Join-Path $root 'artifacts\tests\capture-test.exe'
& $Gcc -std=c11 -Wall -Wextra -Werror -DRMLINK_HOST_TEST "-I$root\tests\capture" "-I$root\firmware\components\debug_probe\include" (Join-Path $root 'tests\capture\capture_test.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_capture.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_target.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_core.c') -o $exe
if ($LASTEXITCODE -ne 0) { throw 'Capture state-machine host build failed' }
& $exe
if ($LASTEXITCODE -ne 0) { throw 'Capture state-machine host test failed' }
