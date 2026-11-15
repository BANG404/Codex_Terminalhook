# TerminalHook for Windows x64

English | [简体中文](README.zh-CN.md)

Extract this directory to a permanent location (spaces in the path are supported), then install:

```powershell
pwsh -NoProfile -File .\install.ps1
```

Codex CLI must already be installed. Runtime does not require Rust, Python, or Node. Installation registers this directory as a local marketplace and enables TerminalHook. Keep the directory in place and rerun the installer to update it. Open a new Codex session to load the tools.

Version 0.4.0 uses native direct calls by default, with no Stop or other lifecycle hooks to trust. Through the Codex configuration interface, the installer adds `mcp__terminalhook` to the user's `[features.code_mode].direct_only_tool_namespaces`, preserving other namespaces and settings. This administration does not start a model. In a new chat, call `run_until` directly and await its result. Long waits do not use a `functions.exec` wrapper; command execution and waiting do not use Codex CLI. If you install manually through the desktop UI without running this script, configure the namespace yourself. Project configuration may override user settings. The host must support this configuration.

The historical synchronous Stop hook version is preserved on the [legacy/stop-hook branch](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook). The current version does not provide `submit_task`, `prepare_task`, `await_tasks`, or `audit_event`.

The four terminal tools remain available: `run_until` starts a command and waits for a regex; `wait_output` continues from a cursor; `write_input` sends input and can wait for a response in the same call; `stop_process` cleans up the process tree. Waiting happens locally without a `pending` result that requires model polling.

Example request to Codex:

> Use TerminalHook to run my development server, wait for the Local URL in its output, then return and keep the server running.

Commands require an explicit absolute working directory. Separate stdout/stderr pipes are the default. Interactive commands can use `mode: "pty"` (ConPTY), which provides a merged `terminal` output stream. Regex matching is line-based by default; use bounded `window` mode for cross-line matching. The maximum wait is 10 minutes, and the MCP timeout is 660 seconds.

Commands terminate on match or timeout by default. For services, use `on_match: "keep_running"` and call `stop_process` when finished. An optional absolute `log_dir` retains rotating raw logs. Default temporary logs are deleted when the MCP connection closes.

The plugin manages commands it launches and cannot subscribe to Codex's built-in terminal. Process handles, output cursors, and temporary logs are valid only for the current MCP connection; reconnecting cleans up the original processes. MCP commands use the local account's OS permissions and retain Codex's default tool approval behavior.

To uninstall, run `codex plugin remove terminalhook@terminalhook-local`, then optionally `codex plugin marketplace remove terminalhook-local`.
