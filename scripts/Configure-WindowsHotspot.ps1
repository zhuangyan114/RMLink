param([switch]$InspectOnly, [string]$PythonPath = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
[Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime] | Out-Null
[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime] | Out-Null
$connections = @([Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()) + @([Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles())
$hotspotManager = $null
foreach ($connection in $connections) {
    if ($null -eq $connection) { continue }
    try {
        $candidate = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($connection)
        if ($candidate.TetheringOperationalState.ToString() -eq 'On') { $hotspotManager = $candidate; break }
    } catch { }
}
if ($null -eq $hotspotManager) { throw 'No running Windows hotspot found through the local API.' }
$configuration = $hotspotManager.GetCurrentAccessPointConfiguration()
Write-Output ('Windows hotspot state: ' + $hotspotManager.TetheringOperationalState)
Write-Output ('Configured band: ' + $configuration.Band)
if ($InspectOnly) { exit 0 }
# Credentials stay in this process and the child's stdin, not command arguments or files.
$payload = @{ssid=$configuration.Ssid; password=$configuration.Passphrase} | ConvertTo-Json -Compress
$configuration = $null
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $PythonPath
$start.Arguments = '"' + (Join-Path $PSScriptRoot 'configure_windows_hotspot.py') + '"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.EnvironmentVariables['PYTHONPYCACHEPREFIX'] = Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\cache\python'
$child = New-Object System.Diagnostics.Process
$child.StartInfo = $start
$child.Start() | Out-Null
$payloadBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($payload)
$child.StandardInput.BaseStream.Write($payloadBytes, 0, $payloadBytes.Length)
$child.StandardInput.BaseStream.Flush()
$child.StandardInput.BaseStream.Close()
$payloadBytes = $null
$payload = $null
$stdout = $child.StandardOutput.ReadToEnd()
$stderr = $child.StandardError.ReadToEnd()
$child.WaitForExit()
Write-Output $stdout
if ($stderr) { Write-Output $stderr }
exit $child.ExitCode
