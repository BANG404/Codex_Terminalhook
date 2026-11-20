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

Use the host terminal for foreground or interactive work. Background commands
close stdin and capture stdout and stderr separately. A natural result has
`status: "exited"` and the actual exit code.

The installer adds `mcp__terminalhook` to
`[features.code_mode].direct_only_tool_namespaces`, preserving other settings.
The installer sets `approval_mode = "approve"` for `submit_task` when the user
has set no approval mode for the tool or the TerminalHook server.
Manual installations need the same host configuration. Open a new chat after an
upgrade to load the current tool catalog.

Example request to Codex:

> Run the build and tell me when it finishes.

Submit long or unpredictably timed commands directly with `submit_task`. Use a
stable `task_key`, program/args, and absolute `cwd`. The tool validates the
shared Codex daemon and original chat before launch and returns any validation
error in the same call. Omit `timeout_ms` for no execution deadline and `until`
for natural exit. After receiving the task ID, end the turn and await delivery
to the originating chat. In compatible hosts, submission opens a task panel.
Use `list_tasks` for chat-wide inspection and `get_task`, `cancel_task`, or
`retry_delivery` for a specific task. See the
[background task guide](https://github.com/BANG404/Codex_Terminalhook/blob/alpha/docs/background-tasks.md).

If Desktop uses stdio and submission reports that the shared endpoint is
unavailable, quit Desktop and run the shared-host launcher from this extracted
package. Use `pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1` on Windows
or `sh ./Start-DesktopSharedHost.sh` on macOS. Reopen the original chat in
that session and submit the task there. Normal Desktop startup restores the
default transport.

To uninstall, run `codex plugin remove terminalhook@terminalhook-local`, then optionally `codex plugin marketplace remove terminalhook-local`.
