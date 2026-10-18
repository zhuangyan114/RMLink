param(
    [ValidateSet('status','install','repair','uninstall')][string]$Action = 'status',
    [string]$ManifestPath,
    [string]$PublishedName,
    [switch]$Elevated
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    if ($Action -eq 'status') {
        $cli = Join-Path $env:ProgramFiles 'USBip\usbip.exe'
        $services = @(Get-CimInstance Win32_SystemDriver | Where-Object {
            $_.Name -match 'usbip' -or $_.PathName -match 'usbip'
        } | ForEach-Object {
            $driverPath = $_.PathName.Trim('"') -replace '^\\SystemRoot', $env:SystemRoot
            $signature = if (Test-Path -LiteralPath $driverPath) {
                [string](Get-AuthenticodeSignature -LiteralPath $driverPath).Status
            } else { 'Unknown' }
            [pscustomobject]@{ name=$_.Name; state=$_.State; path=$driverPath; signature=$signature }
        })
        [pscustomobject]@{ installed_cli=(Test-Path -LiteralPath $cli); services=$services } | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    if ($Action -in @('install','repair')) {
        if (-not $ManifestPath) { throw 'Please select a reviewed driver-package.json.' }
        $ManifestPath = (Resolve-Path -LiteralPath $ManifestPath).Path
        $directory = Split-Path $ManifestPath -Parent
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest.format -ne 'rmlink-driver-v1' -or -not $manifest.files -or -not $manifest.infs) {
            throw 'Unsupported driver manifest. Expected rmlink-driver-v1, files and infs.'
        }
        $verified = @{}
        foreach ($item in $manifest.files) {
            $target = [IO.Path]::GetFullPath((Join-Path $directory $item.path))
            if (-not $target.StartsWith($directory + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
                throw 'Driver manifest path escapes package.'
            }
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $item.sha256) {
                throw 'Driver package SHA256 mismatch.'
            }
            $verified[$target] = $true
        }
        $catalogs = @($verified.Keys | Where-Object { [IO.Path]::GetExtension($_) -eq '.cat' })
        if (-not $catalogs.Count) { throw 'A signed driver catalog is required.' }
        foreach ($catalog in $catalogs) {
            if ((Get-AuthenticodeSignature -LiteralPath $catalog).Status -ne 'Valid') {
                throw 'Driver catalog signature is invalid or is not trusted on this computer.'
            }
        }
        $infs = @($manifest.infs | ForEach-Object {
            $target = [IO.Path]::GetFullPath((Join-Path $directory $_))
            if (-not $verified.ContainsKey($target) -or [IO.Path]::GetExtension($target) -ne '.inf') {
                throw 'Every INF must be listed in the verified manifest.'
            }
            if ((Get-Content -LiteralPath $target -Raw) -notmatch '(?i)usbip') {
                throw 'This installer only accepts USBip driver packages.'
            }
            $target
        })
    } elseif ($PublishedName -notmatch '^oem\d+\.inf$') {
        throw 'Specify the exact published driver name, for example oem123.inf.'
    } else {
        $inf = Join-Path $env:SystemRoot ('INF\' + $PublishedName)
        if ((Get-Content -LiteralPath $inf -Raw) -notmatch '(?i)usbip') {
            throw 'The selected published driver is not a USBip package.'
        }
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $admin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        if ($Elevated) { throw 'Administrator access was not granted.' }
        $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-Action',$Action,'-Elevated')
        if ($ManifestPath) { $arguments += @('-ManifestPath',('"' + $ManifestPath + '"')) }
        if ($PublishedName) { $arguments += @('-PublishedName',$PublishedName) }
        $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw "Driver operation failed ($($process.ExitCode)); no automatic reboot was requested." }
        [pscustomobject]@{ action=$Action; completed=$true; restart_required=($process.ExitCode -eq 3010) } | ConvertTo-Json -Compress
        exit 0
    }
    $restart = $false
    if ($Action -in @('install','repair')) {
        foreach ($inf in $infs) {
            $null = & pnputil.exe /add-driver $inf /install
            if ($LASTEXITCODE -notin @(0,3010)) { throw "PnP driver installation failed ($LASTEXITCODE)." }
            if ($LASTEXITCODE -eq 3010) { $restart = $true }
        }
    } else {
        $null = & pnputil.exe /delete-driver $PublishedName /uninstall
        if ($LASTEXITCODE -notin @(0,3010)) { throw "PnP driver removal failed ($LASTEXITCODE)." }
        $restart = $LASTEXITCODE -eq 3010
    }
    [pscustomobject]@{ action=$Action; completed=$true; restart_required=$restart } | ConvertTo-Json -Compress
    if ($restart) { exit 3010 }
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
}
