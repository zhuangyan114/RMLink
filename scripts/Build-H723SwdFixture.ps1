param([string]$Gcc = 'C:\CLionToolchains\gcc-arm-none-eabi-10.3-2021.10\bin\arm-none-eabi-gcc.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$output = Join-Path $root 'artifacts\build\h723-swd-fixture'
New-Item -ItemType Directory -Force $output | Out-Null
$elf = Join-Path $output 'fixture.elf'
$compact = Join-Path $output 'fixture-code.bin'
& $Gcc -mcpu=cortex-m7 -mthumb -mfloat-abi=soft -ffreestanding -fno-builtin -nostdlib -Os -g -Wall -Wextra -Werror '-Wl,--gc-sections' "-T$root\tests\stm32h723\swd_fixture.ld" (Join-Path $root 'tests\stm32h723\swd_fixture.c') -o $elf
if ($LASTEXITCODE -ne 0) { throw 'H723 fixture build failed' }
$objcopy = Join-Path (Split-Path $Gcc -Parent) 'arm-none-eabi-objcopy.exe'
& $objcopy -O binary $elf $compact
if ($LASTEXITCODE -ne 0) { throw 'H723 fixture binary generation failed' }
# Deterministic nontrivial Flash payload; no original target data is read.
@'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);code=(p/'fixture-code.bin').read_bytes()
data=code+hashlib.shake_256(b'RMLink H723 SWD fixture v1').digest(131072-len(code))
(p/'fixture-128k.bin').write_bytes(data)
r=dict(bytes=len(data),code_bytes=len(code),sha256=hashlib.sha256(data).hexdigest(),
       magic_address='0x24000000',fields_address='0x24000008',stack='0x24008000',
       clock='reset HSI, no PLL/cache/peripheral setup',source='tests/stm32h723/swd_fixture.c')
(p/'fixture.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r))
'@ | python - $output
if ($LASTEXITCODE -ne 0) { throw 'H723 fixture payload generation failed' }
