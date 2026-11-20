# 开发与发布

[English](releasing.md) | 简体中文

TerminalHook 使用 `alpha -> beta -> rc -> main` 阶段流程。
功能 PR 以 `alpha` 为目标。通过同一仓库内的 PR 提升已测试的阶段；
CI 会拒绝跳过阶段以及来自 fork 的提升。仓库初始快照以相同提交初始化四个分支，后续变更均通过 PR 进行。

| 来源分支 | 产物版本 | GitHub Release |
| --- | --- | --- |
| alpha | `<target>-alpha.<commit-count>.g<12-char-sha>` | 预发布 |
| beta | `<target>-beta.<commit-count>.g<12-char-sha>` | 预发布 |
| rc | `<target>-rc.<commit-count>.g<12-char-sha>` | 预发布 |
| main | `<target>` | 稳定版 |
| 功能分支 / PR | `<target>-development.<commit-count>.g<12-char-sha>` | 不可发布 |

`Cargo.toml`、`Cargo.lock` 和源码插件清单共享首发版本及最低版本基线。
普通发布无需修改这些文件；显式调整最低版本时须同步修改三者。
目标版本和渠道后缀在打包时生成，不修改受版本控制的文件。
二进制的 `--version`、MCP 服务标识、包内清单和 `build-info.json` 保持一致。
Release 会拒绝工作区不干净、源码 SHA 不匹配或渠道与分支不匹配的构建。

## 自动计算目标版本

`scripts/version.py` 读取适用的最高稳定 Git 标签 `vMAJOR.MINOR.PATCH`，
并分析其保留的源码之后所有非合并提交的完整消息，按
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
选择影响最大的变更，每个发布周期只递增一次：

| 尚未正式发布的提交 | 目标版本变化 |
| --- | --- |
| `fix:` 或 `perf:`（允许 scope） | 递增补丁号 |
| `feat:`（允许 scope） | 递增次版本号，补丁号归零 |
| 任意类型带 `!`、`BREAKING CHANGE:` 或 `BREAKING-CHANGE:` | 1.0 之前递增次版本号、补丁号归零；1.0 起递增主版本号、次版本号和补丁号归零 |
| 其他类型或不符合约定的消息 | 不递增版本号 |

