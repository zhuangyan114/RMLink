param([string]$IdfPath = 'D:\ESP32\ESP32idf\Espressif\frameworks\esp-idf-v5.1.2')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Read-RmlinkJson([string]$relativePath) {
    $path = Join-Path $root $relativePath
    if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Raw | ConvertFrom-Json }
}
$lastFlash = Read-RmlinkJson 'artifacts\hardware\rmlink-last-flash.json'
$versions = Get-Content -LiteralPath (Join-Path $root 'docs\reference-versions.json') -Raw | ConvertFrom-Json
$references = foreach ($repo in $versions.repositories) {
    $path = Join-Path $root "reference\$($repo.directory)"
    $head = & git -C $path rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head -ne $repo.commit) { throw "Reference commit mismatch: $($repo.directory)" }
    [ordered]@{ directory=$repo.directory; url=$repo.url; commit=$head; trackedChanges=@(& git -C $path diff --name-only HEAD) }
}
$images = foreach ($name in @('firmware', 'firmware-target')) {
    $directory = Join-Path $root "artifacts\build\$name"
    [ordered]@{
        profile = $name
        activeAsLastRecordedFlash = $lastFlash -and $lastFlash.profile -eq $name
        binarySha256 = (Get-FileHash -LiteralPath (Join-Path $directory 'rmlink.bin')).Hash
        sdkconfigSha256 = (Get-FileHash -LiteralPath (Join-Path $directory 'sdkconfig')).Hash
        targetGpioEnabled = [bool]((Get-Content -LiteralPath (Join-Path $directory 'sdkconfig') -Raw) -match '(?m)^CONFIG_RMLINK_TARGET_PINS_CONFIRMED=y')
    }
}
$data = [ordered]@{
    capturedAt = (Get-Date).ToString('o')
    references = @($references)
    firmwareBaseCommit = ($versions.repositories | Where-Object {$_.directory -eq 'esp-usb-bridge'}).commit
    projectCommit = (& git -C $root rev-parse --verify HEAD 2>$null)
    projectWorktree = @(& git -C $root status --short)
    sdk = [ordered]@{ path=$IdfPath; commit=(& git -C $IdfPath rev-parse HEAD); description=(& git -C $IdfPath describe --tags --dirty); existingChanges=@(& git -C $IdfPath status --short) }
    dependencyLockSha256 = (Get-FileHash -LiteralPath (Join-Path $root 'firmware\dependencies.lock')).Hash
    images = @($images)
    lastRecordedFlash = $lastFlash
    originalFlashBackupSha256 = (Get-FileHash -LiteralPath (Join-Path $root 'artifacts\hardware\esp32s3-before-rmlink.bin')).Hash
    deviceTransportTest = (Get-Content -LiteralPath (Join-Path $root 'artifacts\logs\verify-device-transport.log') -Raw | ConvertFrom-Json)
    wirelessStartupTest = if (Test-Path -LiteralPath (Join-Path $root 'artifacts\logs\verify-wireless-startup.json')) {
        Get-Content -LiteralPath (Join-Path $root 'artifacts\logs\verify-wireless-startup.json') -Raw | ConvertFrom-Json
    } else { $null }
    targetUsbReads = Read-RmlinkJson 'artifacts\logs\verify-swd-usb.json'
    targetTcpReads = Read-RmlinkJson 'artifacts\logs\verify-swd-tcp.json'
    targetOwnership = Read-RmlinkJson 'artifacts\logs\verify-target-ownership.json'
    targetReadBenchmark = Read-RmlinkJson 'artifacts\logs\benchmark-swd-reads.json'
    targetPipelineBenchmark = Read-RmlinkJson 'artifacts\logs\benchmark-swd-pipeline.json'
    targetFlashBackupVerification = Read-RmlinkJson 'artifacts\logs\verify-flash-backup.json'
    targetFinalReadState = Read-RmlinkJson 'artifacts\logs\verify-swd-final-state.json'
    transportBenchmark = Read-RmlinkJson 'artifacts\logs\benchmark-transport.json'
    transportDiagnosticChecks = Read-RmlinkJson 'artifacts\logs\verify-transport-bench.json'
    targetFullAcceptance = Read-RmlinkJson 'artifacts\logs\accept-stm32.json'
    targetExceptions = Read-RmlinkJson 'artifacts\logs\verify-target-exceptions.json'
    targetOwnershipCurrent = Read-RmlinkJson 'artifacts\logs\verify-target-ownership-final.json'
    evidenceBoundary = 'Inspect individual result.complete and restored fields; older read-only/transport evidence is version specific; actual SWCLK/voltage/waveforms not instrument-measured'
}
$output = Join-Path $root 'artifacts\validation-snapshot.json'
$data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $output -Encoding utf8
Write-Output "Validation snapshot written: $output"
