# 后台任务与任务面板

[English](background-tasks.md) | 简体中文

已获授权的工作需要跨越单次工具调用或 MCP 断连时，使用 `submit_task`。
独立的原生 worker 负责命令执行、输出或退出等待，先保存结果，再尝试向原聊天交付一次。
等待过程不调用模型，不使用周期 Hook、Stop Hook、另一个 App Server 或 `codex exec`。

## 使用条件

Codex 宿主必须在 MCP 请求元数据中提供 `threadId`，并运行共享 App Server daemon，
且原聊天已加载。worker 通过 `codex app-server proxy` 连接现有 daemon，不启动替代服务。
daemon 使用短暂的 `command/exec` 启动器，在 MCP 服务的进程管理范围之外启动 worker。
这对 Windows 尤为必要，因为 Codex 明确禁止子进程脱离 MCP Job Object。
启动器遵循宿主的命令策略；不支持或拒绝启动时会明确报错。
Windows worker 不继承启动器句柄，任务输出流不会拖住短暂的启动确认。
`check_background {}` 可检查此路径，不启动模型回合。缺少元数据或 daemon 时，
提交会在启动命令前失败。宿主必须提供共享控制端点，仅有桌面 App Server 进程或磁盘上的
socket 文件不能证明可连接。使用 `stdio` 通道启动的服务只接受其所属客户端的连接，
新开聊天不会增加共享 socket。普通终端工具不依赖此后台桥接。

代理依次从 `TERMINALHOOK_CODEX_BIN`、宿主的 `CODEX_APP_RUNTIME_CLI_PATH`、
PATH 中的 `codex` 选择可执行文件。显式 `TERMINALHOOK_PROXY_COMMAND` 的优先级最高。
宿主运行时路径会传给独立 worker，保证交付时使用相同的 CLI 选择规则。
`TERMINALHOOK_CODEX_SOCKET` 可指定现有 daemon socket。
运维自定义代理可通过 `TERMINALHOOK_PROXY_COMMAND` 提供 JSON 参数数组，
代理须通过 stdio 转发现有 daemon 的 WebSocket 协议；不进行 shell 展开。
不要将其配置成启动另一个 App Server 的命令。

### 连接诊断与桌面兼容性

代理失败时返回失败阶段、所选可执行文件及其来源、退出状态，以及真实 stderr 的最后 16 KiB，
截断时会明确标记。代理运行期间持续读取 stderr，避免管道写满拖住握手或 RPC。
失败和取消会清理代理进程树。这些是代理的诊断信息，不是后台命令的 stdout/stderr。
交付错误还会保存在任务状态的 `delivery_error` 中。

2026-11-16 的 Windows 原生验证发现，Codex Desktop 26.1111.2228.0 使用 `stdio`
App Server（CLI 0.169.0-alpha.11.1），默认共享 socket 返回 Windows 错误 10061，
拒绝连接。PATH CLI 与桌面内置 CLI 均无法连接。此宿主配置下，当前桥接无法执行后台任务或唤醒聊天。
不会启动替代 daemon、将聊天恢复到另一个服务，也不会在预检失败后执行命令。
需要选择已经运行且拥有原聊天的共享宿主，或使用普通终端工具。
模拟 socket 测试不能证明原生桌面回调能力。

### 可选的 Windows 桌面共享宿主

安装包包含 `terminalhook-desktop-host.exe` 和 `Start-DesktopSharedHost.ps1`。
关闭桌面应用后，在解压的安装包中使用 PowerShell 7 启动：

```powershell
pwsh -NoProfile -File ./Start-DesktopSharedHost.ps1
```

