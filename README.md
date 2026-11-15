# TerminalHook

English | [简体中文](README.zh-CN.md)

[![CI](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml/badge.svg)](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A local Rust MCP server and Codex plugin. While a command runs, TerminalHook continuously reads its output and matches regular expressions. Each call waits until a match, exit, timeout, or cancellation. It does not return `pending`, request model sampling, or require repeated model queries while waiting.

## Installation and upgrades

TerminalHook is distributed as a Git marketplace containing the compiled Windows x64 binary, MCP configuration, and runtime skill. The stable channel tracks this repository's `marketplace` branch; preview channels track `marketplace-alpha`, `marketplace-beta`, or `marketplace-rc`. Each channel becomes available after its first published Release workflow run. Source branches contain no compiled binaries and must not be installed as binary marketplaces.

Development follows the `alpha → beta → rc → main` stage branches. Changes go through PRs: `alpha` receives full validation, and later promotions compare source trees and reuse the original verification artifacts, avoiding duplicate push and PR runs. Stage versions include a commit count and source SHA. Publication is triggered manually through the Release workflow. See [Development, CI/CD, and stage releases](docs/releasing.md). The project uses the [MIT license](LICENSE).

Development tasks automatically submit an alpha PR and merge it after the actual current-source CI succeeds, using `scripts/deliver.py` and the repository's [task completion rules](AGENTS.md). This does not promote later stages or publish a release. See [automatic alpha delivery](docs/releasing.md#task-completion-and-automatic-alpha-delivery).

From a source checkout, install the Git distribution without building Rust:

```powershell
pwsh -NoProfile -File scripts/install.ps1
# Optional: follow a preview channel instead of stable.
pwsh -NoProfile -File scripts/install.ps1 -Channel beta
```

The installer registers the Git marketplace, installs and enables the plugin, and configures native direct calls. Existing local installations migrate while retaining `terminalhook@terminalhook-local` and other user settings. The marketplace name retains its historical `-local` suffix for compatibility; the source is Git. A failed marketplace registration restores the previous source and tracking ref. Runtime requires Windows and Codex CLI; installation from Git also requires Git. Rust, Python, and Node are not required.

Codex's own startup tasks refresh configured Git marketplaces and their installed plugin caches. To request a refresh explicitly:

```powershell
codex plugin marketplace upgrade terminalhook-local
```

Updates follow the selected branch, not GitHub's latest-release API. There is no custom background updater or guaranteed polling interval while the app stays open. Open a new Codex chat after an update. Processes in existing sessions remain managed by their original MCP connection and are not migrated live. See the [official marketplace commands](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace).

For offline installation or local development, download the ZIP from [GitHub Releases](https://github.com/BANG404/Codex_Terminalhook/releases), extract it to a permanent directory, and run `pwsh -NoProfile -File .\install.ps1 -Source .`. Running its installer without `-Source` selects the Git stable channel. CI preview ZIPs are also available in [Actions artifacts](https://github.com/BANG404/Codex_Terminalhook/actions).

To build and install a local source snapshot:

```powershell
pwsh -NoProfile -File scripts/package.ps1
pwsh -NoProfile -File scripts/install.ps1 -Source ./dist/package
```

`package.ps1` generates the ZIP and `dist/SHA256SUMS.txt`. Local installations require the directory to stay available and the installer to be rerun after local changes; they do not download remote updates. In both modes, the installer uses the standard `codex plugin marketplace add` and `codex plugin add` commands and Codex's configuration editing interface to preserve existing settings and add `mcp__terminalhook` to `features.code_mode.direct_only_tool_namespaces`. This administration does not send model requests.

Alternatively, configure a standalone MCP server using `examples/standalone.config.toml`. Choose either standalone MCP or the plugin to avoid duplicate tools. Enablement in a project's `.codex/config.toml` only applies to trusted projects and does not replace installation. The plugin retains the default tool approval policy. MCP commands use the local account's OS permissions and do not inherit the per-command sandbox of Codex's built-in shell.

To uninstall:

```powershell
codex plugin remove terminalhook@terminalhook-local
codex plugin marketplace remove terminalhook-local
```

## Direct calls and terminal tools

### Desktop app direct calls (tested)

Starting with 0.4.0, native direct calls are the default. There are no lifecycle hooks to review or trust. The installer writes the following user configuration automatically. When installing the plugin manually or configuring standalone MCP, add this namespace to the existing array yourself:

```toml
[features.code_mode]
direct_only_tool_namespaces = ["mcp__terminalhook"]
```

Open a new chat to load the configuration, then call `mcp__terminalhook.run_until` directly and await one result, without a `functions.exec` wrapper. Direct exposure is a host setting; the plugin manifest cannot enforce how tools are exposed. If you install the plugin without running the installer, check this setting. Project configuration can override user settings. Older hosts that do not support it must be updated first. Command execution and waiting do not use Codex CLI.

Desktop evidence collected before the direct-call switch: with 0.3.1 model auditing retained and Stop disabled, the tool call took 120.6039 seconds. During the full 120.169-second command execution interval, the target chat made zero model requests. There was one native call and one result, exit code 0, and 12 complete Chinese output entries on each of stdout and stderr. Model audit records existed both before and after execution. This evidence establishes that call path without a Stop-based wait; it does not guarantee the absence of background requests on every host or version. The default 0.4.0 package no longer includes a model audit hook.

If you retain raw model events and call traces locally, inspect them using `scripts/audit_direct_wait.py <rollout.jsonl> <audit.jsonl>`. By default, it reads command start/end times from `artifacts/desktop-direct-call-markers.json`. Test records are not committed to the source repository.

### Historical hook version

The synchronous Stop hook, background submission, and model audit implementation from 0.3.1 are preserved on the [legacy/stop-hook branch](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook). The current version removes `submit_task`, `prepare_task`, `await_tasks`, `audit_event`, and hook definitions. After upgrading, close the MCP connection in old chats and open a new chat to load the new version.

### Terminal tools

| Tool | Behavior |
| --- | --- |
| `run_until` | Start a command and wait for an output condition; terminate on match/timeout by default, or retain the process |
| `wait_output` | Continue waiting with a process ID and cursor; replay received data before waiting for new output |
| `write_input` | Send input and optionally register a wait at the same time, preventing missed immediate responses |
| `stop_process` | Terminate the managed process tree and wait for cleanup |

### Start and wait

```json
{
  "program": "pwsh",
  "args": ["-NoProfile", "-Command", "npm run dev"],
  "cwd": "D:\\Projects\\my-app",
  "until": [{"name": "ready", "pattern": "Local:\\s+(https?://[^\\s]+)\\s"}],
  "match_mode": "window",
  "window_bytes": 65536,
  "timeout_ms": 300000,
  "on_match": "keep_running",
  "on_timeout": "terminate",
  "env": {"FORCE_COLOR": "0"}
}
```

`program` does not run through an implicit shell. Invoke `pwsh` or `cmd.exe` explicitly for pipes, redirection, and `.cmd` files. `cwd` must be absolute. `env` adds to or overrides the server's inherited environment. Stdin stays open by default for later input; use `write_input` with `eof: true` to send EOF.

The default `mode: "pipe"` reads stdout and stderr separately. `streams` can select either or both. Interactive programs can use `mode: "pty"`, backed by Windows ConPTY. `rows` defaults to 24 and `cols` to 120; both accept 1–500. PTY provides only the merged `terminal` output stream, so omit `streams` or set it to `["terminal"]`. The terminal may echo input.

Chinese input/output and native program pipelines in PowerShell require consistent UTF-8 encoding. The plugin skill includes `scripts/Initialize-Utf8.ps1`. Dot-source it in the target PowerShell session before running the command or script, using its actual installed path:

```powershell
. 'D:/Projects/Codex_Terminalhook/plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1'
# Run the target command in the same session, for example:
$line = [Console]::ReadLine()
Write-Output ('ACK:' + $line)
```

For standalone MCP, you can instead start the `-Command` text with `[Console]::InputEncoding = [Console]::OutputEncoding = $OutputEncoding = [System.Text.UTF8Encoding]::new($false)`. Setting only output encoding does not fix garbled input. Running the initializer in a separate child PowerShell does not configure the parent session. TerminalHook executes `program`/`args` as supplied; callers initialize encoding in the target PowerShell session.

Example result (some fields omitted):

```json
{
  "status": "matched",
  "process_id": "proc_...",
  "cursor": 512,
  "latest_cursor": 530,
  "oldest_cursor": 0,
  "process_running": true,
  "exit_code": null,
  "rule": "ready",
  "stream": "stdout",
  "match": "Local: http://localhost:5173/\n",
  "captures": ["http://localhost:5173/"],
  "match_output_truncated": false,
  "output_tail": {"stdout": "...", "stderr": "", "terminal": ""},
  "log_dir": "C:\\...\\proc_...",
  "logs_rotated": false
}
```

A match establishes that the output condition occurred, not that the command exited successfully. `elapsed_ms` measures the process session lifetime; `wait_elapsed_ms` measures the current wait when a match occurs. `bytes_seen` counts raw output bytes. On Windows, `os_pid` is the Job-managed launcher's PID, not the target application's PID.

### Continue from a cursor

```json
{
  "process_id": "proc_...",
  "cursor": 512,
  "until": [{"name": "done", "pattern": "Build completed"}],
  "timeout_ms": 300000
}
```

Pass this to `wait_output`. Use the previous result's `cursor`, not `latest_cursor`, which may skip output that has not been consumed. A cursor is a **normalized output byte position** in receive order. It can point inside a single read, so later markers in the same chunk are not skipped. Treat it as opaque; do not calculate cursors or reuse them across processes.

Each process retains the latest 1 MiB of normalized history. If a cursor points into evicted history, the result is `cursor_expired`, with `oldest_cursor`, `latest_cursor`, log paths, and output tails. Data is never skipped silently. Unconsumed output can be replayed after process exit while history remains available. Exited sessions are retained within the connection's most recent 64 sessions; older IDs expire.

### Send input and wait

```json
{
  "process_id": "proc_...",
  "data": "continue\n",
  "wait": {
    "until": [{"name": "ack", "pattern": "Accepted"}],
    "timeout_ms": 30000
  }
}
```

Pass this to `write_input`. The server captures the cursor before sending input, then waits continuously, without requiring the model to write and poll separately. Omitting `wait` returns `written`. Input is limited to 65536 UTF-8 bytes and must include any newline explicitly; PTY typically uses `\r`. In pipe mode, `eof: true` closes stdin after writing. For PTY EOF, send the control characters recognized by the target application.

Input writes wait at most 5 seconds so an application that does not read stdin cannot block the connection indefinitely. A write timeout terminates the process to prevent an incomplete write from unexpectedly taking effect later. Timeout or cancellation while waiting for a response preserves the process. The same applies to `wait_output`.

### Stop

Pass `{"process_id":"proc_..."}` to `stop_process`. An active process returns `stopped`; an already stopped or unknown ID returns `not_running`. Repeated calls are safe.

## Matching and termination semantics

`until` accepts 1–16 named rules. Names must be unique and at most 128 bytes. Rules are selected in array order against the currently matchable text. Patterns use Rust `regex` syntax: lookaround and backreferences are unsupported, and patterns that may match zero width are rejected. Each regex is limited to 4096 bytes, 32 capture groups, and a compiled size of 2 MiB.

| Mode | Matching scope |
| --- | --- |
| `line` (default) | Complete lines and the current unfinished line, up to 64 KiB per line; no cross-line matching |
| `window` | A separate `window_bytes` (256–262144) byte buffer per output stream; supports cross-line matching, excluding text outside the window |

Output is decoded incrementally as UTF-8, replacing invalid bytes with U+FFFD. Other encodings must be converted by the target program. ESC-prefixed ANSI CSI/OSC sequences are removed. CR/LF mark line boundaries, and CRLF becomes one newline. Terminal screen layout after cursor movement is not emulated. Cursor-position queries needed for PTY startup are answered locally without involving the model.

Partial lines are matched immediately, so `$` only means the currently observed line end. Variable-length captures should include an explicit delimiter. To capture a full URL or other newline-terminated value, use `window` mode and include the delimiter in the regex. Returned match text is limited to 4096 bytes; the match and all captures combined are limited to 8192 bytes. Truncation sets `match_output_truncated` to true. Match decisions and cursors still use the full text.

| Status | Meaning |
| --- | --- |
| `matched` | A regex matched; `run_until` retains or terminates the process according to `on_match` |
| `exited_unmatched` | Natural exit with output drained and no match; includes the natural exit code |
| `timeout` | The wait deadline was reached; `run_until` follows `on_timeout` |
| `cursor_expired` | Requested history was evicted; diagnose it or explicitly select a new starting point |
| `error` | A parameter, startup, read, logging, or matching error |

Both `on_match` and `on_timeout` default to `terminate` and also accept `keep_running`. `timeout_ms` defaults to 300000 and accepts 1–600000. The packaged MCP tool timeout is 660 seconds, leaving time for cleanup and transport. Target programs must flush their output; data not written to the pipe or terminal cannot be observed.

Cancelling `run_until` terminates its process. Cancelling a later wait only removes that wait. Closing the connection cleans up all active processes. After the root command exits naturally, descendants still holding pipes are cleaned up before the final output is drained. Windows uses a Job Object with `KILL_ON_JOB_CLOSE`. A local launcher gate ensures the target starts only after Job assignment, eliminating the startup-to-assignment race. Normal cleanup waits for the Job completion port to report `ACTIVE_PROCESS_ZERO`, confirming descendant exit before returning. Failure to confirm within 10 seconds returns an error. If the MCP server is forcibly killed, OS closure of the Job also cleans up descendants.

## Logs and resource limits

Each process continuously writes raw output to `stdout.log`/`stderr.log` or `terminal.log`. Each file is limited to 2 MiB, with one rotated `.log.1` copy. `logs_rotated` indicates that older output may have been evicted. Stdin is not logged, although terminal echo may appear in output.

Default logs live in a connection-specific temporary directory and are deleted on normal disconnect. Temporary logs for evicted sessions are also deleted. To retain logs beyond the session, set an absolute `log_dir` in `run_until`; the server creates a separate process directory inside it. Persistent logs are not deleted automatically. A forced termination may leave temporary logs, which can be cleaned up through normal OS temporary-file management.

Each connection supports at most 8 active processes and 64 sessions. Output uses bounded queues, with at most 1 MiB of history per process and 4096 bytes of returned output tail per stream. Commands retained after a match continue draining output. The source includes Unix PTY/process-group support, but only Windows x64 is currently validated; untested Unix paths are not a release guarantee.

The plugin only observes commands it launches and cannot take over Codex's built-in terminal. Native terminal session IDs are not output handles the plugin can subscribe to. Processes, cursors, and temporary logs are valid only within the current MCP connection. They are not guaranteed to persist across connections. The plugin does not automatically intercept every built-in shell call. Its skill guides Codex to use these tools when waiting for output conditions.

## Agent infrastructure

Documentation is maintained in English (the default) and Simplified Chinese;
agent workflow skills and scripts use English. The repository includes the
`terminalhook-development` project skill and a helper for installing
`terminal-wait` into a standalone MCP consumer project. See
[Agent workflows and project skills](docs/agent-workflows.md) for setup,
invocation, updates, and verification.

## Verification

```powershell
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --locked
python -m unittest discover -s tests -v

# Protocol regressions exceeding the default 60-second client timeout
$env:TERMINALHOOK_LONG_WAIT = '1'
python -m unittest discover -s tests -v

# Run the standalone 90-second PowerShell Chinese-text task;
# save its summary and raw logs under artifacts/
python -m unittest discover -s tests -k powershell_utf8_long_wait -v

# Extract the release ZIP, run the installer, and test the cached binary
# in an isolated Codex directory
python scripts/verify_plugin.py

# Test local-to-Git migration, startup auto-update, and the official upgrade command
# with a loopback Git server and an isolated Codex home
python scripts/verify_git_marketplace.py

# Extract the release ZIP and verify the hook-free manifest and binary protocol
python scripts/verify_package.py

# After release validation, compare source, ZIP, installed cache, and acceptance records
python scripts/audit_release.py
```

Protocol tests use only the Python 3 standard library. Installation verification uses Python 3.11+ `tomllib` and checks the installer, cached binary, and idempotent configuration updates in an isolated Codex directory without starting a model. Verify the desktop call path in an actual app chat; protocol tests cannot establish host model request counts. Historical CLI verification scripts are optional development diagnostics, not the default plugin runtime or desktop acceptance procedure.

## Implementation references

- [Codex plugin format and marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Codex MCP configuration and timeouts](https://learn.chatgpt.com/docs/extend/mcp)
- [Official Rust MCP SDK](https://github.com/modelcontextprotocol/rust-sdk)
- [portable-pty / ConPTY](https://docs.rs/portable-pty/latest/portable_pty/)
