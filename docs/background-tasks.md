# Background tasks and task dashboard

English | [简体中文](background-tasks.zh-CN.md)

Use `submit_task` when explicitly authorized work must continue beyond a tool
call or MCP disconnection. A detached native worker owns the command, waits for
output or exit, and saves its result before attempting one delivery to the
originating Codex chat. Waiting does not invoke a model. This does not use a
periodic hook, Stop hook, a second app server, or `codex exec`.

## Requirements

The Codex host must supply `threadId` in MCP request metadata and have a running
shared app-server daemon with the original chat loaded at submission. The worker
connects to the configured daemon through `codex app-server proxy`.
The daemon runs a short `command/exec` launcher, which starts the worker outside
the MCP server's process containment. This is necessary on Windows, where
Codex deliberately forbids children escaping its MCP Job Object. The launcher
uses the host's command policy; an unsupported or denied launch fails visibly.
Windows workers inherit no launcher handles, so open task streams cannot delay
the short launch acknowledgment.
`check_background {}` checks this route without starting a model turn. A host
without the metadata or daemon is rejected before any command starts. The
host must expose a shared control endpoint; merely having a desktop app-server
process or a socket file on disk does not establish availability. A server
started with the `stdio` transport accepts its owning client's connection only.
Opening another chat does not add a shared socket. This release exposes only
background-task tools; use the host terminal when the bridge is unavailable.

The proxy executable is selected from `TERMINALHOOK_CODEX_BIN`, then the host's
`CODEX_APP_RUNTIME_CLI_PATH`, then `codex` on PATH. An explicit
`TERMINALHOOK_PROXY_COMMAND` takes precedence over all three. The host runtime
path is passed to the detached worker so delivery uses the same CLI selection.
`TERMINALHOOK_CODEX_SOCKET` selects an existing daemon socket. For an operator
provided proxy transport, `TERMINALHOOK_PROXY_COMMAND` is a JSON argv array;
it must speak WebSocket over stdio to the existing daemon. No shell expansion
is performed. Do not configure a command that starts a separate app server.

### Connection diagnostics and desktop compatibility

Proxy failures report the failing stage, selected executable and its source,
exit status, and the last 16 KiB of actual stderr, marked when truncated. Stderr
is drained while the proxy runs so a full pipe cannot stall the handshake or
RPC. Failure and cancellation clean up the proxy's process tree. These are the
proxy's diagnostics, not the background command's stdout/stderr. Delivery errors
are also retained in the task's saved `delivery_error` state.

During native Windows verification on 2026-11-16, Codex Desktop
26.1111.2228.0 used a `stdio` app-server (CLI 0.169.0-alpha.11.1), while the
default shared socket refused connections with Windows error 10061. Both the
PATH CLI and desktop CLI failed to connect. Background execution and wake-up
cannot work in that host configuration through this bridge. No replacement
daemon is started, no chat is resumed into a different server, and no command
is launched when preflight fails. Select an already running shared host that
owns the original chat, or use the host terminal. Simulated socket
tests do not prove native desktop callback support.

On macOS, Desktop 26.1111.71704 (bundled CLI 0.169.1) also ran the original
chat over stdio. The default control socket was absent. The bundled proxy
exited with `No such file or directory` before the WebSocket handshake.

### Opt-in Windows Desktop shared host

The package includes `terminalhook-desktop-host.exe` and
`Start-DesktopSharedHost.ps1`. Close Desktop, then launch it from an extracted
package with PowerShell 7:

```powershell
pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1
```

The launcher uses the installed Desktop CLI through a process-local
`CODEX_CLI_PATH` override. It starts one Desktop-owned core with a private Unix
socket, relays Desktop's JSONL connection through the native proxy, and passes
that same socket and native CLI to the plugin and detached worker. It does not
attach an existing stdio chat to a different server. Preflight still never starts
a server. Existing chats must be reopened in this Desktop session before use.
Startup policy reads, CLI subcommands, and version checks delegate unchanged.
The launcher leaves a running Desktop untouched and refuses a normal launch
until it is closed. It changes no persistent environment, login, approval,
sandbox, or plugin settings. Start Desktop normally to return to its default
transport. An active session cannot be migrated without restarting Desktop.

