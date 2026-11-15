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

## 任务完成与 alpha PR

仓库任务以将改动提交到 `alpha` 为收尾步骤。代理遵循仓库内的
[AGENTS.md](../AGENTS.md)：完成实现与相关检查，在基于最新 `origin/alpha`
的 `codex/` 工作分支上只提交本任务的改动，推送该分支，并创建或更新以
`alpha` 为目标的 PR。除非用户要求仅本地修改或指定其他目标，任务完成时自动
执行提交、推送和创建 PR，无需再次确认。

保留无关改动；并发任务使用独立 worktree。PR 标题与描述使用英文，并说明验证结果。
核对目标分支、来源分支、变更文件与初始 CI 状态，然后报告 PR 链接和实际 CI 状态。
PR 提交后检查可能仍在运行。提交失败时保留工作并报告阻塞原因，不将仅本地修改
报告为已提交。合并、后续阶段提升和发布仍是独立操作。

## CI

Alpha PR 在 Windows runner 上执行格式检查、Clippy、Rust 单元测试、
Python 协议与发布策略测试，并通过独立任务构建分发包。
包内二进制会在解压后的目录中测试，包括路径含空格的情况。
产物包含 ZIP、SHA256SUMS.txt 和源码构建标识。
运行期产物、本地聊天轨迹、凭据和编译后二进制不提交到源码仓库。
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
保持 **publish** 关闭可执行端到端验证；显式启用后才会发布 GitHub Release。

工作流检查分支、复用 alpha 源码验证、构建一次最终渠道版本，并检查包内二进制，
包括显式的 65 秒和 90 秒等待回归测试。发布使用该已验证产物；
阶段提升不会将 alpha 二进制重新标记为稳定版。
具备写权限的任务创建带注释的 `v<version>` 标签，并上传分发包、校验和及构建标识。
非生产渠道作为预发布版本。重跑可补完未完成的草稿，但不能替换已有资源或修改标签指向。
稳定版还会拒绝将同一版本用于不同的源码提交。

发布使用 GitHub 工作流令牌，无需个人访问令牌或外部 runner。
工具直接暴露和无模型请求仍需在实际桌面 App 中验证；协议 CI 无法证明桌面行为。
默认包没有生命周期 Hook，历史 0.3.1 实现保存在 `legacy/stop-hook`。

## 本地构建

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_package.py
```

构建暂存于 `dist/package`，与源码插件目录分离。
本地安装器在该暂存分发包存在时优先选择它。安装后请保留 marketplace 目录。

## 许可证

源码和发布包均使用 `LICENSE` 中的 MIT 许可证。
Cargo 依赖继续遵循各自作者的许可证。
