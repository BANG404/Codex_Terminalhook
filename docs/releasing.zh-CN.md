# 开发与发布

[English](releasing.md) | 简体中文

TerminalHook 使用 `alpha -> beta -> rc -> main` 阶段流程。
功能 PR 以 `alpha` 为目标。通过同一仓库内的 PR 提升已测试的阶段；
CI 会拒绝跳过阶段以及来自 fork 的提升。仓库初始快照以相同提交初始化四个分支，后续变更均通过 PR 进行。

| 来源分支 | 产物版本 | GitHub Release |
| --- | --- | --- |
| alpha | `0.5.0-alpha.<commit-count>.g<12-char-sha>` | 预发布 |
| beta | `0.5.0-beta.<commit-count>.g<12-char-sha>` | 预发布 |
| rc | `0.5.0-rc.<commit-count>.g<12-char-sha>` | 预发布 |
| main | `0.5.0` | 稳定版 |
| 功能分支 / PR | `0.5.0-development.<commit-count>.g<12-char-sha>` | 不可发布 |

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

Alpha PR 在五个原生目标执行格式检查、Clippy、Rust 单元测试、Python 协议与发布策略测试，
同时执行原生打包和安装验收：

| 目标 | 原生 runner | 二进制 |
| --- | --- | --- |
| windows-x64 | windows-2025 | terminalhook.exe |
| linux-x64 | ubuntu-24.04 | terminalhook |
| linux-arm64 | ubuntu-24.04-arm | terminalhook |
| macos-x64 | macos-15-intel | terminalhook |
| macos-arm64 | macos-15 | terminalhook |

一个 Linux 任务安装锁定的前端依赖、检查类型并构建仪表盘。
原生测试、打包和 assurance 任务复用同一次运行的 `dashboard-bundle` 产物，
嵌入前核验源码提交和 HTML 校验和。前端产物就绪后，原生测试与打包同时开始。
`Quality checks` 仍要求全部测试和 assurance 检查通过；`Package verification`
等待质量检查与全部原生包完成后才汇总分发产物。即使打包成功，测试失败或取消也不能
授权交付或发布。并行构建缩短等待时间，但测试失败时可能产生额外构建开销。

独立运行 Assurance 时自行构建仪表盘。仅做阶段提升的 PR 跳过前端和原生构建，
仍要求源码证据与安全检查通过。

每个二进制均从路径含空格的解压目录运行测试，验证隔离安装、配置保留、Git 迁移和失败恢复、
启动自动更新、显式升级及缓存中的实际 MCP 启动命令。ZIP 与 Git 树保留可执行权限。
Linux 使用基于 Ubuntu 24.04 构建的 glibc；Alpine/musl 和 Windows ARM64 不在发布矩阵内。
macOS 二进制未签名，也未公证。

分支必需检查保持为 **Quality checks** 和 **Package verification**。两者汇总所有原生结果，
任一任务失败、取消或意外跳过均不能通过。只有完整的五平台归档和安装证据才能生成
`release-distributions` 及汇总 `package-verification` 报告。变更使用 PR，push 不重复验证，
PR 更新后取消过时运行。Actions 固定到提交 SHA。

### 测试分层与门禁

测试类型存在重叠：协议套件同时提供黑盒、边界、集成及权限/隔离检查，
不会将同一套件复制为多个测试任务。可复用的 `assurance.yml` 工作流计入
**Quality checks**；失败或取消将阻止最终产物汇总与交付。阶段提升 PR 重新执行安全扫描，
原生测试则复用与当前源码树完全一致的 alpha 证据。

