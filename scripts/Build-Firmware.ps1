param(
    [string]$ProjectPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'firmware'),
    [switch]$TargetProfile,
    [ValidateSet(64,128,256,512)][int]$TcpPacketSize = 64,
    [ValidateSet(0,1)][int]$ExecutorCore = 0,
    [switch]$ImageCachePsram
)
$root = Split-Path $ProjectPath -Parent
$buildName = if ($TargetProfile) { 'firmware-target' } else { 'firmware' }
if ($TcpPacketSize -ne 64) { $buildName += "-tcp$TcpPacketSize" }
if ($ExecutorCore -eq 0) { $buildName += '-core0' }
else { $buildName += '-core1' }
if ($ImageCachePsram) { $buildName += '-psram' }
$build = Join-Path $root "artifacts\build\$buildName"
$defaults = 'sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.rmlink.defaults'
if ($TargetProfile) { $defaults += ';sdkconfig.rmlink-target.defaults' }
if ($ExecutorCore -eq 0) { $defaults += ';sdkconfig.rmlink-core0.defaults' }
else { $defaults += ';sdkconfig.rmlink-core1.defaults' }
if ($ImageCachePsram) { $defaults += ';sdkconfig.rmlink-psram.defaults' }
[System.IO.Directory]::CreateDirectory($build) | Out-Null
# SDKCONFIG_DEFAULTS only initializes absent keys. Apply this PSRAM-profile
# migration to existing build caches too, without resetting unrelated options.
$cachedConfig = Join-Path $build 'sdkconfig'
if ($ImageCachePsram -and (Test-Path -LiteralPath $cachedConfig)) {
    $configText = [IO.File]::ReadAllText($cachedConfig)
    $configText = $configText.Replace('# CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP is not set', 'CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y')
    $configText = [regex]::Replace($configText, '(?m)^CONFIG_LWIP_TCP_SND_BUF_DEFAULT=\d+\r?$', 'CONFIG_LWIP_TCP_SND_BUF_DEFAULT=32768')
    $configText = [regex]::Replace($configText, '(?m)^CONFIG_TCP_SND_BUF_DEFAULT=\d+\r?$', 'CONFIG_TCP_SND_BUF_DEFAULT=32768')
    [IO.File]::WriteAllText($cachedConfig, $configText, (New-Object Text.UTF8Encoding($false)))
}
& (Join-Path $PSScriptRoot 'Invoke-Idf.ps1') -ProjectPath $ProjectPath -IdfArguments @('-D', 'IDF_TARGET=esp32s3', '-D', "RMLINK_DAP_PACKET_SIZE=$TcpPacketSize", '-D', "SDKCONFIG_DEFAULTS=$defaults", '-D', "SDKCONFIG=$($build.Replace('\', '/'))/sdkconfig", '-B', $build, 'build')
exit $LASTEXITCODE
