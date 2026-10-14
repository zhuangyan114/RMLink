param(
    [ValidateSet('Preflight', 'EnableTestMode', 'Install', 'Restore')]
    [string]$Action = 'Preflight',
    [switch]$Elevate
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$directory = Join-Path $root 'artifacts\driver-test'
$log = Join-Path $root "artifacts\logs\test-driver-$($Action.ToLower()).log"
$receipt = Join-Path $directory "$($Action.ToLower()).json"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if (-not $Elevate) { throw 'Administrator rights required; rerun with -Elevate for a Windows UAC prompt' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action ' + $Action
    $process = Start-Process -FilePath powershell.exe -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Elevated $Action failed; see $log" }
    if (Test-Path -LiteralPath $receipt) { Get-Content -LiteralPath $receipt }
    return
}
New-Item -ItemType Directory -Force $directory | Out-Null
Start-Transcript -LiteralPath $log -Force | Out-Null

function Get-BootState {
    $secureBoot = Confirm-SecureBootUEFI
    $options = (Get-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control).SystemStartOptions
    $hvci = (Get-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity -ErrorAction SilentlyContinue).Enabled
    $protection = 'Unknown'
    $volume = Get-CimInstance -Namespace root\CIMV2\Security\MicrosoftVolumeEncryption -ClassName Win32_EncryptableVolume -Filter "DriveLetter='C:'"
    if ($volume) {
        $result = Invoke-CimMethod -InputObject $volume -MethodName GetProtectionStatus
        if ($result.ReturnValue -eq 0) {
            $protection = switch ($result.ProtectionStatus) { 0 { 'Off' } 1 { 'On' } default { 'Unknown' } }
        }
    }
    [ordered]@{
        secureBoot = [bool]$secureBoot; memoryIntegrity = [bool]$hvci
        testSigningActive = [bool]($options -match '\bTESTSIGNING\b')
        bitLockerCProtection = $protection
        bootedAt = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')
    }
}

function Get-Controller {
    $device = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceID -eq 'ROOT\USB\0000' }
    if (-not $device) { throw 'The expected USBip controller ROOT\USB\0000 was not found' }
    $driver = Get-CimInstance Win32_SystemDriver -Filter "Name='usbip2_ude'"
    [ordered]@{
        instance = $device.DeviceID; inf = $device.InfName; version = $device.DriverVersion
        state = $driver.State; binary = $driver.PathName
        sha256 = (Get-FileHash -LiteralPath $driver.PathName).Hash
    }
}

function Stop-ConnectorAndCheckPorts {
    $statePath = Join-Path $env:USERPROFILE '.rmlink\connector.json'
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $headers = @{ Authorization = 'Bearer ' + $state.token }
        $status = $null
        try { $status = Invoke-RestMethod -Uri http://127.0.0.1:3243/v1/status -Headers $headers -TimeoutSec 3 }
        catch [System.Net.WebException] { }
        if ($status) {
            if (@($status.devices | Where-Object { $_.owner }).Count) { throw 'A target session is active; close it before replacing the driver' }
            Invoke-RestMethod -Uri http://127.0.0.1:3243/v1/shutdown -Method Post -Headers $headers -TimeoutSec 3 | Out-Null
            Start-Sleep -Seconds 2
        }
    }
    $ports = & 'C:\Program Files\USBip\usbip.exe' port 2>&1
    if ($LASTEXITCODE -ne 0 -or ($ports -join '\n') -match '(?im)^\s*Port\s+\d+') {
        throw 'USBip still has imported devices or port status is unavailable; no foreign device will be detached'
    }
}

function Get-VerifiedTestPackage {
    $metadata = Get-Content -LiteralPath (Join-Path $directory 'test-driver.json') -Raw | ConvertFrom-Json
    if ($metadata.sourceCommit -ne '55e1fa7f0c2157017b02dc1f3236e98169a535e4' -or $metadata.formalDistribution) { throw 'Unexpected test package' }
    foreach ($file in $metadata.files) {
        if ((Get-FileHash -LiteralPath (Join-Path $metadata.packageDirectory $file.name)).Hash -ne $file.sha256) { throw "Package hash mismatch: $($file.name)" }
    }
    $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2($metadata.certificateFile)
    if ($certificate.Thumbprint -ne $metadata.certificateThumbprint -or $certificate.NotAfter -lt (Get-Date)) { throw 'Test certificate mismatch or expired certificate' }
    $metadata
}

