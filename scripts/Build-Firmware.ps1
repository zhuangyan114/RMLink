param(
    [string]$ProjectPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'firmware'),
    [switch]$TargetProfile
)
$root = Split-Path $ProjectPath -Parent
$buildName = if ($TargetProfile) { 'firmware-target' } else { 'firmware' }
$build = Join-Path $root "artifacts\build\$buildName"
$defaults = 'sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.rmlink.defaults'
if ($TargetProfile) { $defaults += ';sdkconfig.rmlink-target.defaults' }
[System.IO.Directory]::CreateDirectory($build) | Out-Null
& (Join-Path $PSScriptRoot 'Invoke-Idf.ps1') -ProjectPath $ProjectPath -IdfArguments @('-D', 'IDF_TARGET=esp32s3', '-D', "SDKCONFIG_DEFAULTS=$defaults", '-D', "SDKCONFIG=$($build.Replace('\', '/'))/sdkconfig", '-B', $build, 'build')
exit $LASTEXITCODE
