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

Version 0.4.0 uses native direct calls by default, with no Stop or other lifecycle hooks to trust. Through the Codex configuration interface, the installer adds `mcp__terminalhook` to the user's `[features.code_mode].direct_only_tool_namespaces`, preserving other namespaces and settings. This administration does not start a model. In a new chat, call `run_until` directly and await its result. Long waits do not use a `functions.exec` wrapper; command execution and waiting do not use Codex CLI. If you install manually through the desktop UI without running this script, configure the namespace yourself. Project configuration may override user settings. The host must support this configuration.

The historical synchronous Stop hook version is preserved on the [legacy/stop-hook branch](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook). The current version has no `prepare_task`, `await_tasks`, or `audit_event`. The new `submit_task` uses an independent durable worker and existing-daemon delivery, without lifecycle hooks.

The four terminal tools remain available: `run_until` starts a command and waits for a regex; `wait_output` continues from a cursor; `write_input` sends input and can wait for a response in the same call; `stop_process` cleans up the process tree. Waiting happens locally without a `pending` result that requires model polling.

Example request to Codex:

> Use TerminalHook to run my development server, wait for the Local URL in its output, then return and keep the server running.

Commands require an explicit native absolute working directory. Separate stdout/stderr pipes are the default. Interactive commands can use `mode: "pty"` (Windows ConPTY or Unix PTYs), which provides a merged `terminal` output stream. Invoke `sh -c` / `bash -lc` explicitly for Unix shell syntax. Regex matching is line-based by default; use bounded `window` mode for cross-line matching. Ordinary tool waits have a 10-minute maximum, and the MCP timeout is 660 seconds. Explicit background submissions are independent of these wait limits.

Commands terminate on match or timeout by default. For services, use `on_match: "keep_running"` and call `stop_process` when finished. An optional absolute `log_dir` retains rotating raw logs. Default temporary logs are deleted when the MCP connection closes.

The plugin manages commands it launches and cannot subscribe to Codex's built-in terminal. Ordinary process handles, output cursors, and temporary logs are valid only for the current MCP connection; reconnecting cleans up the original processes. MCP commands use the local account's OS permissions and retain Codex's default tool approval behavior.

For long background work, first call `check_background`, then `submit_task` with a stable `task_key`, program/args, and absolute `cwd`. Omit `timeout_ms` for no execution deadline; omit `until` for natural exit. The call returns immediately. End the turn and await completion delivery to the originating chat; do not poll the model. Background workers and their saved results survive MCP disconnection, but execution does not resume after a reboot. `list_tasks` opens the compact English MCP Apps dashboard in compatible hosts; `get_task`, `cancel_task`, and `retry_delivery` support inspection and recovery. A running shared Codex daemon, CLI proxy, and host-provided `threadId` metadata are required. Delivery failures preserve results; uncertain submissions are never blindly replayed. See the [full guide](https://github.com/BANG404/Codex_Terminalhook/blob/alpha/docs/background-tasks.md).

For Windows Desktop hosts that normally use stdio, close Desktop and run `pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1` from this extracted package. The opt-in adapter gives Desktop and the worker one shared core without changing persistent settings or command permissions. Existing chats must be reopened in that session. See the full guide for source-build and isolated-test options. Normal Desktop startup restores the default transport.

To uninstall, run `codex plugin remove terminalhook@terminalhook-local`, then optionally `codex plugin marketplace remove terminalhook-local`.
