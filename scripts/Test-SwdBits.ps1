param([string]$Gcc='C:\mingw64\bin\gcc.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$output=Join-Path $root 'artifacts\tests\swd-bits-test.exe'
New-Item -ItemType Directory -Force (Split-Path $output -Parent) | Out-Null
& $Gcc -std=c11 -O2 -Wall -Wextra -Werror "-I$root\tests\swd" (Join-Path $root 'tests\swd\swd_bits_test.c') -o $output
if ($LASTEXITCODE -ne 0) { throw 'SWD pin model compilation failed' }
& $output
if ($LASTEXITCODE -ne 0) { throw 'SWD pin model failed' }
