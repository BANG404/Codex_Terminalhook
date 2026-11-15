# TerminalHook for Windows x64

[English](README.md) | 简体中文

解压本目录（路径可包含空格），安装 Git 稳定渠道：

```powershell
pwsh -NoProfile -File .\install.ps1
```

需要已安装 Codex CLI 和 Git。安装器注册 `BANG404/Codex_Terminalhook` 的 `marketplace` 分支，其中包含编译好的 Windows x64 二进制。Codex 启动时的 marketplace 刷新负责更新安装缓存，也可执行 `codex plugin marketplace upgrade terminalhook-local` 显式刷新。使用 `-Channel alpha`、`-Channel beta` 或 `-Channel rc` 跟踪预发布渠道；各渠道须先执行首次发布版 Release 工作流。marketplace 保留历史名称，已有本地安装迁移时无需改变插件 ID。注册失败会恢复原来源和跟踪分支。更新后新开 Codex 会话，活跃进程不做迁移。

运行期不需要 Rust、Python 或 Node。若需离线安装当前解压包，执行 `pwsh -NoProfile -File .\install.ps1 -Source .`，并保留该目录。此模式不需要 Git，不下载后续更新；替换本地文件后重新执行安装器即可更新。不带 `-Source` 时始终安装 Git 分发版。

0.4.0 默认使用原生直接调用，不包含 Stop 或其他生命周期 Hook，无需信任 Hook。安装器通过 Codex 配置接口将 `mcp__terminalhook` 加入用户的 `[features.code_mode].direct_only_tool_namespaces`，保留其他命名空间和设置；该管理过程不启动模型。新聊天直接调用 `run_until` 并等待结果，长等待不经 `functions.exec` 包装，命令运行及等待不使用 Codex CLI。通过桌面 UI 手动安装而未运行此脚本时，也需配置该命名空间；项目配置可能覆盖用户设置。宿主必须支持该配置。

历史同步 Stop Hook 版本保存在 https://github.com/BANG404/Codex_Terminalhook/tree/legacy/stop-hook 。新版不提供 `submit_task`、`prepare_task`、`await_tasks` 或 `audit_event`。

保留原有四个工具：`run_until` 启动命令并等待正则；`wait_output` 从游标继续等待；`write_input` 发送输入并可同时等待；`stop_process` 清理进程树。等待在本地进行，不返回需要模型轮询的 `pending`。

可这样向 Codex 发出请求：

> 使用 TerminalHook 运行我的开发服务器，等到输出 Local 地址后返回并保留服务。

运行命令需要明确的绝对工作目录。默认使用独立 stdout/stderr 管道；交互命令可选 `mode: "pty"`（ConPTY），对应合并的 `terminal` 输出流。默认正则按行匹配，跨行时选择有界 `window` 模式。默认等待上限 10 分钟，MCP 超时 660 秒。

默认在命中或超时时终止命令；服务场景使用 `on_match: "keep_running"` 并在结束后调用 `stop_process`。可选绝对 `log_dir` 保留轮转的原始日志；默认临时日志在 MCP 连接结束时删除。

插件负责它启动的命令，不能订阅 Codex 内置终端。进程句柄、输出游标和临时日志仅在当前 MCP 连接有效；重连会清理原进程。MCP 命令使用本机账户权限，保留 Codex 默认工具审批行为。

卸载：`codex plugin remove terminalhook@terminalhook-local`；随后可按需执行 `codex plugin marketplace remove terminalhook-local`。
