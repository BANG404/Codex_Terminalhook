param(
    [string]$DesktopPath,
    [string]$HostAdapterPath = (Join-Path $PSScriptRoot 'plugins/terminalhook/bin/terminalhook-desktop-host.exe'),
    [string]$CodexHome,
    [string]$IsolatedUiProfile
)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
if (-not $IsWindows) { throw 'This launcher requires Windows PowerShell 7.' }
if (-not $DesktopPath) {
    $package = Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1
    if (-not $package) { throw 'Codex Desktop is not installed. Supply -DesktopPath.' }
    $DesktopPath = Join-Path $package.InstallLocation 'app/ChatGPT.exe'
}
$DesktopPath = (Resolve-Path -LiteralPath $DesktopPath).Path
$HostAdapterPath = (Resolve-Path -LiteralPath $HostAdapterPath).Path
$nativeCli = Join-Path (Split-Path $DesktopPath) 'resources/codex.exe'
if (-not (Test-Path -LiteralPath $nativeCli -PathType Leaf)) { throw 'The Desktop native CLI is missing.' }
if (-not $IsolatedUiProfile) {
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $DesktopPath }
    if ($running) { throw 'Close Codex Desktop first, then run this launcher. The current app was left running.' }
}
$changes = @{
    CODEX_CLI_PATH = $HostAdapterPath
    TERMINALHOOK_DESKTOP_CODEX_BIN = $nativeCli
}
if ($CodexHome) { $changes.CODEX_HOME = (Resolve-Path -LiteralPath $CodexHome).Path }
$arguments = @()
if ($IsolatedUiProfile) {
    if (-not $CodexHome) { throw 'An isolated UI profile also requires a separate -CodexHome.' }
    $normalHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    if ([IO.Path]::GetFullPath($CodexHome).TrimEnd('\', '/') -ieq [IO.Path]::GetFullPath($normalHome).TrimEnd('\', '/')) {
        throw 'The isolated test must use a Codex home separate from the current app.'
    }
    $profilePath = [IO.Path]::GetFullPath($IsolatedUiProfile)
    $changes.CODEX_ELECTRON_USER_DATA_PATH = $profilePath
    $arguments = @('--user-data-dir="' + $profilePath + '"')
}
$previous = @{}
try {
    foreach ($key in $changes.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $changes[$key], 'Process')
    }
    $start = @{ FilePath = $DesktopPath; PassThru = $true; WindowStyle = 'Hidden' }
    if ($arguments.Count) { $start.ArgumentList = $arguments }
    $process = Start-Process @start
    [pscustomobject]@{ process_id = $process.Id; desktop = $DesktopPath; adapter = $HostAdapterPath } | ConvertTo-Json
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