try {
    $boot = Get-BootState
    $result = [ordered]@{ capturedAt = (Get-Date).ToString('o'); action = $Action; boot = $boot; rebootInitiated = $false }
    switch ($Action) {
        'Preflight' {
            $result.controller = Get-Controller
            if (Test-Path -LiteralPath (Join-Path $directory 'test-driver.json')) {
                $metadata = Get-VerifiedTestPackage
                $result.testPackage = [ordered]@{
                    hashesVerified = $true; certificateThumbprint = $metadata.certificateThumbprint
                    packageDirectory = $metadata.packageDirectory
                }
            }
            $result.requiredUserAction = if ($boot.secureBoot) { 'Disable Secure Boot in UEFI, then EnableTestMode and manually restart' } else { 'EnableTestMode and manually restart if TESTSIGNING is not active' }
        }
        'EnableTestMode' {
            if ($boot.secureBoot) { throw 'Secure Boot is enabled. Disable it manually in UEFI before changing TESTSIGNING' }
            if ($boot.bitLockerCProtection -ne 'Off') { throw 'BitLocker protection is active or unknown. Resolve boot-change recovery readiness before changing TESTSIGNING' }
            $bcdBackup = Join-Path $directory 'bcd-before-testsigning'
            if (-not (Test-Path -LiteralPath $bcdBackup)) {
                & bcdedit.exe /export $bcdBackup
                if ($LASTEXITCODE -ne 0) { throw 'BCD backup failed; boot configuration not changed' }
            }
            & bcdedit.exe /set TESTSIGNING ON
            if ($LASTEXITCODE -ne 0) { throw 'Enabling TESTSIGNING failed' }
            $result.testSigningRequested = $true
            $result.restartRequired = -not $boot.testSigningActive
        }
        'Install' {
            if ($boot.secureBoot -or -not $boot.testSigningActive) { throw 'Boot with Secure Boot disabled and TESTSIGNING active before installing the test driver' }
            $metadata = Get-VerifiedTestPackage
            Stop-ConnectorAndCheckPorts
            Import-Certificate -FilePath $metadata.certificateFile -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
            Import-Certificate -FilePath $metadata.certificateFile -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
            $binary = Join-Path $metadata.packageDirectory 'usbip2_ude.sys'
            $signature = Get-AuthenticodeSignature -LiteralPath $binary
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $metadata.certificateThumbprint) { throw 'Installed trust does not validate the expected test signature' }
            & pnputil.exe /add-driver (Join-Path $metadata.packageDirectory 'usbip2_ude.inf') /install
            if ($LASTEXITCODE -notin @(0, 3010)) { throw 'Test package installation failed' }
            $result.restartRequired = $LASTEXITCODE -eq 3010
            & pnputil.exe /restart-device 'ROOT\USB\0000'
            if ($LASTEXITCODE -notin @(0, 3010)) { throw 'USBip controller restart failed' }
            $result.restartRequired = $result.restartRequired -or $LASTEXITCODE -eq 3010
            $result.controller = Get-Controller
            if ($result.controller.sha256 -ne (Get-FileHash -LiteralPath $binary).Hash) { throw 'Windows selected a different driver package; stop before acceptance' }
            $result.installed = $true
            $result.runtimeAcceptanceComplete = $false
        }
        'Restore' {
            $metadata = Get-VerifiedTestPackage
            $backup = Get-Content -LiteralPath (Join-Path $metadata.officialBackup 'backup.json') -Raw | ConvertFrom-Json
            foreach ($file in $backup.hashes) {
                if ((Get-FileHash -LiteralPath (Join-Path $metadata.officialBackup $file.name)).Hash -ne $file.sha256) { throw 'Official backup hash mismatch' }
            }
            $officialCat = Get-AuthenticodeSignature -LiteralPath (Join-Path $metadata.officialBackup 'usbip2_ude.cat')
            if ($officialCat.Status -ne 'Valid' -or $officialCat.SignerCertificate.Subject -notlike '*Microsoft Windows Hardware Compatibility Publisher*') { throw 'Official backup signature mismatch' }
            Stop-ConnectorAndCheckPorts
            $current = Get-Controller
            $testHash = ($metadata.files | Where-Object { $_.name -eq 'usbip2_ude.sys' }).sha256
            $originalHash = ($backup.hashes | Where-Object { $_.name -eq 'usbip2_ude.sys' }).sha256
            if ($current.sha256 -eq $testHash) {
                if ($current.inf -notmatch '^oem\d+\.inf$' -or $current.inf -eq $backup.originalInf) { throw 'Refusing to remove an unexpected INF' }
                & pnputil.exe /delete-driver $current.inf /uninstall
                if ($LASTEXITCODE -notin @(0, 3010)) { throw 'Removing the test package failed' }
            } elseif ($current.sha256 -ne $originalHash) { throw 'Installed driver is neither this test package nor the official backup' }
            & pnputil.exe /add-driver (Join-Path $metadata.officialBackup 'usbip2_ude.inf') /install
            if ($LASTEXITCODE -notin @(0, 3010)) { throw 'Official driver restoration failed' }
            & pnputil.exe /restart-device 'ROOT\USB\0000'
            if ($LASTEXITCODE -notin @(0, 3010)) { throw 'Restored controller restart failed' }
            $result.controller = Get-Controller
            if ($result.controller.sha256 -ne $originalHash) { throw 'Official driver hash has not been restored' }
            & bcdedit.exe /set TESTSIGNING OFF
            if ($LASTEXITCODE -ne 0) { throw 'Disabling TESTSIGNING failed' }
            foreach ($store in @('Cert:\LocalMachine\Root', 'Cert:\LocalMachine\TrustedPublisher', 'Cert:\CurrentUser\Root', 'Cert:\CurrentUser\My')) {
                if ($store -like '*CurrentUser*' -and $identity.User.Value -ne $metadata.certificateOwnerSid) { continue }
                $path = Join-Path $store $metadata.certificateThumbprint
                if (Test-Path -LiteralPath $path) {
                    if ($store -eq 'Cert:\CurrentUser\My') { Remove-Item -LiteralPath $path -DeleteKey }
                    else { Remove-Item -LiteralPath $path }
                }
            }
            $result.officialRestored = $true
            $result.restartRequired = $true
            $result.requiredUserAction = 'Manually restart and re-enable Secure Boot in UEFI'
        }
    }
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receipt -Encoding utf8
    $result | ConvertTo-Json -Depth 6
} catch {
    [ordered]@{ capturedAt = (Get-Date).ToString('o'); action = $Action; complete = $false; failure = $_.Exception.Message } |
        ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding utf8
    Write-Error $_
    exit 1
} finally { Stop-Transcript | Out-Null }
