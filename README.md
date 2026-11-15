# TerminalHook

[![CI](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml/badge.svg)](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

本地 Rust MCP 服务和 Codex 插件。命令运行时，程序持续读取终端输出并匹配正则；调用一直等待到命中、退出、超时或取消。等待期间不返回 `pending`、不请求模型采样，也不要求模型反复查询。

## 安装与升级

下载 [GitHub Releases](https://github.com/BANG404/Codex_Terminalhook/releases) 中的 Windows x64 安装包；CI 的预览包位于 [Actions](https://github.com/BANG404/Codex_Terminalhook/actions) 运行的 artifacts 中。源码仓库不包含已编译二进制，不应将源码 checkout 当作可直接安装的二进制插件包。

开发采用 `alpha → beta → rc → main` 阶段分支。阶段包版本带提交序号和源码 SHA，普通推送仅运行 CI；发布由 Release 工作流手动触发。详见 [开发、CI/CD 和阶段发布](docs/releasing.md)。项目使用 [MIT 许可证](LICENSE)。

Windows x64 发布包：解压 `dist/terminalhook-windows-x64.zip` 到固定目录，执行根目录的 `install.ps1`。包内含 marketplace、插件、二进制、技能和安装说明；运行期只需要 Windows 与 Codex CLI，不需要 Rust、Python 或 Node。

从源码构建、安装或更新：

```powershell
pwsh -NoProfile -File scripts/package.ps1
pwsh -NoProfile -File scripts/install.ps1
```

`package.ps1` 生成 Zip 及 `dist/SHA256SUMS.txt`。安装脚本使用标准 `codex plugin marketplace add` 和 `codex plugin add` 注册本地目录、安装并启用插件。保持该目录可用，更新后重新执行安装脚本，再新开 Codex 会话加载新版本。现有会话中的进程由原 MCP 连接管理，不做热迁移。

也可以独立配置 MCP，参考 `examples/standalone.config.toml`。独立服务和插件二选一，避免重复工具。项目中 `.codex/config.toml` 的启用设置只对受信任项目生效，也不能替代安装。插件保留默认工具审批策略；MCP 命令使用本机账户权限，不继承 Codex 内置 shell 的逐命令沙箱。

卸载：

```powershell
codex plugin remove terminalhook@terminalhook-local
codex plugin marketplace remove terminalhook-local
```

## 工具与同步 Hook

### 桌面 App 同步 Stop Hook（0.3.0）

非交互长任务使用 `submit_task`，参数与 `run_until` 一致，省略 `session_id` / `turn_id`。插件的 `PreToolUse` MCP Hook 会自动绑定真实聊天；提交立即返回任务 ID，Agent 随即结束本轮生成。宿主在同步 `Stop` Hook 中调用 `await_tasks`，本地等待事件，完成后仅返回一次续跑结果。此路径不使用 Codex CLI，也不需要模型调用 `functions.wait` 或状态查询。

更新后在桌面 App 的 **Review hooks / Settings > Hooks** 中审阅并信任插件 Hook。新的测试回合必须加载新版插件。未注入聊天上下文时，`submit_task` 拒绝启动任务。`prepare_task` / `await_tasks` / `audit_event` 供宿主 Hook 使用，不应由模型手动调用。

`submit_task` 的 `on_match` / `on_timeout` 仅允许默认值 `terminate`，以便结果交付时任务已清理。需要等待自然退出时，可使用不会输出的标记并检查退出码。交互服务继续使用 `run_until`。Hook 默认等待上限 650 秒，任务上限 600 秒，MCP 上限 660 秒。中断 Hook 会取消该 Hook 正在等待的任务；MCP 断连会清理其进程。此模式保持回合在 Hook 阶段等待，不是跨应用重启的恢复机制。

插件的 `PreModelRequest` / `PostModelRequest` MCP Hook 记录模型请求边界，任务服务记录 `hook_wait_started` / `hook_wait_finished`。JSONL 文件按聊天分开，仅保存时间、聊天/回合 ID 和事件摘要，不记录提示词。文件位置由 `submit_task.audit_path` 返回：优先 `TERMINALHOOK_AUDIT_DIR`、其次 `PLUGIN_DATA/audit`，否则使用系统临时目录下的 `terminalhook-hook-audit`。每个聊天日志 2 MiB 轮转并保留一份旧日志。验收时必须确认等待前后的模型 Hook 均有记录，再统计等待区间内的请求；缺少模型记录不能视为零唤醒。

### 原有终端工具

| 工具 | 行为 |
| --- | --- |
| `run_until` | 启动命令并等待输出条件；默认命中/超时后终止，可保留进程 |
| `wait_output` | 用进程 ID 和游标继续等待；先重放已到达的数据，再等待新输出 |
| `write_input` | 发送输入；可同时注册等待条件，防止遗漏即时响应 |
| `stop_process` | 终止所管理的进程树，等待清理完成 |

### 启动并等待

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

`program` 不经过隐式 shell；管道、重定向、`.cmd` 需要显式调用 `pwsh`/`cmd.exe`。`cwd` 必须绝对。`env` 添加或覆盖继承的服务环境。默认保留 stdin，支持后续输入；需要 EOF 时用 `write_input` 的 `eof: true`。

默认 `mode: "pipe"` 分别读取 stdout/stderr，`streams` 可选择其一或全部。交互程序可选 `mode: "pty"`，使用 Windows ConPTY，`rows` 默认 24、`cols` 默认 120，范围均为 1–500。PTY 仅提供合并的 `terminal` 输出流，`streams` 应省略或设为 `["terminal"]`；输入可能被终端回显。

PowerShell 中的中文输入、输出和原生程序管道需要统一为 UTF-8。插件技能附带 `scripts/Initialize-Utf8.ps1`，在目标 PowerShell 会话中点导入后再执行命令或脚本（路径使用实际安装位置）：

```powershell
. 'D:/Projects/Codex_Terminalhook/plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1'
# 在同一会话执行目标命令，例如：
$line = [Console]::ReadLine()
Write-Output ('ACK:' + $line)
```

独立 MCP 部署也可在 `-Command` 命令开头直接设置 `[Console]::InputEncoding = [Console]::OutputEncoding = $OutputEncoding = [System.Text.UTF8Encoding]::new($false)`。仅设置输出编码无法修复输入乱码；单独启动子 PowerShell 运行初始化脚本也不会改变父会话。TerminalHook 仍按原样执行 `program`/`args`，编码初始化由调用方在目标 PowerShell 中完成。

结果示例（字段省略）：

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

匹配只证明输出条件出现，不证明命令成功退出。`elapsed_ms` 是进程会话时长，`wait_elapsed_ms` 是匹配时本次等待耗时；`bytes_seen` 是原始输出字节数。Windows 的 `os_pid` 是受 Job 管理的启动器 PID，不是目标应用的 PID。

### 从游标继续等待

```json
{
  "process_id": "proc_...",
  "cursor": 512,
  "until": [{"name": "done", "pattern": "Build completed"}],
  "timeout_ms": 300000
}
```

传给 `wait_output`。使用上次返回的 `cursor`，不要使用 `latest_cursor`，后者可能越过尚未消费的输出。游标表示按接收顺序排列的**归一化输出字节位置**，可以位于一次读取内部，所以同一数据块中的后续标记不会被跳过。请把游标当作不透明值，不自行计算或跨进程复用。

每个进程保留最近 1 MiB 的归一化历史。游标落在已淘汰历史中时返回 `cursor_expired`，附 `oldest_cursor`、`latest_cursor`、日志路径和输出尾部，不会悄悄跳过数据。历史未过期时，即使进程已退出，也可重放未消费的输出。已退出会话最多保留到连接中最近 64 个会话的范围，旧 ID 会过期。

### 发送输入并等待

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

传给 `write_input`。服务先捕获游标再发送输入，接着持续等待，不需要模型先写入再轮询。省略 `wait` 时返回 `written`。输入最多 65536 个 UTF-8 字节，换行需显式包含；PTY 通常使用 `\r`。管道可设置 `eof: true`，写入后关闭 stdin；PTY 的 EOF 应发送目标应用识别的控制字符。

输入写入最多等待 5 秒，防止未读取 stdin 的应用堵死连接；超时会终止进程，避免未完成的写入稍后意外生效。等待响应阶段超时或取消则保留进程。`wait_output` 的超时或取消同样不会停止目标。

### 停止

向 `stop_process` 传入 `{"process_id":"proc_..."}`。活跃进程返回 `stopped`；已停止或未知 ID 返回 `not_running`，可重复调用。

## 匹配与终止语义

`until` 支持 1–16 条命名规则，名字唯一且最多 128 字节。对当前可匹配文本按规则数组顺序选择命中。使用 Rust `regex` 语法，不支持前后查找和反向引用；拒绝可能零宽匹配的规则。正则最多 4096 字节、32 个捕获组，编译体积最多 2 MiB。

| 模式 | 匹配范围 |
| --- | --- |
| `line`（默认） | 完整行及当前未结束行，单行最多 64 KiB；不跨行 |
| `window` | 每个输出流各自保留 `window_bytes`（256–262144）字节，支持跨行；超出窗口的内容不会参与匹配 |

按 UTF-8 增量解码，无效字节替换为 U+FFFD；其他编码需由目标程序转换。剥离 ESC 开头的 ANSI CSI/OSC 控制序列，CR/LF 作为行边界、CRLF 归一为一个换行，不模拟光标移动后的屏幕布局。PTY 启动所需的光标位置查询在本地应答，不涉及模型。

部分行会立即参与匹配，因此 `$` 仅代表当前观察到的行尾。可变长度捕获组应包含明确分隔符；若要捕获完整 URL 等以换行结束的内容，使用 `window` 模式并将分隔符写进正则。匹配文本返回最多 4096 字节，匹配和所有捕获组合计最多 8192 字节，截断时 `match_output_truncated` 为 true；匹配判定与游标仍依据完整文本。

| 状态 | 含义 |
| --- | --- |
| `matched` | 正则命中；`run_until` 按 `on_match` 决定保留/终止 |
| `exited_unmatched` | 自然退出且输出已排空，未命中，附自然退出码 |
| `timeout` | 达到等待时限；`run_until` 按 `on_timeout` 决定保留/终止 |
| `cursor_expired` | 请求的历史已淘汰，需要诊断或显式选择新的起点 |
| `error` | 参数、启动、读取、日志或匹配错误 |

`on_match`、`on_timeout` 均默认为 `terminate`，可选 `keep_running`。`timeout_ms` 默认 300000，范围 1–600000；打包 MCP 工具超时设为 660 秒，为清理与传输保留余量。目标程序必须 flush 输出，未写入管道/终端的内容无法被观察。

取消 `run_until` 会终止其进程；取消后续等待只移除该等待。连接关闭会清理全部活跃进程。根命令自然退出后，仍持有管道的后代会被清理，再排空最后输出。Windows 使用带 `KILL_ON_JOB_CLOSE` 的 Job Object，目标先经过本地启动器闸门，加入 Job 后才启动，消除了目标启动到分配 Job 的竞态。正常清理会等待 Job 完成端口报告 `ACTIVE_PROCESS_ZERO`，确认后代退出后才返回；10 秒内无法确认则报告错误。硬杀 MCP 服务时，操作系统关闭 Job 也会清理后代。

## 日志与资源边界

每个进程持续保存原始输出到 `stdout.log`/`stderr.log` 或 `terminal.log`。每个文件最多 2 MiB，轮转保留一份 `.log.1`，`logs_rotated` 表明更早内容可能已被淘汰。日志不记录 stdin；终端回显仍可能出现在输出中。

默认日志在连接专属临时目录，正常断连时删除，淘汰的旧会话临时日志也会删除。需要会话外保留时，在 `run_until` 设置绝对 `log_dir`，服务在其中创建独立进程目录；持久日志不会自动删除。异常硬杀可能留下临时日志，可按操作系统临时文件管理方式清理。

每连接最多 8 个活跃进程、64 个会话。输出使用有界队列，历史最多 1 MiB/进程，每流仅返回 4096 字节输出尾部。匹配后保留的命令仍持续排空输出。源码含 Unix PTY/进程组实现，当前只验证 Windows x64，不把未经运行的 Unix 分支作为发布保证。

插件仅观察其启动的命令，不能接管 Codex 内置终端。原生终端会话 ID 不是插件可订阅的输出句柄。进程、游标和临时日志仅在当前 MCP 连接有效；不承诺跨连接持久化，也不会自动拦截所有内置 shell 调用。技能会指导 Codex 在等待输出条件时使用这些工具。

## 验证

```powershell
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --locked
python -m unittest discover -s tests -v

# 超过默认 60 秒客户端超时的协议回归测试
$env:TERMINALHOOK_LONG_WAIT = '1'
python -m unittest discover -s tests -v

# 单独运行 90 秒 PowerShell 中文长任务，摘要和原始日志保存到 artifacts/
python -m unittest discover -s tests -k powershell_utf8_long_wait -v

# 从发布 Zip 解压、执行安装脚本，在隔离 Codex 目录测试缓存中的二进制
python scripts/verify_plugin.py

# 使用当前登录进行真实 Codex 65 秒验证
python scripts/verify_codex.py
python scripts/verify_codex.py --plugin

# 实际插件的启动、后续等待、交互输入及停止工作流
python scripts/verify_codex.py --plugin --workflow

# 完成发布验证后核对源码、Zip、安装缓存及验收记录
python scripts/audit_release.py
```

协议测试仅使用 Python 3 标准库；安装验证使用 Python 3.11+ 的 `tomllib`。真实 Codex 验证不修改全局配置，保存 CLI 事件、MCP 往返及摘要到 `artifacts/`。单次等待验证一次调用、一个结果、等待期间无中间结果及模型 shell 查询；工作流验证四个工具和进程清理。证据只证明这些实际运行没有工具轮询，不声称控制客户端所有后台行为。

## 实现依据

- [Codex 插件格式与 marketplace](https://developers.openai.com/plugins/build/plugins)
- [Codex MCP 配置与超时](https://learn.chatgpt.com/docs/extend/mcp)
- [官方 Rust MCP SDK](https://github.com/modelcontextprotocol/rust-sdk)
- [portable-pty / ConPTY](https://docs.rs/portable-pty/latest/portable_pty/)
