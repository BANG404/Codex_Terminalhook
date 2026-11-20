# TerminalHook 原生分发包

[English](README.md) | 简体中文

解压匹配系统/架构的 ZIP（路径可包含空格），`platform.json` 标识包的平台。Windows 安装 Git 稳定渠道：

```powershell
pwsh -NoProfile -File .\install.ps1
```

Linux/macOS 安装 Python 3.11+ 后使用 POSIX 启动脚本：

```sh
sh ./install.sh
# 预发布渠道：
sh ./install.sh --channel alpha
# 离线/本地安装当前包：
sh ./install.sh --source .
```

Git 安装需要已安装 Codex CLI 和 Git。Windows 使用 `marketplace` 或 `marketplace-alpha/beta/rc`；Linux/macOS 追加原生平台（`-linux-x64`、`-linux-arm64`、`-macos-x64` 或 `-macos-arm64`），安装器自动选择引用。Windows 使用 `-Channel alpha/beta/rc`、Unix 使用 `--channel alpha/beta/rc` 选择预发布渠道。各平台/渠道须先执行首次发布版 Release 工作流。Codex 启动时刷新 marketplace 缓存，也可执行 `codex plugin marketplace upgrade terminalhook-local` 显式刷新。保留历史 marketplace 名称和插件 ID。注册失败恢复原来源和跟踪分支。更新后新开会话，活跃进程不迁移。

运行期不需要 Rust、Python 或 Node；仅 Unix 安装阶段需要 Python 3.11+，Windows 安装使用 PowerShell 7。Windows 离线安装执行 `pwsh -NoProfile -File ./install.ps1 -Source .`。保留本地安装目录，此模式不需要 Git，不下载后续更新。不带 `-Source` / `--source` 时选择 Git 分发。Unix 解压需保留可执行权限（例如使用 `unzip`）。Linux 构建使用与 Ubuntu 24.04 兼容的 glibc；macOS 二进制未签名，也未公证。

前台及交互工作使用宿主终端。后台命令 stdin 关闭，输出使用独立 stdout/stderr 管道。
自然退出结果包含 `status: "exited"` 和真实退出码。

安装器将 `mcp__terminalhook` 加入 `[features.code_mode].direct_only_tool_namespaces`，并保留其他设置。
如果用户尚未为 `submit_task` 或其 TerminalHook 服务配置审批模式，安装器会将该工具的审批模式设为 `approval_mode = "approve"`。
手动安装也需配置宿主。升级后新开聊天加载当前工具目录。

请求示例：

> 运行构建，完成后告诉我结果。

耗时较长或完成时间难以预估的命令可直接调用 `submit_task`。使用稳定的 `task_key`、程序及参数和绝对 `cwd`。
工具在启动前校验共享 Codex daemon 和原聊天，校验失败时在同一次调用中返回错误。
省略 `timeout_ms` 表示无执行期限，省略 `until` 表示等待自然退出。
拿到任务 ID 后结束回合，等待结果投递到原聊天。在兼容宿主中，提交会打开任务面板。
用 `list_tasks` 查看当前聊天的任务，用 `get_task`、`cancel_task` 或 `retry_delivery` 操作单个任务。
详见[后台任务指南](https://github.com/BANG404/Codex_Terminalhook/blob/alpha/docs/background-tasks.zh-CN.md)。

如果桌面端使用 stdio 且提交报告共享端点不可用，请退出桌面应用，再从本解压目录运行共享宿主启动器。
Windows 使用 `pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1`，macOS 使用 `sh ./Start-DesktopSharedHost.sh`。
在该桌面会话中重新打开原聊天并提交任务。正常启动桌面即可恢复默认传输。

卸载：`codex plugin remove terminalhook@terminalhook-local`；随后可按需执行 `codex plugin marketplace remove terminalhook-local`。
