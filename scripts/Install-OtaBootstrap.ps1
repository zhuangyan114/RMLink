[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [string]$Port = 'COM13',
    [switch]$Apply,
    [switch]$TargetWiringConfirmed,
    [string]$Python = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$build = (Resolve-Path -LiteralPath $BuildDirectory).Path
$config = [IO.File]::ReadAllText((Join-Path $build 'sdkconfig'))
if ($config -notmatch '(?m)^CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\r?$') { throw 'Build must enable the official OTA rollback bootloader.' }
if ($config -notmatch '(?m)^CONFIG_ESPTOOLPY_FLASHSIZE="16MB"\r?$') { throw 'This bootstrap layout requires the verified 16 MiB RMLink board.' }
$arguments = Get-Content -LiteralPath (Join-Path $build 'flasher_args.json') -Raw | ConvertFrom-Json
$expected = @{'0x0'='bootloader/bootloader.bin';'0x8000'='partition_table/partition-table.bin';'0x10000'='rmlink.bin';'0x310000'='ota_data_initial.bin'}
if (@($arguments.flash_files.PSObject.Properties).Count -ne $expected.Count) { throw 'Unexpected bootstrap flash regions.' }
$files = foreach ($offset in $expected.Keys) {
    if ($arguments.flash_files.$offset -ne $expected[$offset]) { throw "Unexpected flash file at $offset" }
    $path = Join-Path $build $expected[$offset]
    [ordered]@{offset=$offset;path=$path;sha256=(Get-FileHash -LiteralPath $path).Hash;bytes=(Get-Item -LiteralPath $path).Length}
}
$plan = [ordered]@{action='first USB OTA migration';apply=[bool]$Apply;port=$Port;preserve='NVS at 0x9000..0xefff';files=@($files)}
$plan | ConvertTo-Json -Depth 5
if (-not $Apply) { return }
if ($config -match '(?m)^CONFIG_RMLINK_TARGET_PINS_CONFIRMED=y\r?$' -and -not $TargetWiringConfirmed) { throw 'Use -TargetWiringConfirmed only after checking the target wiring.' }
if ($Port -notmatch '^COM\d+$') { throw 'Choose one explicit Windows COM port.' }
$env:PYTHONPATH = Join-Path $root 'host'
& $Python -c 'from rmlink_connector.local_service import LocalService; s=LocalService().status(); assert s is None, "Stop the connector and close debug sessions before USB migration"'
if ($LASTEXITCODE -ne 0) { throw 'Connector is still running; stop it before USB migration.' }
# ROM read/flash intentionally occurs only with -Apply. Never erase the chip.
$flashId = & $Python -m esptool --chip esp32s3 --port $Port flash_id 2>&1
if ($LASTEXITCODE -ne 0 -or ($flashId -join "`n") -notmatch 'Detected flash size:\s*16MB') { throw 'Could not verify 16 MiB physical flash; no flash write performed.' }
$backupDirectory = Join-Path $root ('artifacts\backups\ota-bootstrap-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupDirectory -ErrorAction Stop | Out-Null
$backup = Join-Path $backupDirectory 'before-migration.bin'
& $Python -m esptool --chip esp32s3 --port $Port read_flash 0x0 0x312000 $backup
if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $backup).Length -ne 0x312000) { throw 'Bootstrap backup failed; no flash write performed.' }
foreach ($file in $files) {
    if ((Get-FileHash -LiteralPath $file.path).Hash -ne $file.sha256) { throw 'A build file changed after review; no flash write performed.' }
}
$receipt = [ordered]@{startedAt=(Get-Date).ToString('o');port=$Port;backup=$backup;backupSha256=(Get-FileHash -LiteralPath $backup).Hash;files=@($files);completed=$false}
$receiptPath = Join-Path $backupDirectory 'migration.json'
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
& (Join-Path $PSScriptRoot 'Invoke-Idf.ps1') -IdfArguments @('-B',$build,'-D',"SDKCONFIG=$($build.Replace('\','/'))/sdkconfig",'-p',$Port,'flash')
if ($LASTEXITCODE -ne 0) { throw "USB migration was interrupted; preserve backup and receipt: $receiptPath" }
$receipt.completed = $true
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
Write-Output "USB bootstrap written. Verify boot, USB and Wi-Fi before accepting migration. Receipt: $receiptPath"
