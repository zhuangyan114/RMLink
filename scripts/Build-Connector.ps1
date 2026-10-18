param([string]$Python = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root 'artifacts\tools\connector-build-venv'
$build = Join-Path $root 'artifacts\build\connector'
$pythonExe = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Connector build environment failed' }
}
& $pythonExe -m pip install --disable-pip-version-check pyinstaller==6.16.0
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller install failed' }
New-Item -ItemType Directory -Force $build | Out-Null
& (Join-Path $PSScriptRoot 'Build-FlashAlgorithm.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Flash algorithm build failed' }
& $pythonExe -m pip freeze | Set-Content -Encoding utf8 (Join-Path $build 'build-requirements.txt')
& $pythonExe -m PyInstaller --noconfirm --clean --onefile --console --name RMLinkConnector --add-data "$(Join-Path $root 'artifacts\build\flash-algorithms\stm32f103c8.bin');rmlink_algorithms" --paths (Join-Path $root 'host') --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'work') --specpath (Join-Path $build 'spec') (Join-Path $root 'host\connector_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'Connector executable build failed' }
& $pythonExe -m PyInstaller --noconfirm --clean --onefile --console --name rmlink --paths (Join-Path $root 'host') --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'work-client') --specpath (Join-Path $build 'spec') (Join-Path $root 'host\client_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'RMLink client build failed' }
Copy-Item -LiteralPath (Join-Path $root 'host\distribution\Driver.ps1') -Destination (Join-Path $build 'dist\Driver.ps1')
Copy-Item -LiteralPath (Join-Path $root 'host\distribution\Hotspot.ps1') -Destination (Join-Path $build 'dist\Hotspot.ps1')
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $build 'dist\RMLink-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $root 'host\algorithms\stm32f103c8\LICENSE') -Destination (Join-Path $build 'dist\Orbit-FlashAlgorithm-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $root 'reference\usbip-win2\LICENSE.txt') -Destination (Join-Path $build 'dist\usbip-win2-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $venv 'Lib\site-packages\pyinstaller-6.16.0.dist-info\licenses\COPYING.txt') -Destination (Join-Path $build 'dist\PyInstaller-COPYING.txt')
$basePython = & $pythonExe -c 'import sys; print(sys.base_prefix)'
$pythonLicense = Join-Path $basePython 'LICENSE.txt'
if (Test-Path -LiteralPath $pythonLicense) {
    Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $build 'dist\Python-LICENSE.txt')
}
Get-FileHash (Join-Path $build 'dist\RMLinkConnector.exe')
Get-FileHash (Join-Path $build 'dist\rmlink.exe')
