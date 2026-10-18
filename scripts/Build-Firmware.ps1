[CmdletBinding()]
param(
    [string]$ProjectPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'firmware'),
    [switch]$TargetProfile,
    [ValidateSet(64,128,256,512)][int]$TcpPacketSize = 64,
    [ValidateSet(0,1)][int]$ExecutorCore = 0,
    [ValidateSet(-1,0,1)][int]$WifiCore = -1,
    [switch]$ImageCachePsram,
    [switch]$StreamDiagnostics,
    [ValidateSet(0,160)][int]$StreamIpTos = 0,
    [ValidatePattern('^$|^[a-z0-9-]+$')][string]$BuildTag = ''
)
$root = Split-Path $ProjectPath -Parent
$buildName = if ($TargetProfile) { 'firmware-target' } else { 'firmware' }
if ($TcpPacketSize -ne 64) { $buildName += "-tcp$TcpPacketSize" }
if ($ExecutorCore -eq 0) { $buildName += '-core0' }
else { $buildName += '-core1' }
if ($WifiCore -ge 0) { $buildName += "-wifi$WifiCore" }
if ($ImageCachePsram) { $buildName += '-psram' }
if ($StreamDiagnostics) { $buildName += '-streamdiag' }
if ($StreamIpTos -eq 160) { $buildName += '-video' }
if ($BuildTag) { $buildName += '-' + $BuildTag }
$build = Join-Path $root "artifacts\build\$buildName"
$defaults = 'sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.rmlink.defaults'
if ($TargetProfile) { $defaults += ';sdkconfig.rmlink-target.defaults' }
if ($ExecutorCore -eq 0) { $defaults += ';sdkconfig.rmlink-core0.defaults' }
else { $defaults += ';sdkconfig.rmlink-core1.defaults' }
if ($WifiCore -ge 0) { $defaults += ";sdkconfig.rmlink-wifi$WifiCore.defaults" }
if ($ImageCachePsram) { $defaults += ';sdkconfig.rmlink-psram.defaults' }
if ($StreamDiagnostics) { $defaults += ';sdkconfig.rmlink-streamdiag.defaults' }
[System.IO.Directory]::CreateDirectory($build) | Out-Null
$tosDefaults = Join-Path $build 'sdkconfig.stream-priority.defaults'
[IO.File]::WriteAllText($tosDefaults, "CONFIG_RMLINK_STREAM_IP_TOS=$StreamIpTos`n", (New-Object Text.UTF8Encoding($false)))
$defaults += ';' + $tosDefaults.Replace('\', '/')
# SDKCONFIG_DEFAULTS only initializes absent keys. Apply this PSRAM-profile
# migration to existing build caches too, without resetting unrelated options.
$cachedConfig = Join-Path $build 'sdkconfig'
if (Test-Path -LiteralPath $cachedConfig) {
    $streamConfig = [IO.File]::ReadAllText($cachedConfig)
    $streamConfig = $streamConfig.Replace('# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set', 'CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y')
    if ($streamConfig -notmatch '(?m)^CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\r?$') { $streamConfig += "`nCONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`n" }
    $streamConfig = [regex]::Replace($streamConfig, '(?m)^CONFIG_RMLINK_STREAM_IP_TOS=.*\r?\n', '')
    $streamConfig += "`nCONFIG_RMLINK_STREAM_IP_TOS=$StreamIpTos`n"
    if ($StreamDiagnostics) {
        $streamConfig = $streamConfig.Replace('# CONFIG_RMLINK_STREAM_DIAGNOSTICS is not set', 'CONFIG_RMLINK_STREAM_DIAGNOSTICS=y')
    }
    [IO.File]::WriteAllText($cachedConfig, $streamConfig, (New-Object Text.UTF8Encoding($false)))
}
if ($WifiCore -ge 0 -and (Test-Path -LiteralPath $cachedConfig)) {
    $wifiConfig = [IO.File]::ReadAllText($cachedConfig)
    foreach ($core in 0,1) {
        $key = "CONFIG_ESP_WIFI_TASK_PINNED_TO_CORE_$core"
        $line = if ($core -eq $WifiCore) { "$key=y" } else { "# $key is not set" }
        $wifiConfig = [regex]::Replace($wifiConfig, "(?m)^(?:# )?$key(?:=y| is not set)\r?$", $line)
    }
    [IO.File]::WriteAllText($cachedConfig, $wifiConfig, (New-Object Text.UTF8Encoding($false)))
}
if ($ImageCachePsram -and (Test-Path -LiteralPath $cachedConfig)) {
    $configText = [IO.File]::ReadAllText($cachedConfig)
    $configText = $configText.Replace('# CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP is not set', 'CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y')
    $configText = [regex]::Replace($configText, '(?m)^CONFIG_LWIP_TCP_SND_BUF_DEFAULT=\d+\r?$', 'CONFIG_LWIP_TCP_SND_BUF_DEFAULT=32768')
    $configText = [regex]::Replace($configText, '(?m)^CONFIG_TCP_SND_BUF_DEFAULT=\d+\r?$', 'CONFIG_TCP_SND_BUF_DEFAULT=32768')
    [IO.File]::WriteAllText($cachedConfig, $configText, (New-Object Text.UTF8Encoding($false)))
}
& (Join-Path $PSScriptRoot 'Invoke-Idf.ps1') -ProjectPath $ProjectPath -IdfArguments @('-D', 'IDF_TARGET=esp32s3', '-D', "RMLINK_DAP_PACKET_SIZE=$TcpPacketSize", '-D', "SDKCONFIG_DEFAULTS=$defaults", '-D', "SDKCONFIG=$($build.Replace('\', '/'))/sdkconfig", '-B', $build, 'build')
exit $LASTEXITCODE
