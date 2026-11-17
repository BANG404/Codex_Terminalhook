# TerminalHook

English | [简体中文](README.zh-CN.md)

[![CI](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml/badge.svg)](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

TerminalHook is a Rust MCP server and Codex plugin for durable background commands,
event-driven task dashboards, and completion delivery to the originating chat.
Submit authorized work once, end the turn, and receive its saved result when it
finishes. Native workers wait without model polling and survive MCP disconnection.

Native testing and distribution cover Windows x64, Linux x64/ARM64, and macOS
Intel/Apple Silicon. Commands use separate stdout/stderr pipes and closed stdin.
Windows Job Objects and Unix process groups contain command descendants.

This release removes the former synchronous/interactive MCP tools:
`run_until`, `wait_output`, `write_input`, and `stop_process`. Their dispatch paths,
connection-local sessions, cursor replay, retained-process controls, and PTY
backend are removed too. Existing integrations must migrate to the background
workflow below; use the host terminal for foreground or interactive commands.

[Installation](#installation-and-upgrades) · [Task tools](#task-tools) ·
[Releases](https://github.com/BANG404/Codex_Terminalhook/releases) ·
[Development and releases](docs/releasing.md)

## Installation and upgrades

TerminalHook is distributed as a Git marketplace containing the native binary, MCP configuration, and runtime skill. Installers select the current OS and architecture. Windows retains `marketplace` and `marketplace-alpha/beta/rc`; Unix branches append `-linux-x64`, `-linux-arm64`, `-macos-x64`, or `-macos-arm64` to the channel branch. Each platform/channel becomes available after its first published Release workflow run. Source branches contain no compiled binaries and must not be installed as binary marketplaces.

Development follows the `alpha → beta → rc → main` stage branches. Changes go through PRs: `alpha` receives full validation, and later promotions compare source trees and reuse the original verification artifacts, avoiding duplicate push and PR runs. Stage versions include a commit count and source SHA. Publication is triggered manually through the Release workflow. See [Development, CI/CD, and stage releases](docs/releasing.md). The project uses the [MIT license](LICENSE).

Development tasks automatically submit an alpha PR and merge it after the actual current-source CI succeeds, using `scripts/deliver.py` and the repository's [task completion rules](AGENTS.md). This does not promote later stages or publish a release. See [automatic alpha delivery](docs/releasing.md#task-completion-and-automatic-alpha-delivery).

From a source checkout, install the Git distribution without building Rust:

```powershell
pwsh -NoProfile -File scripts/install.ps1
# Optional: follow a preview channel instead of stable.
pwsh -NoProfile -File scripts/install.ps1 -Channel beta
```

On Linux or macOS, use the POSIX launcher (Python 3.11+ is required for installation):

```sh
sh scripts/install.sh
sh scripts/install.sh --channel alpha
```

The installer registers the Git marketplace, installs and enables the plugin, and configures native direct calls. Existing local installations migrate while retaining `terminalhook@terminalhook-local` and other user settings. The marketplace name retains its historical `-local` suffix for compatibility; the source is Git. A failed marketplace registration restores the previous source and tracking ref. Plugin installation requires Codex CLI, plus Git for Git installations. Windows uses PowerShell 7; Linux/macOS installation uses Python 3.11+. Running the installed MCP binary requires no Rust, Python, or Node.

Codex's own startup tasks refresh configured Git marketplaces and their installed plugin caches. To request a refresh explicitly:

```powershell
codex plugin marketplace upgrade terminalhook-local
```

Updates follow the selected branch, not GitHub's latest-release API. There is no custom background updater or guaranteed polling interval while the app stays open. Open a new Codex chat after an update. Existing chats retain their loaded tool catalog; durable background workers continue independently. See the [official marketplace commands](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace).

For offline installation or local development, download the ZIP matching your OS/architecture from [GitHub Releases](https://github.com/BANG404/Codex_Terminalhook/releases), extract it to a permanent directory, and run `pwsh -NoProfile -File ./install.ps1 -Source .` on Windows or `sh ./install.sh --source .` on Linux/macOS. Use an extractor that preserves executable permissions, such as `unzip` on Unix. Without a local source option the installer selects Git stable. CI preview ZIPs are also available in [Actions artifacts](https://github.com/BANG404/Codex_Terminalhook/actions).

To build and install a local source snapshot:

```powershell
pwsh -NoProfile -File scripts/package.ps1
pwsh -NoProfile -File scripts/install.ps1 -Source ./dist/package
```

On Linux/macOS, build with `python3 scripts/package.py --channel development`, then install with `sh scripts/install.sh --source ./dist/package`. Native source builds require the pinned Rust toolchain, Python 3.11+, and Node.js 20.19+ or 22.12+ with npm to bundle the Svelte dashboard. Linux binaries use glibc (built on Ubuntu 24.04); Alpine/musl and Windows ARM64 are not release targets. macOS artifacts are unsigned and not notarized.

`package.ps1` generates the ZIP and `dist/SHA256SUMS.txt`. Local installations require the directory to stay available and the installer to be rerun after local changes; they do not download remote updates. In both modes, the installer uses the standard `codex plugin marketplace add` and `codex plugin add` commands and Codex's configuration editing interface to preserve existing settings and add `mcp__terminalhook` to `features.code_mode.direct_only_tool_namespaces`. This administration does not send model requests.

Alternatively, configure a standalone MCP server using `examples/standalone.config.toml`. Choose either standalone MCP or the plugin to avoid duplicate tools. Enablement in a project's `.codex/config.toml` only applies to trusted projects and does not replace installation. The plugin retains the default tool approval policy. MCP commands use the local account's OS permissions and do not inherit the per-command sandbox of Codex's built-in shell.

To uninstall:

```powershell
codex plugin remove terminalhook@terminalhook-local
codex plugin marketplace remove terminalhook-local
```

## Task tools

| Tool | Behavior |
| --- | --- |
| `check_background` | Verify the existing shared host and originating chat without starting a model turn |
| `submit_task` | Launch authorized work once under a stable `task_key`; return a durable task ID immediately |
| `list_tasks` | Explicitly inspect the originating chat's tasks without opening a panel |
| `get_task` | Inspect a saved result, execution state and delivery receipt for diagnosis |
| `cancel_task` | Cancel an explicitly selected background task and clean up its process tree |
| `retry_delivery` | Retry a saved result whose delivery failed before submission; never rerun the command |
| `watch_tasks` | App-only subscription to the current task’s native state events |

Call tools directly in the `mcp__terminalhook` namespace. The installer adds this
namespace to `[features.code_mode].direct_only_tool_namespaces` while preserving
other settings. A manual plugin installation needs the same host configuration.
Open a new chat after upgrading so the removed tools disappear from its catalog.
The name TerminalHook refers to the plugin; current packages use native events
and host callbacks and do not register Stop or other lifecycle hooks. The historical
Stop-hook implementation remains on [legacy/stop-hook](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook).

First call `check_background`. A reachable shared Codex daemon, CLI proxy and
host-provided `threadId` metadata are required; TerminalHook never guesses a chat
ID or starts another daemon during preflight. A stdio-only host with no shared
endpoint cannot submit background work. Windows Desktop has an optional shared
host launcher described in the [background guide](docs/background-tasks.md).

Then submit the authorized command, for example:

```json
{
  "task_key": "build-project-2026-11-17",
  "program": "sh",
  "args": ["-c", "./build.sh"],
  "cwd": "/absolute/path/to/project"
}
```

On Windows, use a native executable or `pwsh -NoProfile -Command` with an absolute
native working directory. For PowerShell UTF-8 output, dot-source the packaged
`skills/terminal-wait/scripts/Initialize-Utf8.ps1` in that same PowerShell process.
Commands are argv arrays; explicitly invoke a shell for shell syntax.

`submit_task` returns `task_id`, `worker_pid`, and `state_dir`. End the turn and
await delivery to this chat. Do not repeatedly call `get_task` or `list_tasks` to
wait. Identical retries with the same key reuse the task; changed arguments are
rejected. A lost submission acknowledgement requires inspecting that task before
considering any new command. See [background tasks](docs/background-tasks.md).

## Output, deadlines and recovery

Omit `until` or use `[]` for natural exit. Optional named Rust regex rules stop
the command on a match; matching a line does not prove a successful natural exit.
Rules use 1–128 byte unique names, at most 16 rules, 4096-byte patterns and 32
capture groups. Empty matches, lookaround and backreferences are rejected.
Default matching is per line; `match_mode: "window"` uses a bounded 64 KiB window
for multiline matches. `streams` selects `stdout` and/or `stderr`; streams are
matched independently. Line matching rejects lines exceeding 65536 bytes.

Output is incrementally decoded as UTF-8, invalid bytes become U+FFFD, ANSI
CSI/OSC sequences are stripped for matching, and CRLF is normalized. Returned
output tails are bounded to 4096 bytes per stream; match/capture text shares an
8192-byte budget. Raw stdout/stderr is retained in per-task rotating logs, capped
at 2 MiB per file with one backup. Natural-exit tasks without regex conditions
accept long output without accumulating an unbounded matching buffer.

Omit `timeout_ms` or use `null` for no execution deadline. A finite deadline is
1–31536000000 ms and belongs to the independent worker, so work can exceed the
660-second MCP transport limit. A match, deadline or explicit cancellation cleans
up the process tree before saving the result. Natural root exit also cleans up
descendants before draining final output. Windows uses a kill-on-close Job with
a startup assignment gate; Unix uses a process group. Descendants that deliberately
create another Unix group/session can escape that containment.

Execution and delivery have separate states. A natural result has `status: "exited"`
and the actual `exit_code`; nonzero exits mark the task failed. There are no
connection-local process handles or output cursors. Delivery failure preserves the
saved result; use `retry_delivery` only after diagnosing a failure before submission.
An uncertain delivery requires reconciling the original chat instead of replaying it.
Workers survive MCP disconnect, but execution is not resumed after a reboot or
worker failure. The dashboard's native state subscriptions do not poll the model.
Closing the panel does not stop work or lose completion delivery.

## Verification

```sh
npm ci
npm run check:ui
npm run build:ui
cargo fmt --check
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --locked
python3 -m unittest discover -s tests -v
npx playwright install chromium
npm run test:ui
python3 scripts/package.py --channel development
python3 scripts/verify_package.py
python3 scripts/verify_long_wait.py
```

Protocol regressions prove that only background tools are discoverable and all
four removed names fail direct calls. They exercise detached completion, chat
isolation, idempotency, matching, UTF-8, logs, process cleanup and delivery recovery
using an independent loopback host fixture without model turns. Release additionally
requires a 65-second task across MCP disconnection, 90-second UTF-8 output and
Windows PowerShell UTF-8 output. Native desktop rendering and actual host delivery
remain separate integration checks; fixture evidence does not establish them.
Installation and Git marketplace checks use isolated Codex homes. Detailed gates,
coverage, mutation and performance checks are in [Development and releases](docs/releasing.md).

## Implementation references

- [Codex plugin format and marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Official Rust MCP SDK](https://github.com/modelcontextprotocol/rust-sdk)
