[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,80}$')]
    [string]$InstallId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$PortalSha,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$VeraMonoSha,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$PreActiveSha,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$VolitionSha,

    [string]$PortalRef = 'work/portal-desktop-vera-runtime-v1',
    [string]$RuntimeRoot,
    [string]$Python,
    [switch]$DryRun,
    [switch]$AllowLocalNoPaidCompute,
    [switch]$Activate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is unavailable; supply a dedicated user-local RuntimeRoot.'
    }
    $RuntimeRoot = Join-Path $env:LOCALAPPDATA "P.O.R.T.A.L.\runtimes\$InstallId"
}

if ([string]::IsNullOrWhiteSpace($Python)) {
    if ($null -ne (Get-Command py -ErrorAction SilentlyContinue)) {
        $resolvedPython = @(& py -3.12 -c 'import sys; print(sys.executable)')
        if ($LASTEXITCODE -eq 0 -and $resolvedPython.Count -gt 0) {
            $Python = [string]$resolvedPython[0]
        }
    }
    if ([string]::IsNullOrWhiteSpace($Python)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw 'Python >=3.12 is required. Install it, then supply -Python with its executable path.'
        }
        $Python = $pythonCommand.Source
    }
}

$portalPackage = Join-Path (Split-Path -Parent $PSScriptRoot) 'portal'
$installerArgs = @(
    '--runtime-root', [System.IO.Path]::GetFullPath($RuntimeRoot),
    '--install-id', $InstallId,
    '--portal-sha', $PortalSha,
    '--portal-ref', $PortalRef,
    '--vera-mono-sha', $VeraMonoSha,
    '--pre-active-sha', $PreActiveSha,
    '--volition-sha', $VolitionSha
)
if ($DryRun) { $installerArgs += '--dry-run' }
if ($AllowLocalNoPaidCompute) { $installerArgs += '--allow-local-no-paid-compute' }
if ($Activate) { $installerArgs += '--activate' }

# The installer uses only the standard library until it creates its isolated
# environment. Load that entry point without importing Portal's optional host
# dependencies into the bootstrap interpreter.
$entryPoint = "import sys,types,runpy; sys.version_info < (3,12) and sys.exit('Python >=3.12 is required'); package=types.ModuleType('portal'); package.__path__=[sys.argv.pop(1)]; sys.modules['portal']=package; runpy.run_module('portal.desktop_installer',run_name='__main__')"
& $Python -c $entryPoint $portalPackage @installerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Installer failed. Inspect $RuntimeRoot\logs\install.log and runtime.stderr.log; activation has not been confirmed."
}
