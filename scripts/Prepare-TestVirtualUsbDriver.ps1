param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root 'artifacts\build\usbip-win2-cancel'
$manifest = Get-Content -LiteralPath (Join-Path $build 'build-result.json') -Raw | ConvertFrom-Json
$directory = Join-Path $root 'artifacts\driver-test'
$package = Join-Path $directory 'package'
$backup = Join-Path $directory 'official-backup'
$certificateFile = Join-Path $directory 'RMLink-Test-Driver.cer'
$metadataFile = Join-Path $directory 'test-driver.json'
$signTool = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe'
$inf2Cat = Join-Path $build 'packages\Microsoft.Windows.WDK.x64.10.0.28000.2526\c\bin\10.0.28000.0\x86\Inf2Cat.exe'
if ($manifest.sourceCommit -ne '55e1fa7f0c2157017b02dc1f3236e98169a535e4') { throw 'Unexpected upstream revision' }
if ((Get-FileHash (Join-Path $manifest.packageDirectory 'usbip2_ude.sys')).Hash -ne $manifest.binarySha256) { throw 'Built driver changed since build manifest' }
foreach ($tool in @($signTool, $inf2Cat)) { if (-not (Test-Path -LiteralPath $tool)) { throw "Missing tool: $tool" } }
New-Item -ItemType Directory -Force $directory, $package, $backup | Out-Null

# Preserve the actual installed official package, without exporting private keys.
if (-not (Test-Path -LiteralPath (Join-Path $backup 'backup.json'))) {
    $device = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceID -eq 'ROOT\USB\0000' }
    $driver = Get-CimInstance Win32_SystemDriver -Filter "Name='usbip2_ude'"
    if (-not $device -or -not $driver) { throw 'Installed USBip controller not found' }
    $source = Split-Path $driver.PathName -Parent
    $catalog = Get-AuthenticodeSignature -LiteralPath (Join-Path $source 'usbip2_ude.cat')
    if ($catalog.Status -ne 'Valid' -or $catalog.SignerCertificate.Subject -notlike '*Microsoft Windows Hardware Compatibility Publisher*') {
        throw 'The installed baseline is not the reviewed Microsoft-signed package'
    }
    foreach ($name in @('usbip2_ude.inf', 'usbip2_ude.sys', 'usbip2_ude.cat')) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $backup $name) -Force
    }
    [ordered]@{
        capturedAt = (Get-Date).ToString('o'); deviceInstance = $device.DeviceID
        originalInf = $device.InfName; version = $device.DriverVersion; source = $source
        hashes = @('usbip2_ude.inf', 'usbip2_ude.sys', 'usbip2_ude.cat') | ForEach-Object {
            [ordered]@{ name = $_; sha256 = (Get-FileHash (Join-Path $backup $_)).Hash }
        }
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup 'backup.json') -Encoding utf8
}

$certificate = $null
if (Test-Path -LiteralPath $metadataFile) {
    $previous = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
    $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $previous.certificateThumbprint) -ErrorAction SilentlyContinue
}
if (-not $certificate -or -not $certificate.HasPrivateKey) {
    $certificate = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=RMLink Local Driver Test' `
        -CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
        -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(1)
}
Export-Certificate -Cert $certificate -FilePath $certificateFile -Type CERT -Force | Out-Null
# Current-user trust permits signature/hash verification now. Machine trust is
# added by the elevated installer only after the required boot mode is active.
Import-Certificate -FilePath $certificateFile -CertStoreLocation Cert:\CurrentUser\Root | Out-Null

foreach ($name in @('usbip2_ude.inf', 'usbip2_ude.sys')) {
    Copy-Item -LiteralPath (Join-Path $manifest.packageDirectory $name) -Destination (Join-Path $package $name) -Force
}
Copy-Item -LiteralPath (Join-Path $root 'host\windows\usbip-win2-LICENSE.txt') -Destination (Join-Path $package 'usbip-win2-LICENSE.txt') -Force
$binary = Join-Path $package 'usbip2_ude.sys'
$catalogFile = Join-Path $package 'usbip2_ude.cat'
& $signTool sign /s My /sha1 $certificate.Thumbprint /fd SHA256 $binary
if ($LASTEXITCODE -ne 0) { throw 'Embedded SYS signing failed' }
# The SYS signature changes its hash: regenerate the catalog before signing it.
& $inf2Cat ("/driver:$package") /os:10_X64 /uselocaltime
if ($LASTEXITCODE -ne 0) { throw 'Catalog generation failed' }
& $signTool sign /s My /sha1 $certificate.Thumbprint /fd SHA256 $catalogFile
if ($LASTEXITCODE -ne 0) { throw 'Catalog signing failed' }
& $signTool verify /pa /v $binary
if ($LASTEXITCODE -ne 0) { throw 'Embedded signature verification failed' }
& $signTool verify /pa /v /c $catalogFile $binary
if ($LASTEXITCODE -ne 0) { throw 'Catalog/hash verification failed' }
$files = @('usbip2_ude.inf', 'usbip2_ude.sys', 'usbip2_ude.cat') | ForEach-Object {
    [ordered]@{ name = $_; sha256 = (Get-FileHash (Join-Path $package $_)).Hash }
}
[ordered]@{
    preparedAt = (Get-Date).ToString('o'); sourceCommit = $manifest.sourceCommit
    patchSha256 = $manifest.patchSha256; unsignedBinarySha256 = $manifest.binarySha256
    packageDirectory = $package; certificateFile = $certificateFile
    certificateThumbprint = $certificate.Thumbprint; certificateExpires = $certificate.NotAfter.ToString('o')
    certificateOwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    privateKeyExported = $false; embeddedSysSignature = 'Valid'; catalogAndBinaryHashVerified = $true
    files = $files; officialBackup = $backup; installed = $false; formalDistribution = $false
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metadataFile -Encoding utf8
Write-Output "Test-signed package verified: $package"
Write-Output "Official package backup: $backup"
Write-Output 'Machine boot settings and the installed driver have not changed.'
