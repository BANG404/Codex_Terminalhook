[CmdletBinding()]
param([ValidateSet('auto','alpha','beta','rc','production','development')][string]$Channel = 'auto')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
$previousVersion = $env:TERMINALHOOK_BUILD_VERSION
try {
    python scripts/version.py --channel $Channel --output dist/build-info.json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the source version' }
    $buildInfo = [System.IO.File]::ReadAllText((Join-Path $projectRoot 'dist/build-info.json')) | ConvertFrom-Json
    $env:TERMINALHOOK_BUILD_VERSION = $buildInfo.version
    cargo build --release --locked
    if ($LASTEXITCODE -ne 0) { throw 'Rust release build failed' }
    python scripts/package_plugin.py
    if ($LASTEXITCODE -ne 0) { throw 'Plugin packaging failed' }
} finally {
    if ($null -eq $previousVersion) { Remove-Item Env:TERMINALHOOK_BUILD_VERSION -ErrorAction SilentlyContinue }
    else { $env:TERMINALHOOK_BUILD_VERSION = $previousVersion }
    Pop-Location
}