提交类型不区分大小写；破坏性变更 footer 必须使用上述大写形式。
例如，`v0.5.0` 之后只有修复时目标为 `0.5.1`，存在新功能或破坏性变更时目标为 `0.6.0`。
多个修复不会多次递增补丁号。1.0 之前的破坏性变更规则是本项目的明确策略，
符合 [SemVer 对初始开发阶段的定义](https://semver.org/spec/v2.0.0.html#spec-item-4)。

脚本沿源码树不变的交付和阶段合并追溯保留的源码，因此 main 上的稳定标签即使尚未合回 alpha
也可作为基线。同一源码的 alpha、beta、rc 共用目标版本，production 去掉预发布后缀。
阶段合并消息不会再次触发版本升级。alpha 的新提交不会改变旧 beta/rc 候选版本。
同一保留源码的稳定标签确保重跑时目标版本和构建身份不变。
已发布的 alpha/beta/rc 标签会固定该源码的目标版本，避免较早周期在晋级期间发布稳定版后改变目标。
标签内嵌的源码 SHA 和提交数必须匹配；目标版本冲突会阻断构建。
预发布标签不会成为新源码的版本基线；来自无关或更新源码的稳定标签会被忽略。

打包前须获取完整历史和全部发布标签：`git fetch --tags`；浅克隆则先执行
`git fetch --unshallow --tags`。脚本拒绝浅历史，CI 中消费版本的 checkout 已使用 `fetch-depth: 0`。
没有适用稳定标签时，使用配置基线初始化首发版本。
`build-info.json` 中的 `baseVersion` 记录计算后的目标版本。
只有文档、工具链等非发布提交的周期保留上个目标版本；不可变发布规则仍会拒绝将已存在的稳定版本用于不同源码。
这类周期需要包含触发升级的提交后，才能发布新的 production 版本。

运行版本与发布策略回归测试：

```sh
python3 -m unittest discover -s tests -p 'test_version.py' -v
python3 -m unittest discover -s tests -p 'test_release_policy.py' -v
```

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
仓库启用 **Allow auto-merge** 后，符合条件的 PR 可选择 **Enable auto-merge**。
授权维护者在 PR 上启用后，GitHub 等待分支要求满足。选择 **Merge commit**，
以保留 alpha 交付要求的两个父提交。交付脚本跟随该合并，并核对实际合并结果和原始 HEAD 的 CI 证据。

alpha 正常前进不会改变已验证的 PR HEAD，也不要求为此新增源码提交。
参考 sandboxpi 的 [源码检查策略](https://github.com/BANG404/sandboxpi/blob/alpha/.agents/skills/git-delivery/references/alpha-pr.md)，
脚本从已核实的 CI 运行下载 `source-validation` 报告。流水线拆分前已验证的 PR
可以使用原有打包报告。脚本核对实际测试的合并候选，要求第二个父提交是当前 PR HEAD，
且源码树与报告相同。
第一个父提交为测试时的 base，可以落后于当前 alpha，但必须仍是当前 alpha 的祖先。
源码验证证据缺失、候选身份不符或 alpha 历史被改写时停止合并。
这证明保存的候选已通过验证，不代表最新 alpha 与 PR 的组合已经测试。

仍须满足审阅要求及分支保护。按源码交付的 alpha 策略采用非严格必需检查
（`strict: false`），保留两项必需检查，分支保护中不绑定 App。
交付客户端仍按上述要求核实实际 GitHub Actions 运行及 job。
此配置取消“每个 PR 必须包含最新 alpha 提交”的要求，仍保留 CI 和冲突检查。
配置调整属于显式仓库策略变更，不是绕过保护。若仍启用严格保护，`behind` PR
会停止并报告策略原因，不将其描述为代码冲突。脚本不会更改保护配置，
也不会仅因 alpha 前进而更新分支或重跑 CI。
检查缺失或待完成时等待；检查失败/取消、真实冲突、来源变化、仓库不符或
PR 未合并便关闭时停止。修复冲突或修改源码后，必须重新检查新的 HEAD。
任务交付不会触发 beta/rc/main 提升或发布。

公共 Git 目录中的锁防止多个 worktree 重复跟随同一 PR。
中断后保留本地提交、远端分支和 PR，按输出的原始身份恢复，不重新提交源码：

```powershell
python scripts/deliver.py follow 123 --head <40-character-source-sha> --merge
```

省略 `--merge` 时在 CI 验证通过后返回。暂时的网络或服务端读取失败会有限重试。
创建 PR 或合并响应不明确时，先核实实际 PR。默认跟随期限一小时，可用
`--timeout` 指定秒数。残留锁须先核实再删除。运行针对性回归测试：
`python -m unittest discover -s tests -p test_delivery.py -v`。

### CI 和 CD 跟进

持续跟进已授权的交付，直到核实最终结果。根据源码 SHA、运行 ID 和尝试编号找到最先失败的前置任务，
在当前任务范围内修复可恢复的故障，再验证新的运行。等待超时后，先核对原运行和已经完成的写入，再恢复跟进。
依赖外部状态时，优先使用事件或有界等待。需要凭据、审阅或新的部署与发布授权时，再交给人工处理。

跟进期间检查任务依赖图和实际耗时，将低成本检查放在昂贵的依赖任务之前，复用可核对来源与校验和的产物，
并行执行相互独立的任务。根据实际门禁验证每项优化，保留必需的证据。

## CI

Alpha PR 在 Linux x64 上验证源码，包括仪表盘类型和构建、格式检查、Clippy、
Rust 单元测试，以及 Python 交付和发布策略测试。可复用的安全工作流扫描依赖和脚本。
晋级 PR 核对阶段顺序，确认源码树已通过 alpha 验证，并执行新的安全扫描。

分支保护要求 **Quality checks** 和 **Package verification**。第二个名称对应 CI 的证据门禁。
它在 alpha 生成 `source-validation`，记录实际测试的合并候选、Git 树、运行 ID 和尝试编号，
并在晋级 PR 上校验复用的证据。前置任务失败、取消或意外跳过时，门禁失败。
五平台打包流程由 CD 执行，生成 `package-verification` 和 `release-distributions`。

PR 自动验证只在创建 PR（`opened`）或源分支提交变化（`synchronize`）时运行。
修改标题或说明、重新打开 PR、将草稿标记为可审阅，都不会启动或取消 CI。
新的源码提交会取消过时运行。可在 `alpha` 分支手动运行 **CI**，刷新该分支当前源码树的证据。
Actions 固定到提交 SHA。

### 测试分层与门禁

协议套件覆盖黑盒、边界、集成及权限隔离检查。CI 和 Assurance 共用安全工作流。
Release 调用可复用的 Assurance，运行安全、覆盖率、变异和性能检查。
任何必需验证失败都将阻止发布。

仪表盘构建依赖暂时固定使用 `@ryotsu/picomatch@4.0.7-a` 的 CVE-2026-112366 回移修复。
上游发布修复版本且审计通过后，移除该覆盖项。

| 层次 | 执行时机 | 门禁 |
| --- | --- | --- |
| 边界与白盒 | Alpha CI 和 Release 跑 Rust 单元测试。定时或手动 Assurance 和 Release 测量覆盖率 | 全部测试通过。覆盖率任务要求 `spec.rs` 和 `matcher.rs` 的 LLVM 行覆盖率分别达到 90% |
| 黑盒与集成 | Release 在五个平台验证 | stdio MCP、子进程清理、后台桥接及打包、安装、升级场景通过 |
| 浏览器 E2E | Release 在 Linux x64 验证 | 对最终包运行真实 MCP 仪表盘场景并通过 |
| 静态分析与依赖安全 | Alpha CI 和 Release 跑 Clippy。每次 PR、Assurance 和 Release 执行依赖及脚本扫描 | Clippy 零警告。Bandit 的 B602/B604/B605/B609 规则通过。Cargo 和 npm 审计满足各自门槛，覆盖 npm 构建依赖 |
| 变异测试 | 每周、手动 Assurance 开启 `deep`、Release 验证 | `validate_run` / `validate_deadline` 的所有可编译变异均被测试捕获；存活变异、超时、基线失败均阻止通过 |
| 性能 | 与变异验证一起在 Linux x64 执行 | 20 次真实后台 worker 完成并向 loopback 宿主交付，各输出 64 KiB、匹配窗口 64 KiB；最近秩法 p95 不超过 3 秒 |

覆盖率门禁逐文件检查原始计数；缺失、重复、无效或未达标证据均失败。
这里测量的是**行覆盖率**，不代表分支覆盖率或整个应用的覆盖率；测试代码单独存放在
覆盖工具排除的 `*_tests.rs` 文件中。当前稳定 Rust
未启用 LLVM 实验性分支插桩。变异范围为进程请求及超时校验器；后续新增关键模块时，
应配套测试再扩大范围。性能预算用于发现 Linux CI runner 上的明显回退，不是用户延迟 SLA。
本项目没有可供 DAST 扫描的已部署 HTTP 服务，改以本地浏览器隔离及协议拒绝场景验证，
不扫描无关网络服务。

Assurance 每日 UTC 02:17（北京时间 10:17）运行安全扫描，每周一 UTC 02:47
（北京时间 10:47）运行安全、覆盖率、变异和性能检查。GitHub 定时任务仅从仓库默认分支执行，
需先通过正常阶段流程将该工作流提升到默认分支。在此之前，可选择 `alpha` 分支手动运行
**Assurance** 或执行本地命令。定时任务独立于发布和晋级，按计划执行相应的检查。

原生任务固定 Node 24，并缓存 npm 下载及 Rust 依赖/编译结果。
Rust 缓存键区分操作系统、架构、任务、工具链、锁文件及源码提交；恢复前缀复用旧依赖，
修改后的源码由 Cargo 重新构建。覆盖率与变异缓存独立。归档、身份元数据和验证报告均重新生成，
缓存不能代替任务成功。安全报告、核心覆盖率及深度检查证据保留 14 天，失败时同样保留诊断。
未配置漏洞豁免；扫描本身报错也会阻止验证通过。

本地运行附加检查，工具版本与工作流一致：

```sh
cargo install cargo-audit --version 0.23.0 --locked
cargo install cargo-llvm-cov --version 0.9.1 --locked
cargo install cargo-mutants --version 27.1.0 --locked
python3 -m pip install bandit==1.9.4
cargo audit
npm audit --audit-level=high
bandit -r scripts -t B602,B604,B605,B609
npm ci && npm run build:ui
rustup component add llvm-tools-preview
mkdir -p artifacts/coverage
cargo llvm-cov --locked --json --summary-only --output-path artifacts/coverage/llvm.json
python3 scripts/check_coverage.py artifacts/coverage/llvm.json
mkdir -p artifacts
cargo mutants --in-place --file src/spec.rs --re 'validate_(run|deadline)' --timeout 60 --build-timeout 180 --output artifacts/mutation -- --locked
cargo build --locked
python3 scripts/verify_performance.py
```

请使用独立任务 checkout：变异命令会临时修改校验器，测试结束后恢复。
Linux 性能探针默认使用 debug 程序，可通过 `TERMINALHOOK_EXE` 指定其他原生程序。

Beta、rc、main 晋级 PR 会检查阶段顺序，并要求合并树等于来源树。
同一源码树必须通过 alpha CI，其源码报告必须仍然有效，同时也必须完成 alpha 发布。
Alpha 的 Release 工作流须在同一次尝试中发布 GitHub Release，并更新五个平台的 marketplace。
`promotion-validation.json` 记录源码验证和成功发布 alpha 的任务。证据过期且对应源码树仍位于 alpha 时，
可在 alpha 手动运行 **CI** 刷新。工作流和测试属于源码树，修改后必须重新验证 alpha。

源码提交排除编译后二进制、运行日志、本地聊天轨迹和凭据；仅 Release 会向独立 marketplace 分支写入二进制。

## 提升源码

变更合并至 alpha 后，在 alpha 分支运行 **Release**，选择 `channel=alpha` 并开启 **publish**。
等待 **Publish verified distributions** 成功，再将已发布的源码晋级到 beta。
在已完成身份验证的 checkout 中依次创建各阶段晋级 PR：

```powershell
./scripts/promote.ps1 -Target beta
./scripts/promote.ps1 -Target rc
./scripts/promote.ps1 -Target main
```

每条命令创建下一个阶段的 PR。CI 通过后审阅并合并。
若晋级检查在 alpha 发布完成前失败，发布成功后重新运行失败的检查。

## CD / 手动发布

GitHub Actions 选择 **Release**，指定工作流分支及匹配渠道（`production` 使用 `main`）。
**publish** 关闭时仅验证，不改变发布版和 marketplace 引用；启用后发布完整原生产物集。
四个阶段共用同一套五目标矩阵。

可复用的发布验证工作流检查分支和 alpha 源码证据。Beta、rc 和 main 上的 Release 运行还要求
同一源码树已完成 alpha 发布。通过这些检查后，工作流共享一次仪表盘构建，随后执行原生测试、
Assurance 和各目标的渠道打包。任务会校验共享仪表盘的源码提交和 HTML 校验和。
打包与原生测试并行运行。

| 目标 | 原生 runner | 二进制 |
| --- | --- | --- |
| windows-x64 | windows-2025 | terminalhook.exe |
| linux-x64 | ubuntu-24.04 | terminalhook |
| linux-arm64 | ubuntu-24.04-arm | terminalhook |
| macos-x64 | macos-15-intel | terminalhook |
| macos-arm64 | macos-15 | terminalhook |

每个包内二进制都在路径含空格的解压目录中测试，覆盖协议、隔离安装、配置保留、Git 迁移及回滚、
启动自动更新、显式升级及缓存中的实际 MCP 启动命令。长等待用例覆盖 65 秒后台任务跨越 MCP
断连和 90 秒 UTF-8 输出。Windows 额外检查 PowerShell UTF-8 后台长任务。ZIP 与 Git 树保留可执行权限。
Linux 包使用 Ubuntu 24.04 的 glibc。macOS 包以未签名、未经公证的形式分发。

汇总任务要求原生测试和 Assurance 通过，再核验源码身份、校验和、原生启动配置、二进制格式和可执行权限。
发布版包含五份 `terminalhook-<platform>.zip`、覆盖五份归档的单个 `SHA256SUMS.txt`，
以及共享 `build-info.json`。写权限任务创建带注释的 `v<version>` 标签。非生产渠道是预发布版。
重跑可补完未完成草稿，但不能替换资源、修改标签指向，或将同一稳定版本用于不同源码。
缺少平台时，在创建标签前阻止写入。

不可变 Release 发布后，`scripts/publish_marketplace.py` 下载并核对全部 ZIP 与本地已验证产物一致，
将每个完整包及校验和清单写入对应 Git 分支，不重新编译或替换二进制。Windows 保留历史引用：

| 渠道 | Windows x64 分支 | Windows 安装选项 |
| --- | --- | --- |
| production | `marketplace` | 默认 / `-Channel production` |
| alpha | `marketplace-alpha` | `-Channel alpha` |
| beta | `marketplace-beta` | `-Channel beta` |
| rc | `marketplace-rc` | `-Channel rc` |

Unix 分支在渠道引用后追加 `-linux-x64`、`-linux-arm64`、`-macos-x64` 或 `-macos-arm64`，
例如 `marketplace-alpha-macos-arm64`。Unix 安装器用 `--channel alpha/beta/rc/production`
自动选择原生引用。所有平台保留 `terminalhook@terminalhook-local`。`source.path` 指向 Git 快照内部目录，
注册来源跟踪可移动的 Git 渠道分支。

首次分发提交不含源码历史父提交；后续从当前分发 HEAD 快进，并发写入会被拒绝。
相同重跑不写新提交，比较包含可执行模式。同版本内容变化、源码历史回退、版本降级和跨渠道变化均被拒绝。
过时文件不带入新树。分支在首次发布时创建，因此平台/渠道发布前不能安装。
仓库规则须允许发布令牌创建并推进四个 Windows 渠道分支及十六个 Unix 变体，不向分发分支提交源码 PR。

marketplace 发布中途失败时保留不可变 Release 和已完成平台引用。重跑同一 Release 请求补齐剩余引用；
相同平台跳过，不能覆盖更新的 HEAD。

启动刷新及安装缓存更新由 Codex 负责，没有自建更新守护进程或生命周期 Hook。
发布验证固定 Codex CLI 0.167.0，在每个目标上通过回环 Git 服务验证本地迁移、错误引用恢复、app-server
启动自动更新、官方升级命令和实际缓存 MCP 命令。使用隔离 Codex 目录，不创建模型轮次；
不能以此证明桌面模型请求数，也不承诺热迁移活跃 MCP 会话。

发布使用 GitHub 工作流令牌，不需要 PAT 或外部 runner。工具直接暴露及桌面行为仍须在实际 App 验证。
默认包不含生命周期 Hook；历史 0.3.1 实现保留在 `legacy/stop-hook`。

## 任务面板验证

Release 的 Linux x64 测试任务使用 Playwright，连接真实 TerminalHook stdio 服务
与确定性宿主夹具，验证内嵌 MCP Apps 资源、任务工具、无需列表读取的单任务隔离、
纯英文控件、回调更新、空闲与可见性切换不轮询、回调不产生循环、紧凑响应式布局、宿主主题、输出转义，
以及无外部请求、无界面模型消息。这些检查纳入发布验证；
桌面宿主原生渲染仍需单独集成验证。源码构建需要带 npm 的 Node.js 20.19+ 或 22.12+；
Playwright 仅用于验证。安装后的 MCP 二进制均不需要。运行 Cargo 前先构建独立的 Svelte/Tailwind 资源：

```sh
npm ci
npm run check:ui
npm run build:ui
cargo build --locked
npx playwright install chromium
npm run test:ui
```

截图和证据写入已忽略的 `artifacts/`，并由发布验证上传。
后台执行与宿主交付回归纳入所有原生平台的 Python 协议测试。
参见[后台任务指南](background-tasks.zh-CN.md)。

界面源码位于 `ui/src/`，`components.json` 配置仓库中的 shadcn-svelte 组件。
`npm run dev:ui` 可本地预览布局（任务数据需要 MCP Apps 宿主）。
`npm run build:ui` 生成已忽略的 `ui/dist/index.html`，脚本、样式、图标均内联。
Cargo 将该文件嵌入二进制，修改界面后应先重新打包，再编译 Rust。
打包脚本会自动执行 `npm ci`、类型检查和界面构建。安装后无需 CDN 或前端运行时。

发布工作流下载共享前端产物后，向 `scripts/package.py` 传入 `--prebuilt-dashboard`。
该选项要求 `ui/dist/index.html` 及其 `build-info.json` 清单与当前源码提交和校验和一致；
缺失或不匹配时，在 Rust 编译前失败。本地打包默认仍安装依赖、检查类型并重新构建前端。
需要显式创建和验证可复用产物时，运行 `python3 scripts/dashboard.py build`
和 `python3 scripts/dashboard.py verify`。

## 本地构建

在目标系统/架构安装 Python 3.11+、固定版本 Rust 工具链，以及带 npm 的 Node.js 20.19+ 或 22.12+ 后执行：

```sh
python3 scripts/package.py --channel development
python3 scripts/verify_package.py
sh scripts/install.sh --source ./dist/package
```

Windows 保留 PowerShell 包装脚本：

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_package.py
./scripts/install.ps1 -Source ./dist/package
```

构建暂存到 `dist/package`，与源码插件目录分离。本地安装目录需保留。
不带 `--source` / `-Source` 时，即使从解压包执行安装器，也选择 Git 稳定渠道。
插件安装需要 Codex CLI，Git 安装还需要 Git。Unix 仅在安装及 Codex 配置编辑阶段需要
Python 3.11+；安装后的 Rust MCP 二进制不依赖 Rust 工具链、Python 或 Node。

## 许可证

源码和发布包均使用 `LICENSE` 中的 MIT 许可证。
Cargo 依赖继续遵循各自作者的许可证。