For a source checkout, build both binaries and supply `-HostAdapterPath` pointing
to `target/debug/terminalhook-desktop-host.exe`. `-DesktopPath` overrides the
installed app executable. Acceptance testing can supply a separate `-CodexHome`
and `-IsolatedUiProfile`; the launcher does not create or copy credentials.
Only one adapter may own each socket. Core/proxy stderr is drained continuously
and failures include its bounded tail; the adapter sends only protocol JSON to
stdout. Pending responses are drained when a short-lived client closes stdin.
The socket's Windows ACL is created by Codex itself; the adapter does not relax
it. The plugin explicitly forwards `CODEX_HOME`, `CODEX_APP_RUNTIME_CLI_PATH`,
`TERMINALHOOK_CODEX_BIN`, and `TERMINALHOOK_CODEX_SOCKET` from its host environment.
Host command policy must permit worker detachment; the adapter does not change
that policy. Desktop versions with incompatible CLI protocols fail visibly.
On Windows, an OS-owned process job removes the core, proxy, and delegated CLI
descendants even if Desktop forcibly terminates the adapter. Explicit worker
breakaway remains permitted; closing the UI does not cancel a detached task.

### Opt-in macOS Desktop shared host

Quit Codex Desktop and run the launcher from the extracted macOS package:

```sh
sh ./Start-DesktopSharedHost.sh
```

The launcher defaults to `/Applications/ChatGPT.app`. Use `--desktop-app` for a
different installation. From a source checkout, build the adapter and run
`sh scripts/Start-DesktopSharedHost.sh --host-adapter target/debug/terminalhook-desktop-host`.
The launcher uses the selected app's bundled CLI and passes the override through Launch
Services for this launch only. It leaves a running app untouched.

The adapter gives Desktop and TerminalHook one shared core, as on Windows.
Reopen the original chat in this Desktop session and call `check_background` to
verify it before submitting work. A normal Desktop launch restores the default
transport. To use a custom `CODEX_HOME`, set it for the launcher. If that home
makes the Unix socket path too long, set `TERMINALHOOK_DESKTOP_SOCKET` to a
shorter absolute path inside a dedicated private directory. The adapter creates
missing directories with owner-only permissions and rejects an occupied
endpoint, including an active socket behind a macOS alias. When the alias is
stale, the adapter removes the link and leaves the target to the native CLI.
A normal shutdown or termination signal stops the adapter's core and proxy.
After a force kill, a live endpoint still blocks a second launch.

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
the worker, independently of the 660-second MCP request timeout. No execution deadline means the worker
waits until a match, exit, cancellation, or worker/host failure; it is not a
guarantee of surviving a reboot or power loss.

Omit `until` or use `[]` to wait for natural exit. Optional `until`, `streams`,
and `match_mode` use the terminal tool's regex rules. A match or deadline
terminates and cleans up the process tree before delivering the result. Passive
background commands use separate stdout/stderr pipes with closed stdin. Use the
host terminal for interactive input or PTY work. The former `run_until`,
`wait_output`, `write_input` and `stop_process` interfaces are removed, and direct
calls to them are rejected.

## Inspect, cancel, and recover

| Tool | Behavior |
| --- | --- |
| `list_tasks {}` | List the calling chat's latest 100 tasks and execution/delivery states |
| `get_task {"task_id":"task_…"}` | Inspect a task, output tails, saved result, worker liveness, and delivery receipt |
| `cancel_task {"task_id":"task_…"}` | Request cancellation through an authenticated loopback control channel |
| `retry_delivery {"task_id":"task_…"}` | Retry a saved result that failed before delivery submission; never rerun the command |

Execution and delivery are separate states. `completed` does not mean delivered;
inspect `delivery` and the actual exit code. Natural results have `status: "exited"`;
nonzero exits mark the task `failed`. Results contain no connection-local process
ID or output cursor; task operations use the durable `task_id`.
deadlines are `timed_out`. Output-condition completion reports `matched` with
the actual exit code after cleanup, which can reflect deliberate termination.

