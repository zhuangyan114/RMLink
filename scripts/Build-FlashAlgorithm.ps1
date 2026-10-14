param([string]$Gcc='C:\CLionToolchains\gcc-arm-none-eabi-10.3-2021.10\bin\arm-none-eabi-gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$src=Join-Path $root 'host\algorithms\stm32f103c8'
$out=Join-Path $root 'artifacts\build\flash-algorithms'
New-Item -ItemType Directory -Force $out | Out-Null
& $Gcc -mcpu=cortex-m3 -mthumb -Os -g -ffreestanding -fno-builtin -fno-unwind-tables -fno-asynchronous-unwind-tables -nostdlib -Wall -Wextra -Werror "-Wl,-T,$src\algorithm.ld" (Join-Path $src 'algorithm.c') -o (Join-Path $out 'stm32f103c8.elf')
if ($LASTEXITCODE -ne 0) { throw 'Flash algorithm build failed' }
& (Join-Path (Split-Path $Gcc -Parent) 'arm-none-eabi-objcopy.exe') -O binary (Join-Path $out 'stm32f103c8.elf') (Join-Path $out 'stm32f103c8.bin')
if ($LASTEXITCODE -ne 0) { throw 'Flash algorithm export failed' }
Get-FileHash -LiteralPath (Join-Path $out 'stm32f103c8.bin')
