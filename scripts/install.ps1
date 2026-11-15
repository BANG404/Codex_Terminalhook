[CmdletBinding()]
param([string]$Source)
$ErrorActionPreference = 'Stop'
if (-not $Source) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.agents/plugins/marketplace.json')) { $Source = $PSScriptRoot }
    elseif (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist/package/.agents/plugins/marketplace.json')) { $Source = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist/package' }
    else { $Source = Split-Path -Parent $PSScriptRoot }
}
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$manifest = Join-Path $sourceRoot 'plugins/terminalhook/.codex-plugin/plugin.json'
if (-not (Test-Path -LiteralPath $manifest)) { throw 'Not a TerminalHook marketplace package' }
$binaryPath = Join-Path $sourceRoot 'plugins/terminalhook/bin/terminalhook.exe'
if (-not (Test-Path -LiteralPath $binaryPath)) { throw 'Missing binary; build the source with scripts/package.ps1 first' }
codex plugin marketplace add $sourceRoot --json
if ($LASTEXITCODE -ne 0) { throw 'Marketplace registration failed' }
codex plugin add terminalhook@terminalhook-local --json
if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed' }
$directSetup = Join-Path $PSScriptRoot 'Enable-DirectTools.ps1'
& $directSetup
Write-Output 'TerminalHook installed. Open a new Codex chat to load the updated tools.'
