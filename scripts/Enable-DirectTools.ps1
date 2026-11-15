[CmdletBinding()]
param([string]$CodexPath = 'codex', [string]$ReadMarketplace)
$ErrorActionPreference = 'Stop'

# Use Codex's TOML editor and version lease rather than rewriting user settings.
# This starts only an administrative app-server; it creates no model turn.
$codexExecutable = (Get-Command $CodexPath -ErrorAction Stop).Source
$codexConfigRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
[System.IO.Directory]::CreateDirectory($codexConfigRoot) | Out-Null
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $codexExecutable
$startInfo.Arguments = 'app-server --listen stdio://'
$startInfo.WorkingDirectory = (Resolve-Path -LiteralPath $codexConfigRoot).Path
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
$startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
$server = [System.Diagnostics.Process]::new()
$server.StartInfo = $startInfo

function Invoke-ConfigRequest([int]$RequestId, [string]$Method, [hashtable]$Params) {
    $server.StandardInput.WriteLine((@{id=$RequestId; method=$Method; params=$Params} | ConvertTo-Json -Depth 100 -Compress))
    $server.StandardInput.Flush()
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $lineTask = $server.StandardOutput.ReadLineAsync()
        $remaining = [Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (-not $lineTask.Wait($remaining)) { throw "Codex config request timed out: $Method" }
        $line = $lineTask.Result
        if ($null -eq $line) { throw 'Codex app-server exited before config response' }
        $message = ConvertFrom-Json -InputObject $line -AsHashtable -Depth 100
        if ($message.ContainsKey('id') -and $message.id -eq $RequestId) {
            # Do not echo config values, protocol payloads, or stderr: they may contain secrets.
            if ($message.ContainsKey('error')) { throw "Codex rejected config request: $Method (code $($message.error.code))" }
            return $message.result
        }
    }
    throw "Codex config request timed out: $Method"
}

try {
    if (-not $server.Start()) { throw 'Cannot start Codex configuration service' }
    $stderrDrain = $server.StandardError.ReadToEndAsync()
    $null = Invoke-ConfigRequest 1 'initialize' @{clientInfo=@{name='terminalhook-installer'; version='0.4.0'}}
    $server.StandardInput.WriteLine('{"method":"initialized"}')
    $server.StandardInput.Flush()
    $snapshot = Invoke-ConfigRequest 2 'config/read' @{includeLayers=$true}
    $userLayers = @($snapshot.layers | Where-Object { $_.name.type -eq 'user' -and -not $_.name.profile })
    if ($userLayers.Count -ne 1) { throw 'Cannot identify the base user config layer' }
    $userLayer = $userLayers[0]
    if ($ReadMarketplace) {
        # Marketplace list omits the tracking ref and sparse paths. Return only this
        # registration, never the full config, so installers can restore it exactly.
        return $userLayer.config.marketplaces[$ReadMarketplace]
    }
    $existing = $userLayer.config.features.code_mode.direct_only_tool_namespaces
    if ($null -ne $existing -and $existing -isnot [array]) { throw 'Existing direct-only namespaces must be an array' }
    $namespaces = @($existing)
    $namespaces = @($namespaces | Where-Object { $null -ne $_ })
    if ('mcp__terminalhook' -cnotin $namespaces) { $namespaces += 'mcp__terminalhook' }
    if ('mcp__terminalhook' -cnotin @($existing)) {
        $null = Invoke-ConfigRequest 3 'config/batchWrite' @{
            filePath=$userLayer.name.file
            expectedVersion=$userLayer.version
            edits=@(@{keyPath='features.code_mode.direct_only_tool_namespaces'; value=$namespaces; mergeStrategy='replace'})
        }
    }
    Write-Output 'TerminalHook direct calls enabled in user config; other namespaces preserved.'
} finally {
    if ($server.Id -and -not $server.HasExited) {
        $server.StandardInput.Close()
        if (-not $server.WaitForExit(3000)) { $server.Kill($true); $server.WaitForExit() }
    }
    $server.Dispose()
}
