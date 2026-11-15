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
$catalogJson = codex plugin marketplace list --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing marketplace registration' }
$catalog = $catalogJson | ConvertFrom-Json
$existing = @($catalog.marketplaces | Where-Object { $_.name -eq 'terminalhook-local' })
$previousSource = $null
if ($existing.Count -gt 1) { throw 'Ambiguous TerminalHook marketplace registration' }
if ($existing.Count -eq 1) {
    $oldRoot = [System.IO.Path]::GetFullPath($existing[0].root).TrimEnd('\')
    $newRoot = [System.IO.Path]::GetFullPath($sourceRoot).TrimEnd('\')
    if (-not $oldRoot.Equals($newRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        if ($existing[0].marketplaceSource) { throw 'Refusing to replace a nonlocal TerminalHook marketplace source' }
        $previousSource = $existing[0].root
        codex plugin marketplace remove terminalhook-local --json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot migrate TerminalHook marketplace source' }
    }
}
try {
    codex plugin marketplace add $sourceRoot --json
    if ($LASTEXITCODE -ne 0) { throw 'Marketplace registration failed' }
} catch {
    if ($previousSource) {
        codex plugin marketplace add $previousSource --json
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not restore the previous TerminalHook marketplace source' }
    }
    throw
}
codex plugin add terminalhook@terminalhook-local --json
if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed' }
$directSetup = Join-Path $PSScriptRoot 'Enable-DirectTools.ps1'
& $directSetup
Write-Output 'TerminalHook installed. Open a new Codex chat to load the updated tools.'
