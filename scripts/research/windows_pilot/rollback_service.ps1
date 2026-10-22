param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$name = 'VeraMonoPilot'
$expectedImage = '"D:\VERA\VeraMonoPilot\python\pythonservice.exe"'
$service = Get-CimInstance Win32_Service -Filter "Name='$name'"
if ($null -eq $service) {
    Write-Output 'PILOT_SERVICE_ALREADY_ABSENT'
    return
}
if ($service.PathName -ne $expectedImage -or $service.StartMode -ne 'Manual' -or
    $service.StartName -ne 'NT AUTHORITY\LocalService') {
    throw 'Refuse rollback: target service identity/configuration has changed'
}
if (-not $Apply) {
    Write-Output 'DRY_RUN_ONLY: would stop and unregister VeraMonoPilot; installation/state retained'
    return
}
if ($service.State -ne 'Stopped') {
    Stop-Service -Name $name -ErrorAction Stop
}
$until = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 250
    $service = Get-CimInstance Win32_Service -Filter "Name='$name'"
} while ($null -ne $service -and $service.State -ne 'Stopped' -and (Get-Date) -lt $until)
if ($null -ne $service -and $service.State -ne 'Stopped') {
    throw 'Refuse delete: pilot service did not stop'
}
$out = & sc.exe delete $name
Write-Output $out
if ($LASTEXITCODE -ne 0) {
    throw 'SCM deletion request failed; inspect existing service before any retry'
}
$until = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 350
    $service = Get-CimInstance Win32_Service -Filter "Name='$name'"
} while ($null -ne $service -and (Get-Date) -lt $until)
if ($null -ne $service) {
    throw 'Service marked for deletion or still present; do not re-register blindly'
}
if (!(Test-Path 'D:\VERA\VeraMonoPilot\state\recovery\checkpoints.sqlite')) {
    throw 'State unexpectedly missing after service removal'
}
Write-Output 'ROLLBACK_REGISTRATION_REMOVED_STATE_PRESERVED'