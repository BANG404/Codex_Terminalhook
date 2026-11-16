# 后台任务与任务面板

[English](background-tasks.md) | 简体中文

已获授权的工作需要跨越单次工具调用或 MCP 断连时，使用 `submit_task`。
独立的原生 worker 负责命令执行、输出或退出等待，先保存结果，再尝试向原聊天交付一次。
等待过程不调用模型，不使用周期 Hook、Stop Hook、另一个 App Server 或 `codex exec`。

## 使用条件

Codex 宿主必须在 MCP 请求元数据中提供 `threadId`，并运行共享 App Server daemon，
且原聊天已加载。worker 通过 `codex app-server proxy` 连接现有 daemon，不启动替代服务。
`check_background {}` 可检查此路径，不启动模型回合。缺少元数据或 daemon 时，
提交会在启动命令前失败。当前桥接已在 Codex Desktop daemon 0.169.0 上完成连通性检查；
普通终端工具不依赖此后台桥接。

`TERMINALHOOK_CODEX_BIN` 指定 CLI 可执行文件，默认 `codex`。
`TERMINALHOOK_CODEX_SOCKET` 可指定现有 daemon socket。
运维自定义代理可通过 `TERMINALHOOK_PROXY_COMMAND` 提供 JSON 参数数组，
代理须通过 stdio 转发现有 daemon 的 WebSocket 协议；不进行 shell 展开。
不要将其配置成启动另一个 App Server 的命令。

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
Unix 下任务目录限制为用户私有。请求可能包含敏感环境变量，应保持目录私有。
MCP 断连不会停止已提交的后台 worker；删除状态前应明确取消任务。
被操作系统终止的 worker 不会自动重启；`worker_live: false` 表示控制端点不可用，
此时最后记录的执行状态不能证明任务已结束。

## MCP Apps 可视化面板

`list_tasks`、`submit_task` 和 `get_task` 关联内嵌资源
`ui://terminalhook/tasks.html`，MIME 为 `text/html;profile=mcp-app`。
兼容 MCP Apps 的宿主可显示当前聊天的任务状态、交付状态、期限、耗时、退出码，
以及有界的实时或已保存输出；面板提供英文和简体中文切换。

面板先完成 MCP Apps 握手，再通过宿主转发的 `tools/call` 刷新状态。
不访问外部网络，不发送模型消息；隐藏时暂停自动刷新，同一时间只执行一轮刷新，
失败后降低刷新频率，卸载时停止刷新。任务输出作为纯文本展示。
界面刷新和结果交付互不依赖，关闭面板不会停止任务或丢失结果。

是否渲染 MCP Apps 由宿主决定；纯文本客户端仍可使用同一套工具及结构化结果。
协议和资源测试不能单独证明某个桌面宿主的实际渲染效果。
安装新插件版本后，应新开聊天加载更新后的工具和资源目录。
