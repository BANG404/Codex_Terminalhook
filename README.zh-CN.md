# TerminalHook

[English](README.md) | 简体中文

[![CI](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml/badge.svg)](https://github.com/BANG404/Codex_Terminalhook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

TerminalHook 是 Rust MCP 服务和 Codex 插件，用于持久后台命令、事件驱动任务面板，
以及向原聊天交付完成结果。Codex 会主动将预计耗时较长或完成时间难以预估的任务提交一次，随后结束回合，任务完成后接收已保存结果。
原生 worker 在本地等待，不轮询模型，可跨越 MCP 断连继续运行。

原生测试和分发覆盖 Windows x64、Linux x64/ARM64、macOS Intel/Apple Silicon。
命令使用独立 stdout/stderr 管道，stdin 关闭。Windows Job Object 和 Unix 进程组负责子进程约束。

本次发布移除原有同步/交互式 MCP 工具：`run_until`、`wait_output`、`write_input`、`stop_process`。
对应调用分发、连接内会话、游标续读、保留进程控制及 PTY 后端也已删除。
现有集成需迁移到下述后台任务流程；前台和交互式命令请使用宿主终端。

[安装](#安装与升级) · [任务工具](#任务工具) · [发布版本](https://github.com/BANG404/Codex_Terminalhook/releases) · [开发与发布](docs/releasing.zh-CN.md)

## 安装与升级

TerminalHook 通过 Git marketplace 分发，包含原生二进制、MCP 配置和运行期技能。安装器自动选择当前系统和架构。Windows 保留 `marketplace` 及 `marketplace-alpha/beta/rc`；Unix 分支在渠道分支后追加 `-linux-x64`、`-linux-arm64`、`-macos-x64` 或 `-macos-arm64`。各平台/渠道在首次执行发布版 Release 工作流后可用。源码分支不包含编译后二进制，不能直接作为二进制 marketplace 安装。

开发采用 `alpha → beta → rc → main` 阶段分支。变更通过 PR：`alpha` 完整验证，后续提升核对源码树并复用原验证产物，避免 push 与 PR 重复执行。根据适用稳定版之后的 Conventional Commits 计算共同目标版本；alpha/beta/rc 添加渠道、提交序号和源码 SHA，production 使用无后缀目标版本。发布由 Release 工作流手动触发。详见 [开发、CI/CD 和阶段发布](docs/releasing.zh-CN.md)。项目使用 [MIT 许可证](LICENSE)。

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

更新跟随所选分支，不查询 GitHub 的 latest-release API。项目没有自建后台更新器，也不承诺 App 长期开启时的定时检查间隔。更新后新开 Codex 聊天；已有聊天保留已加载的工具目录，持久后台 worker 独立继续运行。参见[官方 marketplace 命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace)。

离线安装或本地开发：从 [GitHub Releases](https://github.com/BANG404/Codex_Terminalhook/releases) 下载匹配系统/架构的 ZIP，解压到固定目录；Windows 执行 `pwsh -NoProfile -File ./install.ps1 -Source .`，Linux/macOS 执行 `sh ./install.sh --source .`。Unix 应使用 `unzip` 等保留可执行权限的解压工具。不指定本地来源时安装器选择 Git 稳定渠道。CI 预览 ZIP 也可从 [Actions artifacts](https://github.com/BANG404/Codex_Terminalhook/actions) 获取。

从源码构建并安装本地快照：

```powershell
pwsh -NoProfile -File scripts/package.ps1
pwsh -NoProfile -File scripts/install.ps1 -Source ./dist/package
```

Linux/macOS 使用 `python3 scripts/package.py --channel development` 构建，再执行 `sh scripts/install.sh --source ./dist/package` 安装。原生源码构建需要固定版本的 Rust 工具链、Python 3.11+，以及带 npm 的 Node.js 20.19+ 或 22.12+，用于打包 Svelte 面板。Linux 二进制使用 glibc（基于 Ubuntu 24.04 构建）；Alpine/musl 和 Windows ARM64 暂不作为发布目标。macOS 产物未签名，也未公证。

`package.ps1` 生成 ZIP 及 `dist/SHA256SUMS.txt`。本地安装需要保留目录，文件变化后重新执行安装器，不下载远端更新。两种模式均使用标准 `codex plugin marketplace add`、`codex plugin add` 命令及 Codex 配置编辑接口，保留已有设置，并将 `mcp__terminalhook` 加入 `features.code_mode.direct_only_tool_namespaces`。此管理过程不发起模型请求。

如果用户尚未为 `submit_task` 或其 TerminalHook 服务配置审批模式，安装器会为该工具设置 `approval_mode = "approve"`。`examples/standalone.config.toml` 的独立 MCP 示例包含同一设置。独立服务和插件二选一，避免重复工具。项目中 `.codex/config.toml` 的启用设置只对受信任项目生效，也不能替代安装。MCP 命令使用本机账户权限，不继承 Codex 内置 shell 的逐命令沙箱。

卸载：

```powershell
codex plugin remove terminalhook@terminalhook-local
codex plugin marketplace remove terminalhook-local
```

## 任务工具

| 工具 | 行为 |
| --- | --- |
| `check_background` | 验证现有共享宿主及原聊天，不启动模型回合 |
| `submit_task` | 使用稳定的 `task_key` 提交预计耗时较长或完成时间难以预估的命令，立即返回任务 ID |
| `list_tasks` | 明确查询原聊天的任务，不打开面板 |
| `get_task` | 按需诊断已保存结果、执行状态和交付回执 |
| `cancel_task` | 取消明确选择的后台任务，清理其进程树 |
| `retry_delivery` | 仅重试提交前交付失败的已保存结果，不重新执行命令 |
| `watch_tasks` | 仅面板可见，订阅当前任务的原生状态事件 |

直接调用 `mcp__terminalhook` 命名空间下的工具。安装器将其加入
`[features.code_mode].direct_only_tool_namespaces` 并保留其他设置；手动安装插件时也需配置宿主。
更新后新开聊天，以便工具目录移除旧接口。TerminalHook 是插件名称；当前包使用原生事件与宿主回调，
不注册 Stop 等生命周期 Hook。历史 Stop Hook 实现保存在 [legacy/stop-hook](https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook)。

用户请求范围内的命令预计耗时较长，或可能需要等待且完成时间难以预估时，主动调用 `submit_task`。
用户的任务请求本身足以选择后台执行。首次提交前调用 `check_background`。
后台提交需要可连接的共享 Codex daemon、CLI proxy 及宿主提供的
`threadId` 元数据；预检不会猜测聊天 ID，也不会启动另一个 daemon。
只有 stdio 且没有共享端点的宿主无法提交后台任务。Windows Desktop 的可选共享宿主启动器见
[后台任务指南](docs/background-tasks.zh-CN.md)。

以下示例提交一条构建命令。

```json
{
  "task_key": "build-project-2026-11-17",
  "program": "sh",
  "args": ["-c", "./build.sh"],
  "cwd": "/absolute/path/to/project"
}
```

Windows 使用本机程序或 `pwsh -NoProfile -Command`，并指定本机绝对工作目录。
PowerShell UTF-8 输出应在同一 PowerShell 进程中加载包内
`skills/terminal-wait/scripts/Initialize-Utf8.ps1`。命令使用参数数组；需要 Shell 语法时显式调用 Shell。

`submit_task` 返回 `task_id`、`worker_pid`、`state_dir`。随后结束回合，等待原聊天接收完成结果；
不要反复调用 `get_task` 或 `list_tasks` 等待。相同键及参数复用已有任务，参数变化会被拒绝。
启动确认丢失时应先检查原任务，再决定是否执行新命令。详见[后台任务指南](docs/background-tasks.zh-CN.md)。

## 输出、期限与恢复

省略 `until` 或使用 `[]` 等待自然退出。可选命名 Rust 正则规则在命中时终止命令；
命中一行输出不能证明自然退出成功。规则名称须唯一、1–128 字节；最多 16 条规则、
模式不超过 4096 字节、捕获组不超过 32 个。拒绝空匹配、前后查找及反向引用。
默认逐行匹配；`match_mode: "window"` 使用有界 64 KiB 窗口跨行匹配。
`streams` 可选择 `stdout` 和/或 `stderr`，不同流独立匹配；逐行匹配拒绝超过 65536 字节的行。

输出按 UTF-8 增量解码，无效字节替换为 U+FFFD；匹配前移除 ANSI CSI/OSC 并归一化 CRLF。
每条流返回的输出尾部不超过 4096 字节；匹配及捕获文本共享 8192 字节预算。
原始 stdout/stderr 保留在任务轮转日志中，每份文件上限 2 MiB，另保留一份备份。
未设置正则的自然退出任务可处理长输出，不累积无界匹配缓冲区。

默认省略 `timeout_ms`。省略或设为 `null` 时，worker 没有执行期限。
只有用户或操作明确要求时才设置 1–31536000000 毫秒的有限期限。
MCP 请求超时只约束提交调用，worker 独立运行。命中、到期、主动取消均在保存结果前清理进程树。
根进程自然退出时也先清理后代，再排空最终输出。Windows 使用带启动分配门控的
kill-on-close Job。Unix 使用进程组，主动创建其他组或会话的后代可能脱离约束。

执行与交付状态相互独立。自然退出结果为 `status: "exited"`，包含真实 `exit_code`；
非零退出码将任务标记为失败。不再提供连接内进程句柄及输出游标。
交付失败保留已保存结果；诊断确认提交前失败后才使用 `retry_delivery`。
交付结果不明确时先核对原聊天，不盲目重发。worker 可跨越 MCP 断连，但重启或 worker 故障后不恢复执行。
面板通过原生状态事件订阅更新，不轮询模型；关闭面板不会停止任务或丢失交付结果。

## 验证

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

协议回归验证仅能发现后台工具，直接调用四个已删除名称均失败；通过独立 loopback 宿主夹具，
覆盖独立执行、聊天隔离、幂等、输出匹配、UTF-8、日志、进程清理和交付恢复，不启动模型回合。
Release 额外要求 65 秒任务跨越 MCP 断连、90 秒 UTF-8 输出及 Windows PowerShell UTF-8 输出。
真实桌面渲染及宿主交付仍需单独接入验收；夹具证据不代表真实桌面结果。
安装及 Git marketplace 验证使用隔离 Codex home。详细门禁、覆盖率、变异和性能检查见
[开发与发布](docs/releasing.zh-CN.md)。

## 实现参考

- [Codex 插件格式与 marketplace](https://developers.openai.com/plugins/build/plugins)
- [官方 Rust MCP SDK](https://github.com/modelcontextprotocol/rust-sdk)
