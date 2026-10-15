param([switch]$Install,[switch]$StartAtLogon)
$ErrorActionPreference = 'Stop'
$package = [IO.Path]::GetFullPath($PSScriptRoot)
$manifest = Get-Content -LiteralPath (Join-Path $package 'manifest.json') -Raw | ConvertFrom-Json
foreach ($item in $manifest.files) {
    $path = [IO.Path]::GetFullPath((Join-Path $package $item.path))
    if (-not $path.StartsWith($package + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes package' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw "Package hash mismatch: $($item.path)" }
}
$target = Join-Path $env:LOCALAPPDATA 'RMLink'
Write-Output "Validated RMLink $($manifest.version); install destination: $target"
if (-not $Install) { Write-Output 'Add -Install to copy the component; -StartAtLogon optionally registers hidden per-user startup.'; return }
$running = Get-Process -Name RMLinkConnector -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $target 'bin\RMLinkConnector.exe') }
if ($running) { throw 'Stop the currently installed RMLink service before upgrading it.' }
New-Item -ItemType Directory -Force $target | Out-Null
foreach ($name in @('bin','sdk','docs','licenses','firmware','Start.ps1','Wait-Ready.ps1','Install.ps1','manifest.json','README.md')) {
    Copy-Item -LiteralPath (Join-Path $package $name) -Destination $target -Recurse -Force
}
if ($StartAtLogon) {
    $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-Item -Path $run -Force | Out-Null
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $command = '"' + $powershell + '" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $target 'Start.ps1') + '"'
    New-ItemProperty -Path $run -Name 'RMLinkConnector' -Value $command -PropertyType String -Force | Out-Null
}
Write-Output "Installed. Run $target\Start.ps1 to start the service. Virtual USB uses the separately installed driver."