| 层次 | 执行时机 | 门禁 |
| --- | --- | --- |
| 边界与白盒 | 每次 alpha PR：Rust 单元测试，覆盖后台输入上下界及输出流选择 | 全部测试通过；`spec.rs`、`matcher.rs` 各自 LLVM 行覆盖率至少 90% |
| 黑盒与集成 | 每次 alpha PR：五平台 stdio MCP、子进程清理、后台桥接、打包/安装/升级测试 | 核心协议及安装场景全部通过 |
| 浏览器 E2E | 每次 alpha PR 的 Linux x64；Release 发布前再次测试最终包内程序 | 真实 MCP 仪表盘场景通过 |
| 静态分析与依赖安全 | 每次 PR、Release 验证及每日定时扫描 | Clippy 零警告；Bandit 的 B602/B604/B605/B609 Shell 注入规则通过；Cargo audit 无已知漏洞；npm audit 无高危/严重问题，包含开发依赖 |
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
**Assurance** 或执行本地命令；alpha PR 和 Release 门禁已直接调用可复用工作流。
定时运行不会发布或提升源码。

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

Beta、rc、main 提升 PR 使用轻量 Linux 任务，强制阶段顺序、要求合并树等于来源树，
并查找验证过完全相同源码树的成功 alpha CI。每个平台的产物、校验和及源码身份必须存在且未过期；
证据缺失或不匹配都会阻止提升。提升期间不编译项目 Rust 源码、不重跑协议、不重新标记二进制。
`promotion-validation.json` 保留原验证证据。工作流和测试属于源码树，修改后必须重新验证 alpha。

源码提交排除编译后二进制、运行日志、本地聊天轨迹和凭据；仅 Release 会向独立 marketplace 分支写入二进制。

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

GitHub Actions 选择 **Release**，指定工作流分支及匹配渠道（`production` 使用 `main`）。
**publish** 关闭时仅验证，不改变发布版和 marketplace 引用；启用后发布完整原生产物集。
四个阶段共用同一套五目标矩阵。

工作流检查分支、复用完整 alpha 源码验证，为每个目标构建一次最终渠道版本。每个包内二进制均通过
协议、安装、Git 更新、65 秒后台任务跨越 MCP 断连及 90 秒 UTF-8 输出回归。Windows 额外执行
PowerShell UTF-8 后台长任务回归。阶段提升不会将 alpha 二进制重新标记为稳定版。

发布前汇总核验全部源码身份、校验和、原生启动配置、二进制格式和可执行权限。
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
CI 固定 Codex CLI 0.167.0，在每个目标上通过回环 Git 服务验证本地迁移、错误引用恢复、app-server
启动自动更新、官方升级命令和实际缓存 MCP 命令。使用隔离 Codex 目录，不创建模型轮次；
不能以此证明桌面模型请求数，也不承诺热迁移活跃 MCP 会话。

发布使用 GitHub 工作流令牌，不需要 PAT 或外部 runner。工具直接暴露及桌面行为仍须在实际 App 验证。
默认包不含生命周期 Hook；历史 0.3.1 实现保留在 `legacy/stop-hook`。

## 任务面板验证

Alpha 的 Linux x64 测试任务还会使用 Playwright，连接真实 TerminalHook stdio 服务
与确定性宿主夹具，验证内嵌 MCP Apps 资源、任务工具、无需列表读取的单任务隔离、
纯英文控件、回调更新、空闲与可见性切换不轮询、回调不产生循环、紧凑响应式布局、宿主主题、输出转义，
以及无外部请求、无界面模型消息。这些检查纳入 `Quality checks`；
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

截图和证据写入已忽略的 `artifacts/`，并由 CI 上传。
后台执行与宿主交付回归纳入所有原生平台的 Python 协议测试。
参见[后台任务指南](background-tasks.zh-CN.md)。

界面源码位于 `ui/src/`，`components.json` 配置仓库中的 shadcn-svelte 组件。
`npm run dev:ui` 可本地预览布局（任务数据需要 MCP Apps 宿主）。
`npm run build:ui` 生成已忽略的 `ui/dist/index.html`，脚本、样式、图标均内联。
Cargo 将该文件嵌入二进制，修改界面后应先重新打包，再编译 Rust。
打包脚本会自动执行 `npm ci`、类型检查和界面构建。安装后无需 CDN 或前端运行时。

CI 下载共享前端产物后，显式向 `scripts/package.py` 传入 `--prebuilt-dashboard`。
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
