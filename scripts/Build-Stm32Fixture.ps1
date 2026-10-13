param([string]$Gcc = 'C:\CLionToolchains\gcc-arm-none-eabi-10.3-2021.10\bin\arm-none-eabi-gcc.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root 'tests\stm32f103'
$out = Join-Path $root 'artifacts\build\stm32-fixture'
New-Item -ItemType Directory -Force $out | Out-Null
$toolDir = Split-Path $Gcc -Parent
foreach ($name in @('fixture')) {
    & $Gcc -mcpu=cortex-m3 -mthumb -Os -g -ffreestanding -fno-builtin -nostdlib -Wall -Wextra -Werror "-Wl,-T,$src\$name.ld" "-Wl,-Map,$out\$name.map" (Join-Path $src "$name.c") -o (Join-Path $out "$name.elf")
    if ($LASTEXITCODE -ne 0) { throw "Failed to build $name" }
    & (Join-Path $toolDir 'arm-none-eabi-objcopy.exe') -O binary (Join-Path $out "$name.elf") (Join-Path $out "$name.bin")
    if ($LASTEXITCODE -ne 0) { throw "Failed to export $name" }
    & (Join-Path $toolDir 'arm-none-eabi-nm.exe') -n (Join-Path $out "$name.elf") | Set-Content -Encoding utf8 (Join-Path $out "$name.symbols.txt")
}
# Fill the entire 64 KiB image with a deterministic address-varying pattern.
# This exercises Flash boundaries beyond the small executable and SRAM limit.
$image = [IO.File]::ReadAllBytes((Join-Path $out 'fixture.bin'))
$full = New-Object byte[] 65536
for ($i = 0; $i -lt $full.Length; ++$i) { $full[$i] = (($i * 37) -bxor ($i -shr 8)) -band 255 }
[Array]::Copy($image, $full, $image.Length)
[IO.File]::WriteAllBytes((Join-Path $out 'fixture-64k.bin'), $full)
Get-FileHash (Join-Path $out 'fixture-64k.bin')
Get-Content (Join-Path $out 'fixture.symbols.txt')
