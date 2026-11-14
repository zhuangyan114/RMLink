param(
    [string]$Python,
    [string]$Iscc,
    [string]$OutputDirectory,
    [string]$SignCommand,
    [switch]$AllowUncommitted
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $Python = Join-Path $root 'artifacts\tools\connector-build-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python.exe' }
}
& (Join-Path $PSScriptRoot 'Build-Connector.ps1') -Python $Python
if ($LASTEXITCODE -ne 0) { throw 'Connector build failed' }
$arguments = @((Join-Path $PSScriptRoot 'build_windows_installer.py'))
if ($Iscc) { $arguments += @('--iscc',$Iscc) }
if ($OutputDirectory) { $arguments += @('--output',$OutputDirectory) }
if ($SignCommand) { $arguments += @('--sign-command',$SignCommand) }
if ($AllowUncommitted) { $arguments += '--allow-uncommitted' }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