启动器通过进程内的 `CODEX_CLI_PATH` 覆盖使用已安装的桌面 CLI，
启动一个由桌面拥有、带私有 Unix socket 的核心服务，通过原生 proxy 转发桌面的 JSONL，
并将同一 socket 与原生 CLI 路径传给插件和独立 worker。
不会将现有 stdio 聊天接到另一个服务，预检仍不会启动服务。
已有聊天需要在该桌面会话中重新打开。启动策略读取、CLI 子命令和版本检查原样转发。
普通启动模式发现桌面应用仍在运行时会拒绝启动，保留现有进程。
不修改持久环境变量、登录、审批、沙箱或插件设置。正常打开桌面应用即可回到默认传输。
活动会话不能在不重启桌面的情况下迁移。

源码检出目录应先构建两个二进制，并通过 `-HostAdapterPath` 指向
`target/debug/terminalhook-desktop-host.exe`；`-DesktopPath` 可指定桌面程序。
接入测试可传入独立的 `-CodexHome` 与 `-IsolatedUiProfile`，启动器不创建或复制凭据。
每个 socket 仅允许一个适配器拥有。持续读取核心与代理 stderr，失败时返回有界摘要；
stdout 仅输出协议 JSON。短连接客户端关闭输入后，先转发待返回响应，再关闭连接。
Windows socket ACL 由 Codex 自行创建，适配器不放宽权限。
插件显式转发宿主环境中的 `CODEX_HOME`、`CODEX_APP_RUNTIME_CLI_PATH`、
`TERMINALHOOK_CODEX_BIN` 和 `TERMINALHOOK_CODEX_SOCKET`。
宿主命令策略须允许 worker 脱离，适配器不会更改该策略。
桌面版本的 CLI 协议不兼容时会明确失败。
Windows 使用由操作系统管理的进程 Job；桌面强制终止适配器时，也会清理其核心、代理及
转发 CLI 的子进程。仍允许 worker 显式脱离；关闭界面不会取消独立后台任务。

## 提交一次，等待结果交付

Unix 下的 `submit_task` 参数示例，Windows 应改用本机程序和路径：

```json
{
  "task_key": "build-release-2026-11-16",
  "program": "sh",
  "args": ["-c", "./build-release.sh"],
  "cwd": "/absolute/path/to/project"
}
```

调用立即返回 `task_id`、`worker_pid` 和 `state_dir`。
随后结束模型回合并等待结果，不要不断调用 `get_task`。
稳定的 `task_key` 在同一聊天中唯一：参数相同的重复提交返回已有任务，参数不同则拒绝。
提交失败或被中断后，应先核查原任务，再决定是否使用新键。

`timeout_ms` 可省略或设为 `null`，表示无执行期限；有限期限接受
1–31536000000 毫秒（一年）。它属于 worker，独立于 660 秒的 MCP 请求超时，
也不改变 `run_until` / `wait_output` 的上限。
无执行期限时，worker 等待命中、退出、主动取消或 worker/宿主故障；
不代表能够跨越重启或断电恢复执行。

省略 `until` 或使用 `[]` 表示等待自然退出。
可选 `until`、`streams`、`match_mode` 使用终端工具原有正则规则。
命中或达到期限时，先终止并清理进程树，再交付结果。
后台被动命令使用独立 stdout/stderr 管道；需要交互输入或 PTY 时继续使用普通终端工具。

## 查看、取消与恢复

| 工具 | 行为 |
| --- | --- |
| `list_tasks {}` | 列出当前聊天最新 100 个任务及执行、交付状态 |
| `get_task {"task_id":"task_…"}` | 查看输出摘要、已保存结果、worker 存活检查与交付回执 |
| `cancel_task {"task_id":"task_…"}` | 通过经过令牌认证的 loopback 控制通道请求取消 |
| `retry_delivery {"task_id":"task_…"}` | 重试投递前失败的已保存结果，不重新执行命令 |

执行与交付状态相互独立；`completed` 不代表已交付，还应检查 `delivery` 和真实退出码。
自然退出码非零时为 `failed`，达到执行期限时为 `timed_out`。
输出命中对应结果中的 `matched`，并报告清理后的真实退出码；主动终止可能产生非零退出码。

