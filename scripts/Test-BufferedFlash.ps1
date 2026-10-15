param([string]$Gcc='C:\mingw64\bin\gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
foreach ($packetBytes in @(64,512)) {
    $exe=Join-Path $root "artifacts\tests\flash-test-$packetBytes.exe"
    & $Gcc -std=c11 -Wall -Wextra -Werror "-DRMLINK_PACKET_SIZE=$packetBytes" "-I$root\firmware\components\debug_probe\include" (Join-Path $root 'tests\flash\flash_test.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_flash.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_core.c') -o $exe
    if ($LASTEXITCODE -ne 0) { throw 'Buffered Flash model build failed' }
    & $exe
    if ($LASTEXITCODE -ne 0) { throw 'Buffered Flash model failed' }
}
