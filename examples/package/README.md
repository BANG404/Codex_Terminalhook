# TerminalHook native distribution

English | [简体中文](README.zh-CN.md)

Extract the ZIP matching your OS/architecture (paths with spaces are supported). `platform.json` identifies this package. On Windows, install the Git stable channel:

```powershell
pwsh -NoProfile -File .\install.ps1
```

On Linux/macOS, install with Python 3.11+ and the POSIX launcher:

```sh
sh ./install.sh
# Preview channel:
sh ./install.sh --channel alpha
# Offline/local installation of this package:
sh ./install.sh --source .
```

Codex CLI and Git must already be installed for Git installation. Windows uses `marketplace` or `marketplace-alpha/beta/rc`; Linux/macOS append the native platform (`-linux-x64`, `-linux-arm64`, `-macos-x64`, or `-macos-arm64`). Installers select the native ref automatically. Use Windows `-Channel alpha/beta/rc` or Unix `--channel alpha/beta/rc` for previews. Each platform/channel requires its first published Release workflow run. Codex's startup marketplace refresh updates the cache; explicitly refresh with `codex plugin marketplace upgrade terminalhook-local`. The historical marketplace name and plugin ID are retained. Failed registration restores the previous source and tracking branch. Open a new Codex session after updates; active processes are not migrated.

Runtime does not require Rust, Python, or Node. Unix installation alone needs Python 3.11+; Windows installation uses PowerShell 7. Offline Windows installation uses `pwsh -NoProfile -File ./install.ps1 -Source .`. Keep local installation directories in place; local mode needs no Git and does not download future updates. Without `-Source` / `--source`, installers select the Git distribution. Preserve ZIP executable permissions when extracting on Unix (for example with `unzip`). Linux builds use glibc compatible with Ubuntu 24.04; macOS binaries are unsigned and not notarized.

This release exposes background-task tools only. The former `run_until`,
`wait_output`, `write_input`, and `stop_process` MCP interfaces, connection-local
sessions, cursor replay and PTY backend have been removed. Use the host terminal
for foreground or interactive work. Command stdin is closed; background output
uses separate stdout/stderr pipes. A natural result has `status: "exited"` and the
actual exit code, without a process handle or output cursor.

The installer adds `mcp__terminalhook` to
`[features.code_mode].direct_only_tool_namespaces`, preserving other settings.
Manual installations need the same host configuration. Open a new chat after an
upgrade to load the changed tool catalog. Current packages use native events and
host callbacks without lifecycle hooks. The historical Stop-hook implementation
remains on [legacy/stop-hook](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook).

Example request to Codex:

> Submit my build as one TerminalHook background task, then deliver its result to this chat when it completes.

For long background work, first call `check_background`, then `submit_task` with a stable `task_key`, program/args, and absolute `cwd`. Omit `timeout_ms` for no execution deadline; omit `until` for natural exit. The call returns immediately. End the turn and await completion delivery to the originating chat; do not poll the model. Background workers and their saved results survive MCP disconnection, but execution does not resume after a reboot. `list_tasks` opens the compact English MCP Apps dashboard in compatible hosts; `get_task`, `cancel_task`, and `retry_delivery` support inspection and recovery. A running shared Codex daemon, CLI proxy, and host-provided `threadId` metadata are required. Delivery failures preserve results; uncertain submissions are never blindly replayed. See the [full guide](https://github.com/BANG404/Codex_Terminalhook/blob/alpha/docs/background-tasks.md).

For Windows Desktop hosts that normally use stdio, close Desktop and run `pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1` from this extracted package. The opt-in adapter gives Desktop and the worker one shared core without changing persistent settings or command permissions. Existing chats must be reopened in that session. See the full guide for source-build and isolated-test options. Normal Desktop startup restores the default transport.

To uninstall, run `codex plugin remove terminalhook@terminalhook-local`, then optionally `codex plugin marketplace remove terminalhook-local`.
