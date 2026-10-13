param(
    [string]$ProjectPath = 'D:\STM32\RMLink\firmware',
    [string]$IdfPath = 'D:\ESP32\ESP32idf\Espressif\frameworks\esp-idf-v5.1.2',
    [string]$ToolsPath = 'D:\ESP32\ESP32idf\Espressif',
    [string[]]$IdfArguments = @('build')
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $ToolsPath 'python_env\idf5.1_py3.11_env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "IDF Python not found: $pythonPath" }
$env:IDF_PATH = $IdfPath
$env:IDF_TOOLS_PATH = $ToolsPath
$env:IDF_PYTHON_ENV_PATH = Split-Path (Split-Path $pythonPath -Parent) -Parent
$exports = & $pythonPath (Join-Path $IdfPath 'tools\idf_tools.py') export --format key-value
if ($LASTEXITCODE -ne 0) { throw 'ESP-IDF environment export failed.' }
foreach ($line in $exports) {
    $pair = $line -split '=', 2
    if ($pair.Count -ne 2) { continue }
    $value = $pair[1]
    if ($pair[0] -eq 'PATH') { $value = $value.Replace('%PATH%', $env:PATH).Replace('$PATH', $env:PATH) }
    [Environment]::SetEnvironmentVariable($pair[0], $value, 'Process')
}
Push-Location -LiteralPath $ProjectPath
try {
    & $pythonPath (Join-Path $IdfPath 'tools\idf.py') -C $ProjectPath @IdfArguments
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
