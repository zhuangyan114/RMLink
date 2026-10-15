param([Parameter(Mandatory=$true)][int]$ServiceProcessId, [int]$TimeoutSeconds=30)
$ErrorActionPreference='Stop'
$timer=[Diagnostics.Stopwatch]::StartNew()
$statePath=Join-Path $env:USERPROFILE '.rmlink\connector.json'
while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    if (-not (Get-Process -Id $ServiceProcessId -ErrorAction SilentlyContinue)) { throw 'Connector exited during startup; inspect the service logs.' }
    $response=$null
    try {
        $token=(Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).token
        $request=[Net.HttpWebRequest]::Create('http://127.0.0.1:3243/v1/status')
        $request.Proxy=$null; $request.Timeout=1000; $request.ReadWriteTimeout=1000
        $request.Headers['Authorization']='Bearer '+$token
        $response=$request.GetResponse()
        if ([int]$response.StatusCode -eq 200) { return }
    } catch { } finally { if ($response) { $response.Close() } }
    Start-Sleep -Milliseconds 200
}
throw 'Connector API did not become ready within the startup deadline; inspect the service logs.'
