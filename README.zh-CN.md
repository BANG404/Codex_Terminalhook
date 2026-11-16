# TerminalHook

[English](README.md) | 简体中文

[![CI](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml/badge.svg)](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

TerminalHook 是 Rust MCP 服务和 Codex 插件，支持事件驱动的终端等待与交互式进程控制。命令运行时，程序持续读取终端输出并匹配正则；调用一直等待到命中、退出、超时或取消。直接等待期间不返回 `pending`、不请求模型采样，也不要求模型反复查询。明确提交的后台任务立即返回、独立运行，并向原聊天交付已保存结果。

原生测试和分发覆盖 Windows x64、Linux x64/ARM64，以及 macOS Intel/Apple Silicon。每个目标均运行 Rust 和 MCP 协议测试，并验证解压包、安装及 Git marketplace 更新。Windows 使用 Job Object 和 ConPTY；Linux/macOS 使用进程组和 Unix PTY。

## 主要能力

- **等待终端事件：** 使用 `run_until` 等待开发服务器的就绪 URL、构建结果或其他正则输出条件。
- **接续读取输出：** 使用 `wait_output`、进程 ID 和游标，读取上次返回后收到的输出。
- **控制交互命令：** 支持管道和 PTY 模式，使用 `write_input` 发送输入，并可在同一次调用中等待响应。
- **管理进程生命周期：** 按需保留命令，或使用 `stop_process` 停止其进程树；原始输出保存到轮转日志。
- **执行长时间后台任务：** 使用 `submit_task` 跨越 MCP 调用或断连等待，完成后向原聊天交付结果；英文 MCP Apps 面板展示执行与交付状态，并在收到宿主回调时更新。参见[后台任务与任务面板](docs/background-tasks.zh-CN.md)。
- **集成 Codex：** 安装包含 `terminal-wait` 技能的插件，或配置独立 MCP。当前版本使用直接工具调用，无需生命周期 Hook。

[安装与升级](#安装与升级) · [工具参考](#原有终端工具) · [发布版本](https://github.com/BANG404/Codex_Terminalhook/releases) · [开发与发布](docs/releasing.zh-CN.md)

## 安装与升级

TerminalHook 通过 Git marketplace 分发，包含原生二进制、MCP 配置和运行期技能。安装器自动选择当前系统和架构。Windows 保留 `marketplace` 及 `marketplace-alpha/beta/rc`；Unix 分支在渠道分支后追加 `-linux-x64`、`-linux-arm64`、`-macos-x64` 或 `-macos-arm64`。各平台/渠道在首次执行发布版 Release 工作流后可用。源码分支不包含编译后二进制，不能直接作为二进制 marketplace 安装。

开发采用 `alpha → beta → rc → main` 阶段分支。变更通过 PR：`alpha` 完整验证，后续提升核对源码树并复用原验证产物，避免 push 与 PR 重复执行。阶段包版本带提交序号和源码 SHA，发布由 Release 工作流手动触发。详见 [开发、CI/CD 和阶段发布](docs/releasing.zh-CN.md)。项目使用 [MIT 许可证](LICENSE)。

开发任务使用 `scripts/deliver.py` 及仓库的[任务完成规则](AGENTS.md)，自动提交 alpha PR，并在当前来源的实际 CI 成功后合并。这不会提升后续阶段或发布版本。参见[自动交付到 alpha](docs/releasing.zh-CN.md#任务完成与自动交付到-alpha)。

从源码 checkout 安装 Git 分发版，无需构建 Rust：

```powershell
pwsh -NoProfile -File scripts/install.ps1
# 可选：改为跟踪预发布渠道。
pwsh -NoProfile -File scripts/install.ps1 -Channel beta
```

Linux 或 macOS 使用 POSIX 启动脚本（安装时需要 Python 3.11+）：

```sh
sh scripts/install.sh
sh scripts/install.sh --channel alpha
```

安装器注册 Git marketplace、安装并启用插件、配置原生直接调用。已有本地安装会迁移，保留 `terminalhook@terminalhook-local` 和其他用户设置。marketplace 名称保留历史 `-local` 后缀以兼容旧配置，实际来源为 Git。marketplace 注册失败会恢复原来源和跟踪分支。插件安装需要 Codex CLI，Git 安装还需要 Git。Windows 使用 PowerShell 7；Linux/macOS 安装使用 Python 3.11+。安装后的 MCP 二进制运行不需要 Rust、Python 或 Node。

Codex 自身的启动任务刷新已配置的 Git marketplace 及已安装插件缓存。也可显式请求刷新：

```powershell
codex plugin marketplace upgrade terminalhook-local
```

更新跟随所选分支，不查询 GitHub 的 latest-release API。项目没有自建后台更新器，也不承诺 App 长期开启时的定时检查间隔。更新后新开 Codex 聊天；原会话进程仍由原 MCP 连接管理，不做热迁移。参见[官方 marketplace 命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace)。

离线安装或本地开发：从 [GitHub Releases](https://github.com/BANG404/Codex_Terminalhook/releases) 下载匹配系统/架构的 ZIP，解压到固定目录；Windows 执行 `pwsh -NoProfile -File ./install.ps1 -Source .`，Linux/macOS 执行 `sh ./install.sh --source .`。Unix 应使用 `unzip` 等保留可执行权限的解压工具。不指定本地来源时安装器选择 Git 稳定渠道。CI 预览 ZIP 也可从 [Actions artifacts](https://github.com/BANG404/Codex_Terminalhook/actions) 获取。

从源码构建并安装本地快照：

```powershell
pwsh -NoProfile -File scripts/package.ps1
pwsh -NoProfile -File scripts/install.ps1 -Source ./dist/package
```

Linux/macOS 使用 `python3 scripts/package.py --channel development` 构建，再执行 `sh scripts/install.sh --source ./dist/package` 安装。原生源码构建需要固定版本的 Rust 工具链、Python 3.11+，以及带 npm 的 Node.js 20.19+ 或 22.12+，用于打包 Svelte 面板。Linux 二进制使用 glibc（基于 Ubuntu 24.04 构建）；Alpine/musl 和 Windows ARM64 暂不作为发布目标。macOS 产物未签名，也未公证。

`package.ps1` 生成 ZIP 及 `dist/SHA256SUMS.txt`。本地安装需要保留目录，文件变化后重新执行安装器，不下载远端更新。两种模式均使用标准 `codex plugin marketplace add`、`codex plugin add` 命令及 Codex 配置编辑接口，保留已有设置，并将 `mcp__terminalhook` 加入 `features.code_mode.direct_only_tool_namespaces`。此管理过程不发起模型请求。

也可以独立配置 MCP，参考 `examples/standalone.config.toml`。独立服务和插件二选一，避免重复工具。项目中 `.codex/config.toml` 的启用设置只对受信任项目生效，也不能替代安装。插件保留默认工具审批策略；MCP 命令使用本机账户权限，不继承 Codex 内置 shell 的逐命令沙箱。

卸载：

```powershell
codex plugin remove terminalhook@terminalhook-local
codex plugin marketplace remove terminalhook-local
```

## 直接调用与终端工具

### 桌面 App 直接调用（已实测）

0.4.0 起默认使用原生直接调用，不包含生命周期 Hook，也无需审阅或信任 Hook。安装器自动写入以下用户配置；手动安装插件或独立配置 MCP 时，应自行将该命名空间加入已有数组：

```toml
[features.code_mode]
direct_only_tool_namespaces = ["mcp__terminalhook"]
```

新开聊天加载配置后，直接调用 `mcp__terminalhook.run_until` 并等待一次返回，不经 `functions.exec` 包装。直接调用属于宿主配置，插件 manifest 本身不能强制工具暴露方式；直接安装插件但跳过安装脚本时，必须检查该设置。项目配置可以覆盖用户设置；不支持该配置的旧宿主需先更新。普通命令运行及等待不使用 Codex CLI；后台结果交付通过 `codex app-server proxy` 连接现有 daemon，不使用 `codex exec`，不轮询模型。

直接调用切换前的桌面实测：0.3.1 保留模型审计并禁用 Stop 后，工具调用耗时 120.6039 秒；完整命令执行区间 120.169 秒内，目标聊天模型请求数为 0。一次原生调用、一次结果，退出码 0，中文 stdout/stderr 各 12 条完整，模型审计在执行前后均有记录。此证据证明该调用路径，无需 Stop 等待；不等同于所有宿主或所有版本的后台请求保证。0.4.0 默认包不再提供模型审计 Hook。

本地保留原始模型事件和调用轨迹时，可用 `scripts/audit_direct_wait.py <rollout.jsonl> <audit.jsonl>` 复核原始记录；默认使用 `artifacts/desktop-direct-call-markers.json` 中的命令起止时间。测试记录不纳入源码仓库。

### 历史 Hook 版本

0.3.1 的同步 Stop Hook、后台提交和模型审计实现单独保存在 [legacy/stop-hook 分支](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook)。当前版本不包含生命周期 Hook 定义或 `prepare_task`、`await_tasks`、`audit_event`；新的 `submit_task` 是独立持久 worker API，与历史 Stop Hook 实现不同；升级后旧聊天仍需结束其 MCP 连接，再新开聊天加载新版。

### 原有终端工具

| 工具 | 行为 |
| --- | --- |
| `run_until` | 启动命令并等待输出条件；默认命中/超时后终止，可保留进程 |
| `wait_output` | 用进程 ID 和游标继续等待；先重放已到达的数据，再等待新输出 |
| `write_input` | 发送输入；可同时注册等待条件，防止遗漏即时响应 |
| `stop_process` | 终止所管理的进程树，等待清理完成 |

明确要求跨越单次调用运行时，可使用 `check_background`、`submit_task`、`list_tasks`、`get_task`、`cancel_task`、`retry_delivery`。期限、持久化、安全恢复与内嵌面板见[后台任务指南](docs/background-tasks.zh-CN.md)。

### 避免模型驱动的轮询

一次性命令、构建、测试及 CI/CD，应让首次 `run_until` 直接等待最终结果或自然退出。
不要仅为了继续等待，就在启动、PR 创建、`queued` 或 `in_progress` 消息处返回。
当前 API 等待自然退出时，可指定一条命令不会输出的特定正则；返回
`exited_unmatched` 且退出码为 0，才表明命令成功执行完毕。仅匹配到输出行不能证明命令成功。

按预计任务时长设置超时，最多 600000 毫秒，且低于客户端工具超时（包内为 660 秒）。
本地等待只避免单次调用内部的模型轮询；反复进行 30 或 60 秒等待，仍会产生模型往返。
不要仅为定期汇报进度而拆分等待。超时后应先诊断或报告未解决的状态，再决定恢复方式，
不要自动重复等待。超过单次上限的任务需要有总截止时间的有限续等计划。

只有在执行了必要的中间操作或其他有用工作后，需要等待保留进程的后续条件时，
才使用 `wait_output`，例如检查正在运行的服务。发送输入且需要响应时，
用一次带 `wait` 的 `write_input` 完成。这些是面向模型的使用指引；
服务端不会强制禁止重复等待，也不保证模型请求次数。

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

默认 `mode: "pipe"` 分别读取 stdout/stderr，`streams` 可选择其一或全部。交互程序可选 `mode: "pty"`，使用 Windows ConPTY 或 Unix PTY，`rows` 默认 24、`cols` 默认 120，范围均为 1–500。PTY 仅提供合并的 `terminal` 输出流，`streams` 应省略或设为 `["terminal"]`；输入可能被终端回显。Linux/macOS 使用 `/home/me/project` 或 `/Users/me/project` 等本机绝对工作目录；需要 shell 语法时显式调用 `sh -c` 或 `bash -lc`。

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

每连接最多 8 个活跃进程、64 个会话。输出使用有界队列，历史最多 1 MiB/进程，每流仅返回 4096 字节输出尾部。匹配后保留的命令仍持续排空输出。Unix 清理会向所管理的进程组发送信号；主动创建其他会话/进程组的后代可能逃离该组。服务被硬杀时的 Job 自动清理属于 Windows 特有保证。

插件仅观察其启动的命令，不能接管 Codex 内置终端。原生终端会话 ID 不是插件可订阅的输出句柄。进程、游标和临时日志仅在当前 MCP 连接有效；不承诺跨连接持久化，也不会自动拦截所有内置 shell 调用。技能会指导 Codex 在等待输出条件时使用这些工具。

## 代理基础设施

文档维护英文（默认）与简体中文两版；代理工作流技能和脚本使用英文。
仓库提供 `terminalhook-development` 项目技能，以及将 `terminal-wait` 安装到
独立 MCP 目标项目的辅助脚本。安装、调用、更新和验证方式见
[代理工作流与项目技能](docs/agent-workflows.zh-CN.md)。

## 验证

```powershell
npm ci
npm run check:ui
npm run build:ui
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

# 使用回环 Git 服务和隔离 Codex 目录验证本地迁移、启动自动更新和官方升级命令
python scripts/verify_git_marketplace.py

# 从发布 Zip 解压，验证无 Hook 的清单和实际二进制协议
python scripts/verify_package.py

# 完成发布验证后核对源码、Zip、安装缓存及验收记录
python scripts/audit_release.py
```

协议测试仅使用 Python 3 标准库；安装验证使用 Python 3.11+ 的 `tomllib`，在隔离 Codex 目录检查安装器、缓存二进制和配置的幂等写入，不启动模型。桌面调用路径应在实际 App 聊天中验证；协议测试不能证明宿主模型请求数。历史 CLI 验证脚本仅为可选开发诊断，不是插件的默认运行或桌面验收方式。

## 实现依据

- [Codex 插件格式与 marketplace](https://developers.openai.com/plugins/build/plugins)
- [Codex MCP 配置与超时](https://learn.chatgpt.com/docs/extend/mcp)
- [官方 Rust MCP SDK](https://github.com/modelcontextprotocol/rust-sdk)
- [portable-pty / ConPTY](https://docs.rs/portable-pty/latest/portable_pty/)
