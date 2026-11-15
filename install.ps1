[CmdletBinding(DefaultParameterSetName = 'Git')]
param(
    [Parameter(ParameterSetName = 'Git')]
    [ValidateSet('production', 'alpha', 'beta', 'rc')]
    [string]$Channel = 'production',
    [Parameter(ParameterSetName = 'Git')]
    [string]$GitSource = 'https://github.com/BANG404/Codex_Terminalhook.git',
    [Parameter(Mandatory, ParameterSetName = 'Local')]
    [string]$Source
)
$ErrorActionPreference = 'Stop'
$marketplaceName = 'terminalhook-local' # Keep the existing plugin ID and user settings.
$pluginId = "terminalhook@$marketplaceName"
$localInstall = $PSCmdlet.ParameterSetName -eq 'Local'
if ($localInstall) {
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path
    $manifest = Join-Path $sourceRoot 'plugins/terminalhook/.codex-plugin/plugin.json'
    if (-not (Test-Path -LiteralPath $manifest)) { throw 'Not a TerminalHook marketplace package' }
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'plugins/terminalhook/bin/terminalhook.exe'))) {
        throw 'Missing binary; build the source with scripts/package.ps1 first'
    }
    $addArguments = @($sourceRoot)
} else {
    $channelRef = if ($Channel -eq 'production') { 'marketplace' } else { "marketplace-$Channel" }
    $addArguments = @($GitSource, '--ref', $channelRef)
}
$directSetup = Join-Path $PSScriptRoot 'Enable-DirectTools.ps1'
if (-not (Test-Path -LiteralPath $directSetup)) { throw 'Missing Enable-DirectTools.ps1 beside the installer' }
$catalogJson = codex plugin marketplace list --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing marketplace registration' }
$catalog = $catalogJson | ConvertFrom-Json
$existing = @($catalog.marketplaces | Where-Object { $_.name -eq $marketplaceName })
if ($existing.Count -gt 1) { throw 'Ambiguous TerminalHook marketplace registration' }
$restoreArguments = $null
if ($existing.Count -eq 1) {
    $previous = $existing[0]
    if ($previous.marketplaceSource) {
        if ($previous.marketplaceSource.sourceType -ne 'git') { throw 'Unsupported previous marketplace source' }
        $registration = & $directSetup -ReadMarketplace $marketplaceName
        if (-not $registration -or $registration.source_type -ne 'git') { throw 'Cannot read the previous user marketplace registration' }
        $restoreArguments = @($registration.source)
        if ($registration.ref) { $restoreArguments += @('--ref', $registration.ref) }
        foreach ($sparsePath in $registration.sparse_paths) { $restoreArguments += @('--sparse', $sparsePath) }
    } else {
        $restoreArguments = @($previous.root)
    }
    codex plugin marketplace remove $marketplaceName --json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot migrate TerminalHook marketplace source' }
}
$added = $null
try {
    $addedJson = codex plugin marketplace add @addArguments --json
    if ($LASTEXITCODE -ne 0) { throw 'Marketplace registration failed; ensure this channel has a published binary distribution' }
    $added = $addedJson | ConvertFrom-Json
    if ($added.marketplaceName -ne $marketplaceName) { throw "Unexpected marketplace name: $($added.marketplaceName)" }
    $installedBinary = Join-Path $added.installedRoot 'plugins/terminalhook/bin/terminalhook.exe'
    if (-not (Test-Path -LiteralPath $installedBinary -PathType Leaf)) { throw 'Git marketplace does not contain the compiled Windows binary' }
    Write-Output $addedJson
    codex plugin add $pluginId --json
    if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed' }
    & $directSetup
} catch {
    # Keep the plugin and its settings; restore registration after a failed migration.
    if ($added -and -not $added.alreadyAdded) {
        codex plugin marketplace remove $added.marketplaceName --json | Out-Null
    }
    if ($restoreArguments) {
        codex plugin marketplace add @restoreArguments --json | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not restore the previous TerminalHook marketplace source' }
    }
    throw
}
if ($localInstall) {
    Write-Output 'TerminalHook installed from a local directory. Remote updates require a Git installation.'
} else {
    Write-Output "TerminalHook tracks $channelRef through Codex Git marketplace updates."
}
Write-Output 'Open a new Codex chat to load the updated tools.'
