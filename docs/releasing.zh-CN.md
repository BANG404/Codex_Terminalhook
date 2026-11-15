# 开发与发布

[English](releasing.md) | 简体中文

TerminalHook 使用 `alpha -> beta -> rc -> main` 阶段流程。
功能 PR 以 `alpha` 为目标。通过同一仓库内的 PR 提升已测试的阶段；
CI 会拒绝跳过阶段以及来自 fork 的提升。仓库初始快照以相同提交初始化四个分支，后续变更均通过 PR 进行。

| 来源分支 | 产物版本 | GitHub Release |
| --- | --- | --- |
| alpha | `0.4.0-alpha.<commit-count>.g<12-char-sha>` | 预发布 |
| beta | `0.4.0-beta.<commit-count>.g<12-char-sha>` | 预发布 |
| rc | `0.4.0-rc.<commit-count>.g<12-char-sha>` | 预发布 |
| main | `0.4.0` | 稳定版 |
| 功能分支 / PR | `0.4.0-development.<commit-count>.g<12-char-sha>` | 不可发布 |

`Cargo.toml`、`Cargo.lock` 和源码插件清单共享稳定版本基线。
准备下一个稳定版本时须同步修改。渠道后缀在打包时生成，不修改受版本控制的文件。
二进制的 `--version`、MCP 服务标识、包内清单和 `build-info.json` 保持一致。
CI 会拒绝工作区不干净、源码 SHA 不匹配或渠道与分支不匹配的构建。

## 任务完成与自动交付到 alpha

实现完成并通过相关本地检查后，代理只在专属 `codex/` 分支提交本任务改动，然后执行：

```powershell
python scripts/deliver.py submit --body-file D:/path/to/pr-body.md
```

PR 描述文件应放在跟踪文件之外（例如被忽略的 `artifacts/` 目录）。
辅助脚本要求工作区干净、英文 Conventional Commit 标题、origin 拉取和推送 URL
指向相同仓库、源码能与 alpha 无冲突合并。脚本不强制推送，创建或复用来源准确的
同仓库 alpha PR，跟随实际 CI 并自动合并。仅本地工作或其他目标需显式指定。
参见 [AGENTS.md](../AGENTS.md)。

