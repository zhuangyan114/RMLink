param([Parameter(Mandatory=$true)][string]$BinDirectory)
$ErrorActionPreference = 'Stop'
$bin = [IO.Path]::GetFullPath($BinDirectory).TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $bin 'rmlink.exe'))) { throw 'RMLink executable is missing.' }
if (-not ('RMLinkCommandLocation' -as [type])) {
    Add-Type -TypeDefinition 'using System; using System.Text; using System.Runtime.InteropServices; public static class RMLinkCommandLocation { [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern uint GetFinalPathNameByHandle(IntPtr handle, StringBuilder path, uint length, uint flags); }'
}
$stream = [IO.File]::OpenRead((Join-Path $bin 'rmlink.exe'))
try {
    $buffer = [Text.StringBuilder]::new(32768)
    $length = [RMLinkCommandLocation]::GetFinalPathNameByHandle($stream.SafeFileHandle.DangerousGetHandle(),$buffer,32768,0)
    if (-not $length -or $length -ge 32768) { throw 'Cannot resolve the physical executable path.' }
    $physicalPath = $buffer.ToString()
    if ($physicalPath -match '\\Packages\\[^\\]+\\LocalCache\\') {
        throw "The executable is redirected into a package-private cache: $physicalPath. Install under USERPROFILE instead of LOCALAPPDATA."
    }
} finally { $stream.Dispose() }
$originalEntries = @($env:Path -split ';' | Where-Object { $_ } | ForEach-Object {
    [Environment]::ExpandEnvironmentVariables($_).Trim('"').TrimEnd('\')
})
$userPath = [Environment]::GetEnvironmentVariable('Path','User')
$legacyBin = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'RMLink\bin')).TrimEnd('\')
$entries = @($userPath -split ';' | Where-Object {
    $_ -and [Environment]::ExpandEnvironmentVariables($_).Trim('"').TrimEnd('\') -ine $legacyBin
})
if (-not ($entries | Where-Object { [Environment]::ExpandEnvironmentVariables($_).Trim('"').TrimEnd('\') -ieq $bin })) {
    $entries += $bin
}
$newUserPath = $entries -join ';'
if ($newUserPath -cne $userPath) { [Environment]::SetEnvironmentVariable('Path',$newUserPath,'User') }
$processEntries = @($env:Path -split ';' | Where-Object { $_ -and $_.Trim('"').TrimEnd('\') -ine $legacyBin })
if (-not ($processEntries | Where-Object { $_.Trim('"').TrimEnd('\') -ieq $bin })) { $processEntries += $bin }
$env:Path = $processEntries -join ';'

# Existing Windows Terminal windows retain their old environment. Put a small
# launcher in an already-configured user command directory when one is present.
$defaultBin = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.rmlink\app\bin')).TrimEnd('\')
$shimInstalled = $null
if ($bin -ieq $defaultBin) {
    foreach ($candidate in @((Join-Path $env:USERPROFILE 'bin'),(Join-Path $env:USERPROFILE '.local\bin'))) {
        if (-not ($originalEntries | Where-Object { $_ -ieq $candidate.TrimEnd('\') })) { continue }
        $shim = Join-Path $candidate 'rmlink.cmd'
        $marker = 'rem RMLink managed command shim'
        if (Test-Path -LiteralPath $shim) {
            if (-not (Select-String -LiteralPath $shim -SimpleMatch $marker -Quiet)) {
                throw "An unrelated rmlink.cmd already exists: $shim"
            }
        }
        New-Item -ItemType Directory -Force $candidate | Out-Null
        $content = "@echo off`r`n$marker`r`n" + '"%USERPROFILE%\.rmlink\app\bin\rmlink.exe" %*' + "`r`nexit /b %errorlevel%`r`n"
        [IO.File]::WriteAllText($shim,$content,[Text.Encoding]::ASCII)
        $shimInstalled = $shim
        break
    }
}
if (-not ('RMLinkEnvironmentNotifyAsync' -as [type])) {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class RMLinkEnvironmentNotifyAsync { [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool SendNotifyMessage(IntPtr hWnd, uint msg, UIntPtr wParam, string lParam); }'
}
$null = [RMLinkEnvironmentNotifyAsync]::SendNotifyMessage([IntPtr]0xffff,0x1a,[UIntPtr]::Zero,'Environment')
Write-Output "Registered command directory: $bin"
Write-Output "Physical executable: $physicalPath"
if ($shimInstalled) { Write-Output "Existing-terminal launcher: $shimInstalled" }
else { Write-Output 'Fully exit Windows Terminal and reopen it to refresh an existing terminal host environment.' }