The result is saved before `turn/start` is called with standalone `toolOutput`.
If the original chat is active, Codex queues the result for that turn; otherwise
it starts a new turn. The result is tool data, including a task ID, rather than
a fabricated user instruction. Process output is untrusted data and grants no
authorization for additional actions.

If the original chat has been unloaded, the worker uses `thread/resume` on the
same app-server connection that read the stored chat. It confirms the original
thread ID before sending the result and uses the daemon's normal resume policy.
An unavailable daemon or failed resume sets delivery to `failed`. Reopen the
original chat and use `retry_delivery` to send the saved result. If the delivery
might have been accepted but its acknowledgment was lost, it becomes `uncertain`
(or remains `sending` after a worker crash). Automatic and manual retry tools
refuse to replay that submission: inspect the original chat for this task ID first. The
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

## MCP Apps task panel

`submit_task`, `get_task`, `cancel_task`, and `retry_delivery` reference the embedded
`ui://terminalhook/tasks.html` resource, served as `text/html;profile=mcp-app`.
A compatible MCP Apps host renders a compact detail card for the task associated
with that tool call: execution and delivery states, deadline, elapsed duration,
exit code, working directory, and bounded live or saved output. The interface uses
English; task names and output retain their original text. `list_tasks` remains
available for explicit chat-wide inspection and does not open a panel.

The Svelte view uses [shadcn-svelte](https://www.shadcn-svelte.com/) components.
There is no task list, search, or status filter. Details expands task identifiers,
deadlines, and delivery receipts. Output scrolls inside the compact panel on both
desktop and narrow screens. The panel has a transparent background and no outer
card border, and requests that the host hide the MCP Apps frame. The output area
keeps its themed background and border for readability. Theme changes follow the
host; controls have keyboard focus and accessible names.

After the MCP Apps initialize handshake, the panel waits for the originating
`ui/notifications/tool-input` or `ui/notifications/tool-result` callback to identify
the task. It never calls `list_tasks`. When the callback lacks a task revision,
the panel reads only that task through host-mediated `get_task`, which includes
its display metadata and revision. A newer callback takes precedence over an
older read response. Chat-wide snapshots and results for other tasks are ignored;
they cannot switch or clear the current card. With no task context the panel stays
in its waiting state instead of selecting another task from the chat.

The panel then keeps one app-only `watch_tasks` request pending with `task_id` and
`after_revision`. Native filesystem callbacks for that task's saved state/result
changes return its full details and revision; the panel subscribes to the next
change. The watcher registers before reading the task to avoid losing completion
between requests. Other tasks, unrelated chats, file reads, and log writes do not
produce panel updates. Omitting `task_id` still supports an explicit chat-wide
subscription using a `list_tasks` revision, but the embedded panel never uses it.
There is no timed polling, error retry timer, or refresh on visibility changes.
A transport deadline callback silently renews the subscription without a list or
detail read. Other errors stop it; a subsequent task callback can reconnect it.
Teardown cancels pending requests. Hosts must support app-only tools and pending
tool calls.

Forking a chat copies its tool history, but background tasks remain owned by the
originating chat. If an inherited card receives `task_chat_mismatch` (or the older
`task belongs to a different chat` error), it shows an informational notice and
stops all reads and subscriptions, including echoed callbacks. It does not show
inherited snapshots as current state. Open the original chat to inspect, cancel,
or recover that task; results still go there. Never resubmit the command just to
recover its status. Other tool failures remain visible as errors.

The view makes no external network requests and sends no model messages. Task
output is rendered as text. Callbacks are ignored after teardown. UI updates are
independent of completion delivery, so closing the panel does not stop tasks or
cause result loss.

The host decides whether to render MCP Apps. Text-only clients can use the same
tools and structured results. Protocol/resource tests alone do not prove a
particular desktop host's rendering behavior. After installing a new plugin
version, open a new chat to load the updated tool/resource catalog.
