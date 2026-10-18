$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime] | Out-Null
    [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime] | Out-Null
    $profiles = @([Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()) + @([Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles())
    $selected = $null
    foreach ($profile in $profiles) {
        if ($null -eq $profile) { continue }
        try {
            $candidate = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
            if ($null -eq $selected) { $selected = $candidate }
            if ($candidate.TetheringOperationalState.ToString() -eq 'On') { $selected = $candidate; break }
        } catch { }
    }
    if ($null -eq $selected) {
        @{supported=$true; connected=$false; message='Open Windows Settings > Network & Internet > Mobile hotspot; select 2.4 GHz.'} | ConvertTo-Json -Compress
    } else {
        $on = $selected.TetheringOperationalState.ToString() -eq 'On'
        $band = if ($on) { $selected.GetCurrentAccessPointConfiguration().Band.ToString() } else { $null }
        @{supported=$true; connected=$on; band=$band; message='RMLink uses a 2.4 GHz hotspot. This check does not print credentials.'} | ConvertTo-Json -Compress
    }
} catch {
    @{supported=$false; connected=$null; message='Hotspot state unavailable. Check Windows Settings > Network & Internet > Mobile hotspot.'} | ConvertTo-Json -Compress
}
