param([string]$Port = 'COM13', [switch]$TargetProfile, [switch]$TargetWiringConfirmed,
      [ValidateSet(64,128,256,512)][int]$TcpPacketSize = 64)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$buildName = if ($TargetProfile) { 'firmware-target' } else { 'firmware' }
if ($TcpPacketSize -ne 64) { $buildName += "-tcp$TcpPacketSize" }
$build = Join-Path $root "artifacts\build\$buildName"
$config = Get-Content -LiteralPath (Join-Path $build 'sdkconfig') -Raw
if ($config -match '(?m)^CONFIG_RMLINK_TARGET_PINS_CONFIRMED=y' -and -not $TargetWiringConfirmed) {
    throw 'This build enables target GPIO. Confirm the actual wiring before using -TargetWiringConfirmed.'
}
& (Join-Path $PSScriptRoot 'Invoke-Idf.ps1') -IdfArguments @('-B', $build, '-D', "SDKCONFIG=$($build.Replace('\', '/'))/sdkconfig", '-p', $Port, 'flash')
$flashExit = $LASTEXITCODE
if ($flashExit -eq 0) {
    $receipt = [ordered]@{
        flashedAt = (Get-Date).ToString('o')
        profile = $buildName
        port = $Port
        binarySha256 = (Get-FileHash -LiteralPath (Join-Path $build 'rmlink.bin')).Hash
        sdkconfigSha256 = (Get-FileHash -LiteralPath (Join-Path $build 'sdkconfig')).Hash
        targetGpioEnabled = [bool]($config -match '(?m)^CONFIG_RMLINK_TARGET_PINS_CONFIRMED=y')
        tcpPacketSize = $TcpPacketSize
    }
    $receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'artifacts\hardware\rmlink-last-flash.json') -Encoding utf8
}
exit $flashExit
