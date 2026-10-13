param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root 'docs\reference-versions.json') -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force (Join-Path $root 'reference') | Out-Null
foreach ($repo in $manifest.repositories) {
    $path = Join-Path $root "reference\$($repo.directory)"
    if (-not (Test-Path -LiteralPath $path)) {
        & git clone --no-checkout --filter=blob:none $repo.url $path
        if ($LASTEXITCODE -ne 0) { throw "Clone failed: $($repo.directory)" }
        & git -C $path checkout --detach $repo.commit
        if ($LASTEXITCODE -ne 0) { throw "Checkout failed: $($repo.directory)" }
    }
    $head = & git -C $path rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head -ne $repo.commit) {
        throw "Existing reference differs from pin; preserved without reset: $path"
    }
    Write-Output "$($repo.directory): $head"
}
