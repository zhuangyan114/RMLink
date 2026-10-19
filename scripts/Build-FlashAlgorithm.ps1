param([string]$Gcc='C:\CLionToolchains\gcc-arm-none-eabi-10.3-2021.10\bin\arm-none-eabi-gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$out=Join-Path $root 'artifacts\build\flash-algorithms'
New-Item -ItemType Directory -Force $out | Out-Null
$pack=Get-Content -Raw (Join-Path $root 'host\algorithms\targets.json') | ConvertFrom-Json
foreach ($property in $pack.algorithms.PSObject.Properties) {
    $name=$property.Name; $algorithm=$property.Value
    $src=Join-Path $root "host\algorithms\$name"
    $flags=@("-mcpu=$($algorithm.cpu)",'-mthumb','-mfloat-abi=soft','-Os','-g','-ffreestanding','-fno-builtin','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-nostdlib','-Wall','-Wextra','-Werror','-Wl,--build-id=none',"-Wl,-T,$src\algorithm.ld")
    foreach ($define in $algorithm.defines) { $flags += "-D$define" }
    & $Gcc @flags (Join-Path $src 'algorithm.c') -o (Join-Path $out "$name.elf")
    if ($LASTEXITCODE -ne 0) { throw "Flash algorithm $name build failed" }
    & (Join-Path (Split-Path $Gcc -Parent) 'arm-none-eabi-objcopy.exe') -O binary (Join-Path $out "$name.elf") (Join-Path $out $algorithm.fileName)
    if ($LASTEXITCODE -ne 0) { throw "Flash algorithm $name export failed" }
}
& 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe' (Join-Path $PSScriptRoot 'generate_target_pack.py')
if ($LASTEXITCODE -ne 0) { throw 'Shared target package generation failed' }
& 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe' (Join-Path $PSScriptRoot 'embed_flash_algorithm.py')
if ($LASTEXITCODE -ne 0) { throw 'Flash algorithm embedding failed' }
