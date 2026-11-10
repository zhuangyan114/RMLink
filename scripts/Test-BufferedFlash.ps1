param([string]$Gcc='C:\mingw64\bin\gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
foreach ($packetBytes in @(64,512)) {
    $exe=Join-Path $root "artifacts\tests\flash-test-$packetBytes.exe"
    & $Gcc -std=c11 -Wall -Wextra -Werror "-DRMLINK_PACKET_SIZE=$packetBytes" "-I$root\firmware\components\debug_probe\include" (Join-Path $root 'tests\flash\flash_test.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_flash.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_target.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_core.c') -o $exe
    if ($LASTEXITCODE -ne 0) { throw 'Buffered Flash model build failed' }
    & $exe
    if ($LASTEXITCODE -ne 0) { throw 'Buffered Flash model failed' }
    $packExe=Join-Path $root "artifacts\tests\target-pack-test-$packetBytes.exe"
    & $Gcc -std=c11 -Wall -Wextra -Werror "-DRMLINK_PACKET_SIZE=$packetBytes" "-I$root\firmware\components\debug_probe\include" (Join-Path $root 'tests\flash\target_pack_test.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_flash.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_target.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_core.c') -o $packExe
    if ($LASTEXITCODE -ne 0) { throw 'Shared target package model build failed' }
    & $packExe
    if ($LASTEXITCODE -ne 0) { throw 'Shared target package model failed' }
    $portExe=Join-Path $root "artifacts\tests\flash-port-test-$packetBytes.exe"
    & $Gcc -std=c11 -Wall -Wextra -Werror -DRMLINK_HOST_TEST -DRMLINK_FLASH_PORT_HOST_TEST "-DRMLINK_PACKET_SIZE=$packetBytes" "-I$root\tests\flash" "-I$root\firmware\components\debug_probe\include" (Join-Path $root 'tests\flash\flash_port_test.c') (Join-Path $root 'firmware\components\debug_probe\rmlink_flash_port.c') -o $portExe
    if ($LASTEXITCODE -ne 0) { throw 'Flash port model build failed' }
    & $portExe
    if ($LASTEXITCODE -ne 0) { throw 'Flash port model failed' }
}
