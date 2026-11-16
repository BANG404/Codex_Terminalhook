---
name: terminal-wait
description: Run commands and await final results, service readiness, or interactive responses locally through TerminalHook, avoiding repeated model-driven terminal checks.
---

Use `run_until` to launch a command with `program`, argument array, absolute `cwd`, and named `until` rules. Choose the condition that fulfills the user's task before starting. Await one result; output matching happens locally. Repeated short waits still create model round trips, even when each call waits locally.

Call `mcp__terminalhook` tools directly and await the result once. Do not wrap long waits in `functions.exec`: its yield behavior can introduce repeated model-driven waits. The installer adds `mcp__terminalhook` to `[features.code_mode].direct_only_tool_namespaces` in user config; a fresh chat loads the new tools. If this namespace is unavailable as a native direct call, report that the direct-call configuration has not loaded; do not silently switch to repeated wrapper waits. This version has no lifecycle hooks. For explicitly requested work that must outlive a tool call, use the independent background submission flow below. For completion by natural exit, use a specific marker the command will never emit and check the returned exit code.

## Choose the wait boundary

- **One-shot commands, builds, tests, and CI/CD:** make the first `run_until` wait for the final outcome or natural exit. Do not return at "started", "PR created", "queued", or "in_progress" just to resume waiting. When success requires the whole command to finish, prefer natural exit and inspect `exit_code`; a matched success-looking line alone is insufficient.
- **Services and interactive programs:** match readiness with `on_match: "keep_running"` when you need to use the service, supply input, or do other useful work next. After that work, use `wait_output` for a later condition on the same process. Retain `process_id` and `cursor`; call `stop_process` when finished. Do not replace continuation with another `run_until` that launches duplicate work.
- **Input and response:** use `write_input` with `wait` when you need the response. It registers the cursor before sending, then waits in the same call. Avoid a separate send followed by repeated output checks.

For example, a CI delivery command that prints a PR URL before merging should normally be awaited through natural exit in the initial call. A development server's ready URL is an appropriate early return when browser checks follow. Intermediate markers are useful only when the next action needs them or the user explicitly requested those milestones.

## Background tasks and dashboard

Use `check_background` before first background submission. The host must provide `threadId` request metadata and an existing shared Codex daemon. `submit_task` takes a stable per-chat `task_key`, program/args, absolute `cwd`, optional output conditions, and optional `timeout_ms`. Omit/null the deadline for no execution time limit; omit/empty `until` for natural exit. It returns immediately and owns execution outside the MCP connection. End the turn after submission and await the host-delivered result. Do not poll `get_task` or repeatedly submit tasks. Identical task keys return the existing task; changed arguments are rejected.

`list_tasks` opens the embedded MCP Apps dashboard in compatible hosts. The panel refreshes through tools without model messages. `get_task` inspects liveness, output tails, the persisted result, and delivery receipt. `cancel_task` explicitly cancels a task. `retry_delivery` retries only saved results that failed before submission and never reexecutes the command; `uncertain`/`sending` delivery requires inspection of the original chat before any manual recovery. Ordinary terminal tools keep their original connection-bound process lifetime. Background state survives MCP disconnection; command execution is not restored after OS/worker failure. Waiting uses the worker, and delivery uses `codex app-server proxy` to the existing daemon; never start a second app server or use `codex exec` to recover status.

## Timeouts and recovery

Choose a timeout for the expected task duration, at most `600000` ms and below the client's actual tool timeout (the packaged MCP limit is 660 seconds). For a several-minute build or CI wait, use an appropriate several-minute budget instead of repeated 30- or 60-second waits. Do not shorten waits solely to produce periodic commentary, and do not poll shell/status tools while a wait is pending.

Treat a timeout as a recovery boundary. Inspect the returned status, output tail, and logs as needed, and report what remains unresolved. Do not automatically call `wait_output` again for the same condition, restart the command, or resend input. Continue only with a concrete reason based on new diagnostic evidence or an explicit instruction to keep waiting. A task exceeding the per-call limit needs a bounded continuation plan with an overall deadline, rather than an open-ended timeout loop.

`run_until` terminates on match and timeout by default. Choose `on_timeout: "keep_running"` only when preservation is needed for diagnosis or a planned continuation; preservation alone is not a reason to keep checking. `wait_output` and response waits preserve the process on timeout/cancellation. Cancelling `run_until` cleans up its process.

## Invocation details

- There is no implicit shell. On Linux/macOS, invoke `sh -c` or `bash -lc` for shell syntax. On Windows, invoke `pwsh -NoProfile -Command` or `cmd.exe` explicitly for shell syntax and `.cmd` files. Supply an absolute native `cwd` (for example `/home/me/project`, `/Users/me/project`, or `D:/Projects/my-app`).
- TerminalHook sends and decodes UTF-8. Before PowerShell text I/O, dot-source [scripts/Initialize-Utf8.ps1](scripts/Initialize-Utf8.ps1) in the target session: `pwsh -NoProfile -Command ". '<absolute path to this skill>/scripts/Initialize-Utf8.ps1'; <command>"`. This sets `Console.InputEncoding`, `Console.OutputEncoding`, and `$OutputEncoding` to UTF-8 without a BOM, including Windows PowerShell 5.1 and native command pipelines. Resolve the script from this skill's installed location; use the same prefix before invoking a `.ps1` file. Running the initializer in a separate process does not configure the target session. For a standalone MCP installation without this skill, set those three values explicitly before the command.
- Choose a specific output marker. A match proves that condition occurred, not that the process exited successfully.
- For a justified later wait, call `wait_output` with the retained `process_id`, previous `cursor`, and the next condition. Use `cursor`, not `latest_cursor`, to avoid skipping data already received after the previous match. Do not calculate or reuse cursors across processes. `cursor_expired` means buffered history was lost: inspect logs/diagnostics before explicitly choosing a new start.
- For input, call `write_input` with `data` and optionally `wait: {"until":[...],"timeout_ms":...}`. The output cursor is registered before sending, preventing a race with immediate responses. Include newline explicitly. Pipe EOF can be sent with `eof: true`.
- Pipe mode preserves stdout/stderr separately. Use `mode: "pty"` for programs requiring a real terminal; output is then a merged `terminal` source, input can be echoed, and Enter is typically `\r`. PTY EOF is application-specific.
- Default line matching includes unfinished lines, removes ANSI CSI/OSC, and normalizes CR/LF. `$` means the end currently observed. Use a delimiter for complete variable-length captures. For newline delimiters or cross-line patterns, set `match_mode: "window"` with a suitable `window_bytes` (256..262144). Output outside that bounded window is not matched.
- Rules use Rust regex syntax, without lookaround, backreferences or zero-width matches. The tool limits output returned to the model and reports any match/capture truncation explicitly.
- Results include status, natural exit code when observed, output tails, and a rotating raw `log_dir`. Set an absolute `log_dir` in `run_until` if logs must survive the connection; default logs are temporary.

The ordinary terminal tools manage only commands launched by their MCP connection. It cannot attach to Codex built-in terminal sessions or resume processes after reconnection. Commands execute with local MCP OS permissions; normal tool approval remains applicable. Closing the connection cleans up its ordinary terminal processes; explicitly submitted background tasks remain independently owned by their workers.
