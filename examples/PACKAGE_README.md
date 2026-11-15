# TerminalHook for Windows x64

解压本目录到固定位置（路径可包含空格），安装：

```powershell
pwsh -NoProfile -File .\install.ps1
```

需要已安装 Codex CLI。运行期不需要 Rust、Python 或 Node。安装会注册当前目录为本地 marketplace 并启用 TerminalHook；请保留此目录，后续可重新运行安装脚本更新。新开 Codex 会话加载工具。

0.3.0 新增桌面 Agent 的同步 Stop Hook 流程：`submit_task` 提交长任务后立即返回，Agent 结束本轮生成，宿主的 MCP Hook 在本地等待完成后再让 Agent 续跑。此运行流程不需要 Codex CLI。安装更新后在桌面 App 的 Review hooks / Settings > Hooks 审阅并信任新增 Hook。`prepare_task` / `await_tasks` / `audit_event` 由宿主调用，不应由模型手动调用。模型请求记录和 Stop 等待记录的文件路径由 `submit_task.audit_path` 返回。

保留原有四个工具：`run_until` 启动命令并等待正则；`wait_output` 从游标继续等待；`write_input` 发送输入并可同时等待；`stop_process` 清理进程树。等待在本地进行，不返回需要模型轮询的 `pending`。

可这样向 Codex 发出请求：

> 使用 TerminalHook 运行我的开发服务器，等到输出 Local 地址后返回并保留服务。

运行命令需要明确的绝对工作目录。默认使用独立 stdout/stderr 管道；交互命令可选 `mode: "pty"`（ConPTY），对应合并的 `terminal` 输出流。默认正则按行匹配，跨行时选择有界 `window` 模式。默认等待上限 10 分钟，MCP 超时 660 秒。

默认在命中或超时时终止命令；服务场景使用 `on_match: "keep_running"` 并在结束后调用 `stop_process`。可选绝对 `log_dir` 保留轮转的原始日志；默认临时日志在 MCP 连接结束时删除。

插件负责它启动的命令，不能订阅 Codex 内置终端。进程句柄、输出游标和临时日志仅在当前 MCP 连接有效；重连会清理原进程。MCP 命令使用本机账户权限，保留 Codex 默认工具审批行为。

卸载：`codex plugin remove terminalhook@terminalhook-local`；随后可按需执行 `codex plugin marketplace remove terminalhook-local`。