该方式参考 sandboxpi 的 [alpha 交付实现](https://github.com/BANG404/sandboxpi/blob/alpha/scripts/delivery/git-workflow.mjs)：
交付客户端先观察真实检查再合并，CI 工作流保留只读权限，不通过特权 PR 任务执行
待合并代码。`Quality checks` 和 `Package verification` 必须来自 GitHub Actions
App 15368，均对应预期来源 HEAD、同一次最新 `ci.yml` PR 运行及其最新尝试。
脚本核对对应真实 job，在合并前再次读取 PR 身份及检查，再提交绑定 SHA 的
`merge_method=merge` 请求，最后验证实际提交有两个父提交且第二个是指定来源。
不使用 GitHub 原生 Auto-merge，目标 PR 也不能已经启用该功能。

仍须满足审阅要求及分支保护。检查缺失或待完成时等待；检查失败/取消、冲突、
来源变化、仓库不符或 PR 未合并便关闭时停止。严格保护可能要求在 alpha 前进后
更新来源分支；须在本任务中处理并重新验证。不会削弱保护、自动改写来源分支，
也不会通过任务交付触发 beta/rc/main 提升或发布。

公共 Git 目录中的锁防止多个 worktree 重复跟随同一 PR。
中断后保留本地提交、远端分支和 PR，按输出的原始身份恢复，不重新提交源码：

```powershell
python scripts/deliver.py follow 123 --head <40-character-source-sha> --merge
```

省略 `--merge` 时只观察。网络/服务端暂时读取失败会有限重试；写入不会盲目重试，
创建 PR 或合并响应不明确时读取实际 PR 核实。默认跟随期限一小时，可用
`--timeout` 指定秒数。残留锁须先核实再删除。运行针对性回归测试：
`python -m unittest discover -s tests -p test_delivery.py -v`。

## CI

Alpha PR 在 Windows runner 上执行格式检查、Clippy、Rust 单元测试、
Python 协议与发布策略测试，并通过独立任务构建分发包。
包内二进制会在解压后的目录中测试，包括路径含空格的情况。
产物包含 ZIP、SHA256SUMS.txt 和源码构建标识。
运行期产物、本地聊天轨迹、凭据和编译后二进制不提交到源码分支。
编译后二进制仅由 Release 工作流提交到独立 marketplace 分发分支。
Actions 依赖固定到提交 SHA。

分支必需检查保持为 **Quality checks** 和 **Package verification**。
变更通过 PR 进行；push 事件不再重复执行 PR 验证。更新 PR 后，过时的 CI 运行会被取消。
Alpha PR 完整执行一次检查。Beta、rc 和 main 的提升 PR 使用轻量 Linux 任务：
强制阶段顺序、要求合并树等于来源树、查找与该已测试源码树完全一致的成功 alpha CI 包，
并保留其运行、产物和校验和标识。证据缺失、过期或不匹配均会阻止提升。
提升期间不进行 Rust 编译、协议重测或二进制重命名。
`promotion-validation.json` 链接原始证据。源码树包含工作流和测试文件，因此 CI 变更也必须重新通过 alpha 验证。

## 提升源码

在已完成身份验证的 checkout 中执行：

```powershell
./scripts/promote.ps1 -Target beta
./scripts/promote.ps1 -Target rc
./scripts/promote.ps1 -Target main
```

每条命令只创建下一个阶段的 PR。CI 通过后审阅并合并。
这些命令不发布产物。

## CD / 手动发布

在 GitHub Actions 中选择 **Release**，选择工作流分支及匹配的渠道（`production` 使用 `main`）。
保持 **publish** 关闭可执行端到端验证；显式启用后才会发布 GitHub Release 并推进对应 Git marketplace 渠道。

工作流检查分支、复用 alpha 源码验证、构建一次最终渠道版本，并检查包内二进制，
包括显式的 65 秒和 90 秒等待回归测试。发布使用该已验证产物；
阶段提升不会将 alpha 二进制重新标记为稳定版。
具备写权限的任务创建带注释的 `v<version>` 标签，并上传分发包、校验和及构建标识。
非生产渠道作为预发布版本。重跑可补完未完成的草稿，但不能替换已有资源或修改标签指向。
稳定版还会拒绝将同一版本用于不同的源码提交。

不可变 Release 发布后，`scripts/publish_marketplace.py` 下载其 ZIP，核对与已验证本地产物一致，
将 ZIP 的完整文件集（包括 `terminalhook.exe`）及发布版 `SHA256SUMS.txt` 写入 Git 树。
此步骤不会重新编译或下载另一份二进制。

| 发布渠道 | 分发分支 | 安装器选项 |
| --- | --- | --- |
| production | `marketplace` | 默认 / `-Channel production` |
| alpha | `marketplace-alpha` | `-Channel alpha` |
| beta | `marketplace-beta` | `-Channel beta` |
| rc | `marketplace-rc` | `-Channel rc` |

首次分发提交没有源码历史父提交；后续提交以当前分发 HEAD 为父提交，不强制更新分支，
并发写入会被拒绝。内容相同的重跑不写入新提交；同版本内容变化、源码历史回退、
版本降级和跨渠道更新均会被拒绝。上一版的过时文件不会带入新树。
分支在首次发布时创建，创建前该渠道不能安装。不要向这些分支提交源码 PR 或手动编辑；
仓库规则须允许发布令牌创建并快进更新这四个分支。

marketplace 步骤失败时，已发布的不可变 GitHub Release 保留。
重跑同一 Release 请求可补充分发，但不能替换资源或覆盖更新的 marketplace HEAD。
**publish** 关闭时，Release 和 Git marketplace 引用均不会变化。

目录保留历史名称 `terminalhook-local`，以保留插件 ID 和设置。
`source.path` 指向 **Git 快照内部**的本地目录；marketplace 注册来源本身是 Git URL，
跟踪可移动的渠道分支，不固定到发布标签或提交。启动刷新与安装缓存更新由 Codex 负责，
项目不增加更新守护进程或生命周期 Hook。

CI 固定使用 Codex CLI 0.167.0 运行 `scripts/verify_git_marketplace.py`。
回环 HTTP Git 服务验证本地安装迁移、渠道切换失败恢复、app-server 启动自动更新，
以及官方 marketplace 升级命令替换二进制。验证使用隔离 Codex 目录，不创建模型轮次。
这不代表所有桌面版本都能热切换活跃 MCP 连接。

发布使用 GitHub 工作流令牌，无需个人访问令牌或外部 runner。
工具直接暴露和无模型请求仍需在实际桌面 App 中验证；协议 CI 无法证明桌面行为。
默认包没有生命周期 Hook，历史 0.3.1 实现保存在 `legacy/stop-hook`。

## 本地构建

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_package.py
```

构建暂存于 `dist/package`，与源码插件目录分离。
使用 `./scripts/install.ps1 -Source ./dist/package` 安装本地包，并保留该目录。
不带 `-Source` 时，即使从源码 checkout 或解压 ZIP 执行安装器，也跟踪 Git 稳定渠道。

## 许可证

源码和发布包均使用 `LICENSE` 中的 MIT 许可证。
Cargo 依赖继续遵循各自作者的许可证。
