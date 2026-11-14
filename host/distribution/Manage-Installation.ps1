param(
    [Parameter(Mandatory=$true)][ValidateSet('Prepare','Configure','Remove')][string]$Action,
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [ValidateSet('on','off')][string]$AddToPath = 'on',
    [ValidateSet('on','off')][string]$StartAtLogon = 'off',
    [string]$ErrorFile
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell must use its own modules when launched from PowerShell 7.
$env:PSModulePath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules') + ';' +
    (Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules')
$directory = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
$bin = Join-Path $directory 'bin'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Get-InstalledProcesses {
    Get-Process -Name rmlink,RMLinkConnector -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($bin + '\', [StringComparison]::OrdinalIgnoreCase)
    }
}

function Get-ServiceStopCommands($Processes) {
    if (-not ('RMLinkInstallCommandLine' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RMLinkInstallCommandLine {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    public static string[] Split(string command) {
        int count; IntPtr pointer = CommandLineToArgvW(command, out count);
        if (pointer == IntPtr.Zero) throw new InvalidOperationException("Cannot read service arguments");
        try {
            string[] result = new string[count];
            for (int i=0; i<count; i++) result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer, i*IntPtr.Size));
            return result;
        } finally { LocalFree(pointer); }
    }
}
'@
    }
    $commands = @{}
    foreach ($process in $Processes) {
        $information = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $process.Id)
        if (-not $information) { continue }
        if (-not $information.CommandLine) { throw 'Cannot inspect the installed service. Stop it manually first.' }
        $arguments = [RMLinkInstallCommandLine]::Split($information.CommandLine)
        $options = @()
        for ($index=1; $index -lt $arguments.Length; $index++) {
            if ($arguments[$index] -in @('--state-dir','--api-port')) {
                if ($index + 1 -ge $arguments.Length) { throw 'Invalid service arguments. Stop it manually first.' }
                $options += @($arguments[$index],$arguments[$index + 1])
                $index++
            } elseif ($arguments[$index] -match '^--(state-dir|api-port)=') {
                $options += $arguments[$index]
            }
        }
        # PyInstaller has a parent and child with the same command line.
        $commands[(ConvertTo-Json -InputObject @($options) -Compress)] = $options
    }
    foreach ($options in $commands.Values) { [pscustomobject]@{ Arguments = @($options) } }
}

function Remove-CommandPath {
    $oldPath = [Environment]::GetEnvironmentVariable('Path','User')
    $entries = @($oldPath -split ';' | Where-Object {
        [Environment]::ExpandEnvironmentVariables($_).Trim('"').TrimEnd('\') -ine $bin
    })
    $newPath = $entries -join ';'
    if ($null -ne $oldPath -and $oldPath -cne $newPath) {
        [Environment]::SetEnvironmentVariable('Path',$newPath,'User')
    }
    # Remove only the exact launcher produced by the legacy portable installer.
    $defaultBin = Join-Path $env:USERPROFILE '.rmlink\app\bin'
    if ($bin -ieq $defaultBin) {
        foreach ($folder in @('bin','.local\bin')) {
            $shim = Join-Path $env:USERPROFILE "$folder\rmlink.cmd"
            if (Test-Path -LiteralPath $shim -PathType Leaf) {
                $content = [IO.File]::ReadAllText($shim)
                if ($content.Contains('rem RMLink managed command shim') -and
                    $content.Contains('"%USERPROFILE%\.rmlink\app\bin\rmlink.exe" %*')) {
                    Remove-Item -LiteralPath $shim
                }
            }
        }
    }
}

try {
    if ($Action -eq 'Prepare') {
        $processes = @(Get-InstalledProcesses)
        if ($processes | Where-Object { $_.ProcessName -eq 'rmlink' }) {
            throw 'Close the RMLink console before continuing.'
        }
        if ($processes) {
            # The client refuses shutdown while a target session owns a device.
            # Never terminate a process or stop a service from another directory.
            $client = Join-Path $bin 'rmlink.exe'
            if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
                throw 'The installed client is missing. Stop its background service before continuing.'
            }
            foreach ($command in (Get-ServiceStopCommands $processes)) {
                $options = $command.Arguments
                $output = & $client @options --json service stop 2>&1
                if ($LASTEXITCODE -ne 0) {
                    throw ('Cannot stop RMLink. End active debug sessions first. ' + ($output -join ' '))
                }
            }
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            while (@(Get-InstalledProcesses).Count -and [DateTime]::UtcNow -lt $deadline) {
                Start-Sleep -Milliseconds 200
            }
            if (@(Get-InstalledProcesses).Count) { throw 'RMLink is still exiting. Try again in a few seconds.' }
        }
        exit 0
    }

    if ($Action -eq 'Configure' -and $AddToPath -eq 'on') {
        & (Join-Path $directory 'Register-Command.ps1') -BinDirectory $bin
    } else {
        Remove-CommandPath
    }
    $existing = (Get-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector' -ErrorAction SilentlyContinue).RMLinkConnector
    $startupScript = Join-Path $directory 'Start-Service.ps1'
    if ($Action -eq 'Configure' -and $StartAtLogon -eq 'on') {
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $command = '"' + $powershell + '" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $startupScript + '"'
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector' -PropertyType String -Value $command -Force | Out-Null
    } elseif ($existing -and ($existing.IndexOf($startupScript,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                             $existing.IndexOf((Join-Path $directory 'Start.ps1'),[StringComparison]::OrdinalIgnoreCase) -ge 0)) {
        Remove-ItemProperty -LiteralPath $runKey -Name 'RMLinkConnector'
    }
    if ($Action -eq 'Remove') {
        Write-Output 'Removed command and startup registration. User settings and recordings were preserved.'
    }
} catch {
    if ($ErrorFile) { [IO.File]::WriteAllText($ErrorFile, $_.Exception.Message, [Text.UTF8Encoding]::new($false)) }
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