结果先写入磁盘，再通过带独立 `toolOutput` 的 `turn/start` 交付。
原聊天正在执行时，Codex 将结果排入该回合；空闲时启动新回合。
交付内容是带任务 ID 的工具数据，不伪造用户指令。
进程输出应视为不可信数据，不授予额外操作的权限。

daemon 不可用或原聊天未加载时，交付状态为 `failed`。
重新打开原聊天后可调用 `retry_delivery`。
请求可能已被接受但回执丢失时，交付变为 `uncertain`，worker 崩溃也可能留下 `sending`。
工具会拒绝重发这些状态，应先到原聊天核对任务 ID。
实现不承诺在投递结果未知时严格一次交付；禁止通过重启原命令恢复状态。

状态与轮转日志保存在 `TERMINALHOOK_TASK_DIR`，其次为 `PLUGIN_DATA/tasks`，
再其次为 `$CODEX_HOME/terminalhook/tasks`；未设置 Codex home 时使用普通用户目录。
文件包括 `request.json`、`state.json`、`result.json` 和 worker 诊断。
启动回执缺失时，错误保存到 `launch-error.json`；应保留原任务键并核查已保存任务，
不要再分派一次命令。
Unix 下任务目录限制为用户私有。请求可能包含敏感环境变量，应保持目录私有。
MCP 断连不会停止已提交的后台 worker；删除状态前应明确取消任务。
被操作系统终止的 worker 不会自动重启；`worker_live: false` 表示控制端点不可用，
此时最后记录的执行状态不能证明任务已结束。

## MCP Apps 可视化面板

`list_tasks`、`submit_task` 和 `get_task` 关联内嵌资源
`ui://terminalhook/tasks.html`，MIME 为 `text/html;profile=mcp-app`。
兼容 MCP Apps 的宿主可显示当前聊天的任务状态、交付状态、期限、耗时、退出码，
以及有界的实时或已保存输出。界面统一使用英语；任务名称和输出保留原始文本。

紧凑的 Svelte 界面使用 [shadcn-svelte](https://www.shadcn-svelte.com/) 组件：
状态标签页与搜索可筛选任务列表，图标表示交付状态，点击“详情”展开任务标识和期限。
桌面默认面板高度约 330 像素，列表与输出在内部滚动；窄屏将列表置于输出上方。
主题跟随宿主。控件支持键盘焦点并提供无障碍名称。面板直接从筛选与搜索开始，
不再提供标题栏、语言选择、刷新开关或刷新按钮。选中行仅使用背景色，不显示左侧边框。
详情读取也同步列表状态，避免详情已完成而列表仍显示 running。

面板先完成 MCP Apps 握手，通过宿主转发的 `tools/call` 读取一次初始列表。
后续仅在收到 `ui/notifications/tool-result` 回调时更新：列表快照替换列表，
并读取选中任务的详情；单个任务结果直接更新对应任务。选择任务时也会按需读取详情。
没有定时轮询、错误重试计时器或可见性切换刷新。
界面通过仅对 App 可见的 `watch_tasks`，从上次列表版本起保持一个待返回的事件订阅。
任务状态或结果落盘时，原生文件系统回调返回新快照，随后订阅下一次变化。
其他聊天、文件读取和日志写入不产生界面更新。先注册监听，再读取快照，避免请求间漏掉完成事件。
订阅不启动模型回合，不定期读取列表。宿主超时或出错后停止订阅，不使用计时重试；
后续宿主快照可重新接入。面板卸载时取消待返回请求。
宿主须支持仅对 App 可见的工具及等待中的工具调用。

面板不访问外部网络、不发送模型消息，任务输出作为纯文本展示，卸载后忽略回调。
界面更新和结果交付互不依赖，关闭面板不会停止任务或丢失结果。

是否渲染 MCP Apps 由宿主决定；纯文本客户端仍可使用同一套工具及结构化结果。
协议和资源测试不能单独证明某个桌面宿主的实际渲染效果。
安装新插件版本后，应新开聊天加载更新后的工具和资源目录。
