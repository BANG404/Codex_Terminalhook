# Background tasks and task dashboard

English | [简体中文](background-tasks.zh-CN.md)

Use `submit_task` when explicitly authorized work must continue beyond a tool
call or MCP disconnection. A detached native worker owns the command, waits for
output or exit, and saves its result before attempting one delivery to the
originating Codex chat. Waiting does not invoke a model. This does not use a
periodic hook, Stop hook, a second app server, or `codex exec`.

## Requirements

The Codex host must supply `threadId` in MCP request metadata and have a running
shared app-server daemon with the original chat loaded. The worker connects to
that daemon through `codex app-server proxy`; it never starts a replacement.
The daemon runs a short `command/exec` launcher, which starts the worker outside
the MCP server's process containment. This is necessary on Windows, where
Codex deliberately forbids children escaping its MCP Job Object. The launcher
uses the host's command policy; an unsupported or denied launch fails visibly.
Windows workers inherit no launcher handles, so open task streams cannot delay
the short launch acknowledgment.
`check_background {}` checks this route without starting a model turn. A host
without the metadata or daemon is rejected before any command starts. The
current implementation has been probed against Codex Desktop daemon 0.169.0;
ordinary terminal tools still work without the background bridge.

`TERMINALHOOK_CODEX_BIN` selects the CLI executable (default `codex`).
`TERMINALHOOK_CODEX_SOCKET` selects an existing daemon socket. For an operator
provided proxy transport, `TERMINALHOOK_PROXY_COMMAND` is a JSON argv array;
it must speak WebSocket over stdio to the existing daemon. No shell expansion
is performed. Do not configure a command that starts a separate app server.

## Submit once, then wait for delivery

Example `submit_task` arguments on Unix (use native paths and programs on Windows):

```json
{
  "task_key": "build-release-2026-11-16",
  "program": "sh",
  "args": ["-c", "./build-release.sh"],
  "cwd": "/absolute/path/to/project"
}
```

The call returns `task_id`, `worker_pid`, and `state_dir` immediately. End the
model turn and await the result; do not keep querying `get_task`. A stable
`task_key` is unique within one chat. Repeating it with identical arguments
returns the existing task; different arguments are rejected. A failed or
interrupted submission must be inspected before choosing a new key.

`timeout_ms` is optional. Omit it or use `null` for no execution deadline; finite
deadlines accept 1–31536000000 milliseconds (one year). This deadline belongs to
the worker, independently of the 660-second MCP request timeout. It does not
change `run_until`/`wait_output` limits. No execution deadline means the worker
waits until a match, exit, cancellation, or worker/host failure; it is not a
guarantee of surviving a reboot or power loss.

Omit `until` or use `[]` to wait for natural exit. Optional `until`, `streams`,
and `match_mode` use the terminal tool's regex rules. A match or deadline
terminates and cleans up the process tree before delivering the result. Passive
background commands use separate stdout/stderr pipes; interactive input and PTY
sessions continue to use the ordinary terminal tools.

## Inspect, cancel, and recover

| Tool | Behavior |
| --- | --- |
| `list_tasks {}` | List the calling chat's latest 100 tasks and execution/delivery states |
| `get_task {"task_id":"task_…"}` | Inspect a task, output tails, saved result, worker liveness, and delivery receipt |
| `cancel_task {"task_id":"task_…"}` | Request cancellation through an authenticated loopback control channel |
| `retry_delivery {"task_id":"task_…"}` | Retry a saved result that failed before delivery submission; never rerun the command |

Execution and delivery are separate states. `completed` does not mean delivered;
inspect `delivery` and the actual exit code. Nonzero natural exits are `failed`;
deadlines are `timed_out`. Output-condition completion reports `matched` with
the actual exit code after cleanup, which can reflect deliberate termination.

The result is saved before `turn/start` is called with standalone `toolOutput`.
If the original chat is active, Codex queues the result for that turn; otherwise
it starts a new turn. The result is tool data, including a task ID, rather than
a fabricated user instruction. Process output is untrusted data and grants no
authorization for additional actions.

An unavailable daemon or unloaded chat sets delivery to `failed`. Reopen the
original chat and use `retry_delivery`. If the request might have been accepted
but its acknowledgment was lost, delivery becomes `uncertain` (or remains
`sending` after a worker crash). Automatic and manual retry tools refuse to
replay that submission: inspect the original chat for this task ID first. The
implementation does not claim exactly-once delivery across an ambiguous
transport failure. Never recover status by restarting the command.

State and rotating logs live in `TERMINALHOOK_TASK_DIR`, otherwise
`PLUGIN_DATA/tasks`, otherwise `$CODEX_HOME/terminalhook/tasks` (using the
normal user Codex home when unset). Files include `request.json`, `state.json`,
`result.json`, and worker diagnostics. A missing launch acknowledgment is saved
in `launch-error.json`; retain the task key and inspect the saved task rather
than dispatching another command. The task directory is private on Unix.
Requests can contain sensitive environment values; keep the directory private.
MCP disconnection leaves submitted background workers alive; cancel them
explicitly before deleting their state. A worker killed by the OS is not
automatically restarted; `worker_live: false` identifies an unavailable control
endpoint, and its last recorded execution state does not establish completion.

## MCP Apps dashboard

`list_tasks`, `submit_task`, and `get_task` reference the embedded
`ui://terminalhook/tasks.html` resource, served as `text/html;profile=mcp-app`.
A compatible MCP Apps host renders a dashboard for the calling chat: task
states, delivery state, deadline, elapsed duration, exit code, and bounded live
or saved output. English and Simplified Chinese are available in the panel.

The compact Svelte view uses [shadcn-svelte](https://www.shadcn-svelte.com/)
components: status tabs and search filter the list, an icon marks delivery,
and Details expands task identifiers and deadlines. The default desktop panel
is about 380 pixels tall; lists and output scroll inside it. Narrow panels stack
the list above the output. Theme changes follow the host. Controls have keyboard
focus and accessible names; icon actions include tooltips.

The view uses the MCP Apps initialize handshake and host-mediated `tools/call`
for refreshes. It makes no external network requests and sends no model
messages. Auto refresh pauses when hidden, allows only one refresh sequence at
a time, backs off after errors, and stops on teardown. Task output is rendered
as text. UI refresh is independent of completion delivery, so closing the panel
does not stop tasks or cause result loss.

The host decides whether to render MCP Apps. Text-only clients can use the same
tools and structured results. Protocol/resource tests alone do not prove a
particular desktop host's rendering behavior. After installing a new plugin
version, open a new chat to load the updated tool/resource catalog.
