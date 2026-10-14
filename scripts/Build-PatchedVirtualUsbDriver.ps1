param([switch]$Rebuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$reference = Join-Path $root 'reference\usbip-win2'
$worktree = Join-Path $root 'artifacts\build\usbip-win2-cancel'
$revision = '55e1fa7f0c2157017b02dc1f3236e98169a535e4'
$patch = Join-Path $root 'host\windows\patches\usbip-win2-cancel-length.patch'
$nugetExe = Join-Path $root 'artifacts\tools\nuget\nuget.exe'
$nugetHash = '992D70CAC5B06C38EFEC91806CABA64CDCC07E6D963A0959DBBBAF264D33B800'
if (-not (Test-Path -LiteralPath (Join-Path $reference '.git'))) { throw 'Restore the pinned reference repositories first' }
if (-not (Test-Path -LiteralPath (Join-Path $worktree '.git'))) {
    & git -C $reference fetch --depth=1 origin tag v.0.9.8.1
    if ($LASTEXITCODE -ne 0) { throw 'Cannot fetch the fixed upstream release' }
    & git clone --no-hardlinks --no-checkout $reference $worktree
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated driver checkout' }
    & git -C $worktree checkout --detach $revision
    if ($LASTEXITCODE -ne 0) { throw 'Cannot select fixed driver source' }
}
if ((& git -C $worktree rev-parse HEAD) -ne $revision) { throw 'Driver checkout differs; inspect it before building' }
& git -C $worktree apply --reverse --check --ignore-space-change $patch 2>$null
if ($LASTEXITCODE -ne 0) {
    & git -C $worktree apply --check --ignore-space-change $patch
    if ($LASTEXITCODE -ne 0) { throw 'Patch conflicts with existing driver changes' }
    & git -C $worktree apply --ignore-space-change $patch
    if ($LASTEXITCODE -ne 0) { throw 'Cannot apply cancellation patch' }
}
$changes = @(& git -C $worktree diff --name-only HEAD)
if ($changes.Count -ne 1 -or $changes[0] -ne 'drivers/ude/request_list.cpp') {
    throw 'Unexpected tracked driver changes; preserve and inspect the build checkout'
}
$actualDiff = @(& git -C $worktree diff --no-ext-diff HEAD -- 'drivers/ude/request_list.cpp')
$expectedAdded = @(Get-Content -LiteralPath $patch | Where-Object { $_.StartsWith('+') -and -not $_.StartsWith('+++') })
$actualAdded = @($actualDiff | Where-Object { $_.StartsWith('+') -and -not $_.StartsWith('+++') })
$actualRemoved = @($actualDiff | Where-Object { $_.StartsWith('-') -and -not $_.StartsWith('---') })
$expectedContent = @($expectedAdded | Where-Object { $_ -ne '+' })
$actualContent = @($actualAdded | Where-Object { $_ -ne '+' })
# Git may align an unchanged blank line with either end of the added block.
if ($actualRemoved.Count -or $expectedAdded.Count -ne $actualAdded.Count -or
    ($expectedContent -join "`n") -cne ($actualContent -join "`n")) {
    throw 'Driver file contains changes beyond the reviewed additive patch'
}
if (-not (Test-Path -LiteralPath $nugetExe)) {
    New-Item -ItemType Directory -Force (Split-Path $nugetExe -Parent) | Out-Null
    Invoke-WebRequest -Uri 'https://dist.nuget.org/win-x86-commandline/v7.9.0/nuget.exe' -OutFile $nugetExe
}
if ((Get-FileHash -LiteralPath $nugetExe).Hash -ne $nugetHash -or
    (Get-AuthenticodeSignature -LiteralPath $nugetExe).Status -ne 'Valid') {
    throw 'NuGet executable differs from the reviewed, signed tool'
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$msbuild = Join-Path $visualStudio 'MSBuild\Current\Bin\amd64\MSBuild.exe'
$vcTargets = Join-Path $visualStudio 'MSBuild\Microsoft\VC\v180'
if (-not (Test-Path -LiteralPath (Join-Path $vcTargets 'Platforms\x64\PlatformToolsets\v145\Toolset.props'))) {
    throw 'This isolated overlay requires Visual Studio 2026 with the v145 C++ tools'
}
& $nugetExe restore (Join-Path $worktree 'drivers\ude\packages.config') -PackagesDirectory (Join-Path $worktree 'packages') -NonInteractive -Verbosity quiet
if ($LASTEXITCODE -ne 0) { throw 'SDK / WDK package restore failed' }
$overlay = Join-Path $worktree 'msbuild-overlay'
New-Item -ItemType Directory -Force $overlay | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'host\windows\msbuild\Toolset.props') -Destination (Join-Path $overlay 'Toolset.props')
Copy-Item -LiteralPath (Join-Path $root 'host\windows\msbuild\Toolset.targets') -Destination (Join-Path $overlay 'Toolset.targets')
$toolRoot = Join-Path $worktree 'packages\Microsoft.Windows.WDK.x64.10.0.28000.2526\c\bin\10.0.28000.0\x64'
$sdkTools = Join-Path $worktree 'packages\Microsoft.Windows.SDK.CPP.10.0.28000.2705\c\bin\10.0.28000.0\x86'
$messages = Join-Path $worktree 'userspace\resources'
& (Join-Path $sdkTools 'mc.exe') -c -n -h $messages -r $messages (Join-Path $messages 'messages.mc')
if ($LASTEXITCODE -ne 0) { throw 'Message header generation failed' }
$output = Join-Path $worktree 'out\x64\Release'
$log = Join-Path $root 'artifacts\logs\build-ude-cancel.log'
$arguments = @((Join-Path $worktree 'drivers\ude\usbip2_ude.vcxproj'),
    $(if ($Rebuild) { '/t:Rebuild' } else { '/t:Build' }), '/m', '/nologo', '/verbosity:minimal',
    '/p:Configuration=Release', '/p:Platform=x64', '/p:SignMode=Off', '/p:EnableTestSign=false',
    '/p:SpectreMitigation=false', '/p:Driver_SpectreMitigation=false', '/p:PreferredToolArchitecture=x64',
    '/p:ImportDirectoryBuildProps=false', '/p:ImportDirectoryBuildTargets=false',
    "/p:SolutionDir=$worktree\", "/p:OutDir=$output\", "/p:VCTargetsPath=$vcTargets\",
    "/p:_ToolsetPropsPath=$overlay\Toolset.props", "/p:_ToolsetTargetsPath=$overlay\Toolset.targets",
    '/p:_ToolsetFound=true', "/p:WDKBinRoot_x86=$toolRoot")
& $msbuild @arguments 2>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) { throw "Driver build failed; see $log" }
$manifest = [ordered]@{
    capturedAt = (Get-Date).ToString('o')
    sourceCommit = $revision
    patchSha256 = (Get-FileHash -LiteralPath $patch).Hash
    binarySha256 = (Get-FileHash -LiteralPath (Join-Path $output 'usbip2_ude.sys')).Hash
    packageDirectory = (Join-Path $output 'usbip2_ude')
    signature = 'NotSigned'
    installed = $false
    spectreLibraryOption = $false
    sdk = '10.0.28000.2705'
    wdk = '10.0.28000.2526'
    nugetExeSha256 = $nugetHash
    buildLog = $log
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $worktree 'build-result.json') -Encoding utf8
Write-Output "Experimental driver package built: $($manifest.packageDirectory); installation and runtime validation are separate steps."
