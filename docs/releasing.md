# Development and releases

English | [简体中文](releasing.zh-CN.md)

TerminalHook uses the stage progression `alpha -> beta -> rc -> main`.
Feature PRs target `alpha`. Promote a tested stage through a same-repository PR;
CI rejects skipped stages and promotions from forks. The initial repository
snapshot seeds all four branches with the same commit. Subsequent changes use PRs.

| Source branch | Artifact version | GitHub Release |
| --- | --- | --- |
| alpha | `0.5.0-alpha.<commit-count>.g<12-char-sha>` | Prerelease |
| beta | `0.5.0-beta.<commit-count>.g<12-char-sha>` | Prerelease |
| rc | `0.5.0-rc.<commit-count>.g<12-char-sha>` | Prerelease |
| main | `0.5.0` | Stable |
| Feature branch / PR | `0.5.0-development.<commit-count>.g<12-char-sha>` | Not publishable |

`Cargo.toml`, `Cargo.lock` and the source plugin manifest share a stable version
baseline. Change them together for the next stable release. Channel suffixes are
derived during packaging without editing tracked files. The binary's `--version`,
MCP server identity, packaged manifest and `build-info.json` agree. CI rejects a
dirty checkout, a mismatched source SHA or a mismatched channel/branch.

## Task completion and automatic alpha delivery

After implementation and the relevant local checks, agents commit only their
task's changes on a dedicated `codex/` branch and run:

```powershell
python scripts/deliver.py submit --body-file D:/path/to/pr-body.md
```

Keep the PR body outside tracked files (for example under ignored `artifacts/`).
The helper requires a clean worktree, an English Conventional Commit subject,
matching origin fetch/push repository URLs, and a source that merges cleanly
with alpha. It pushes without force, creates or reuses the exact same-repository
alpha PR, then follows its actual CI and automatically merges it. Local-only
work or a different destination must be requested explicitly. See [AGENTS.md](../AGENTS.md).

This follows sandboxpi's [alpha delivery model](https://github.com/BANG404/sandboxpi/blob/alpha/scripts/delivery/git-workflow.mjs):
the delivery client observes real checks before merging. The CI workflow retains
read-only permissions; no privileged PR job executes submitted code to merge it.
Both `Quality checks` and `Package verification` must be successful checks from
GitHub Actions App 15368, for the expected source HEAD, from the same latest
`ci.yml` PR run and attempt. The helper verifies the corresponding real jobs,
rechecks PR identity and gates before a SHA-bound `merge_method=merge` request,
then proves the resulting commit has two parents with that source as its second.
Native GitHub Auto-merge is not used and must not already be active on that PR.

Required reviews and branch protections still apply. Missing or pending checks
wait; failed/cancelled checks, conflicts, changed source, wrong repositories,
and closed unmerged PRs stop delivery. Strict protection may require updating a
branch after alpha advances; resolve that in the task and verify the new source.
No protection is weakened, no source branch is automatically rewritten, and no
beta/rc/main promotion or release is triggered by task delivery.

One lock in the common Git directory prevents multiple waiters across worktrees.
An interrupted operation retains its local commit, remote branch, and PR. Resume
the printed identity instead of resubmitting source:

```powershell
python scripts/deliver.py follow 123 --head <40-character-source-sha> --merge
```

Omit `--merge` for observation only. Reads have bounded retries for transient
network/server errors. Writes are never blindly retried; an ambiguous creation
or merge response is reconciled against the actual PR. The default follow-up
deadline is one hour, adjustable with `--timeout` in seconds. A stale lock must
be inspected before removal. Run the focused regressions with
`python -m unittest discover -s tests -p test_delivery.py -v`.

## CI

Alpha PRs run formatting, Clippy, Rust unit tests, and Python protocol/release-policy
tests on five native targets, followed by native package and installation checks:

| Target | Native runner | Binary |
| --- | --- | --- |
| windows-x64 | windows-2025 | terminalhook.exe |
| linux-x64 | ubuntu-24.04 | terminalhook |
| linux-arm64 | ubuntu-24.04-arm | terminalhook |
| macos-x64 | macos-15-intel | terminalhook |
| macos-arm64 | macos-15 | terminalhook |

Each binary is tested from an extracted directory with spaces in its path. Each
target verifies isolated installation, configuration preservation, Git migration
and rollback, startup updates, explicit upgrades, and the actual cached MCP launch
command. ZIPs and Git trees retain executable permissions. Linux uses glibc built
on Ubuntu 24.04; Alpine/musl and Windows ARM64 are outside the release matrix.
macOS binaries are unsigned and not notarized.

Required branch checks remain **Quality checks** and **Package verification**.
They aggregate all native results and reject failed, cancelled, or unexpectedly
skipped work. Only a complete five-platform archive and installation evidence set
can produce `release-distributions` and the aggregate `package-verification` report.
Changes use PRs; pushes do not duplicate validation, and updated PRs cancel obsolete
runs. Actions are pinned to commit SHAs.

### Test layers and gates

Test types overlap: the protocol suite supplies black-box, boundary, integration,
and permission/isolation checks; they are not separate copies of the same suite.
The reusable `assurance.yml` workflow contributes to **Quality checks**. Its
failure or cancellation blocks packaging and delivery. Promotion PRs rerun fresh
security scans while reusing the exact alpha tree's native test evidence.

| Layer | Execution | Gate |
| --- | --- | --- |
| Boundary and white-box | Every alpha PR: Rust unit tests, including inclusive background input limits and stream selection | All tests pass; `spec.rs` and `matcher.rs` each have at least 90% LLVM line coverage |
| Black-box and integration | Every alpha PR: stdio MCP, child cleanup, background bridge, package/install/update tests on five platforms | All core protocol and installation scenarios pass |
| Browser E2E | Every alpha PR on Linux x64; again against the final packaged executable before Release publication | Real MCP dashboard scenarios pass |
| Static analysis and dependencies | Every PR and Release verification; daily scheduled scan | Clippy warnings fail; Bandit shell-injection rules B602/B604/B605/B609 pass; Cargo audit has no known vulnerabilities; npm audit has no high/critical issues, including development dependencies |
| Mutation | Weekly, manual Assurance with `deep`, and Release verification | Every viable mutation of `validate_run` / `validate_deadline` is caught; surviving mutations, timeouts and baseline failures fail the job |
| Performance | With mutation verification on Linux x64 | 20 real background worker completions with loopback host delivery, 64 KiB output and a 64 KiB matching window; nearest-rank p95 at most 3 seconds |

The coverage gate checks each required file's raw counters and fails on missing,
duplicate, invalid, or below-threshold evidence. It measures **line coverage**,
not branch coverage or whole-application coverage. Test code lives in separate
`*_tests.rs` files excluded by the coverage tool. Stable Rust does not enable
LLVM's experimental branch instrumentation here. Mutation scope is deliberately
the process request and timeout validators; expand it with tests when adding
other critical modules. The performance budget detects gross regressions on the
Linux CI runner and is not an end-user latency SLA. There is no deployed HTTP
service to target with DAST: browser isolation and protocol rejection scenarios
are exercised locally instead of scanning unrelated network services.

Assurance runs daily at 02:17 UTC for security and Mondays at 02:47 UTC for
security, coverage, mutation and performance. GitHub schedules execute only from
the repository's default branch, once that branch contains this workflow through
the normal stage progression. Until then, use the **Assurance** manual dispatch on
`alpha` (or run the commands locally); alpha PR and Release gates already call
the reusable workflow directly. Scheduled runs never publish or promote source.

Native jobs pin Node 24 and cache npm downloads and Rust dependencies/builds.
Rust cache keys separate OS, architecture, job, toolchain, lockfile and revision;
restore prefixes reuse older dependencies while Cargo rebuilds changed sources.
Coverage and mutation caches are separate. Archives, identities and verification
reports are freshly generated; cached output never substitutes for a passing job.
Security reports, core coverage and deep-test evidence are retained for 14 days,
including failure diagnostics. No vulnerability exemptions are configured; scan
errors also fail verification.

Run the extra checks locally with the pinned versions used by the workflow:

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

Run in an isolated task checkout: the mutation command temporarily edits the
validator and restores it after testing. The Linux performance probe uses the
debug binary by default; `TERMINALHOOK_EXE` selects another native executable.

Promotion PRs on beta, rc, and main use lightweight Linux jobs to enforce stage
order, require the merge tree to equal the source tree, and locate a successful
alpha CI run with that exact tested tree. Every platform's artifact, checksum,
and source identity must be available and unexpired. Missing or mismatched evidence
blocks promotion. No project Rust compilation, protocol rerun, or binary relabeling occurs
during promotion. `promotion-validation.json` retains the original evidence.
Workflow and test changes are part of the tree and require fresh alpha validation.

Compiled binaries, runtime logs, local chat traces, and credentials are excluded
from source commits. Only Release writes binaries to dedicated marketplace branches.

## Promote source

From an authenticated checkout:

```powershell
./scripts/promote.ps1 -Target beta
./scripts/promote.ps1 -Target rc
./scripts/promote.ps1 -Target main
```

Each command opens only the next stage's PR. Review and merge it after CI passes.
The commands do not publish artifacts.

## CD / manual publication

In GitHub Actions, select **Release**, choose the workflow branch and matching
channel (`production` uses `main`). With **publish** disabled the workflow validates
without changing release or marketplace refs. Enable it to publish the complete
native platform set. All four stages use the same five-target matrix.

The workflow checks the branch, reuses complete alpha source validation, and
builds the final channel version once per target. Every packaged binary passes
protocol, installation, Git update, a 65-second background task across MCP disconnection, and 90-second
UTF-8 output regressions. Windows additionally runs the PowerShell UTF-8
long-wait regression. Stage promotion does not relabel alpha binaries.

The aggregate validates all source identities, checksums, native launch configs,
binary formats, and executable permissions before publication. A release contains
five `terminalhook-<platform>.zip` assets, one `SHA256SUMS.txt` covering all five,
and the shared `build-info.json`. A write-scoped job creates an annotated
`v<version>` tag. Non-production releases are prereleases. A retry may complete an
unfinished draft but cannot replace assets, retarget tags, or reuse a stable
version for different source. Missing platforms block writes before tag creation.

After the immutable release is published, `scripts/publish_marketplace.py`
downloads and verifies every released ZIP against its validated local artifact.
It copies each complete package and the checksum manifest into its own Git branch,
without compilation or binary substitution. Windows retains its historical refs:

| Channel | Windows x64 branch | Windows installer option |
| --- | --- | --- |
| production | `marketplace` | default / `-Channel production` |
| alpha | `marketplace-alpha` | `-Channel alpha` |
| beta | `marketplace-beta` | `-Channel beta` |
| rc | `marketplace-rc` | `-Channel rc` |

Unix branches append `-linux-x64`, `-linux-arm64`, `-macos-x64`, or `-macos-arm64`
to the channel ref, for example `marketplace-alpha-macos-arm64`. Unix installers
automatically choose the native ref with `--channel alpha/beta/rc/production`.
All platforms preserve `terminalhook@terminalhook-local`. `source.path` is local
within the Git snapshot; registration tracks a moving Git channel branch.

First distribution commits have no source-history parent. Later updates are
fast-forwards from the current distribution head; concurrent writes are rejected.
Identical retries are no-ops, including executable modes. Changed existing versions,
source-history rollbacks, version downgrades, and cross-channel changes are rejected.
Obsolete files are removed from new trees. Branches are created on first publication,
so a platform/channel cannot be installed beforehand. Repository rules must permit
the release token to create and advance the four Windows channel branches and
their sixteen Unix variants. Do not submit source PRs to distribution branches.

If marketplace publication fails partway through, the immutable release and
completed platform refs remain. Retry the same Release request to finish missing
refs; existing identical platforms are skipped, and newer heads are never overwritten.

Codex owns startup refresh and installed-cache updates; there is no updater daemon
or lifecycle hook. CI pins Codex CLI 0.167.0 and verifies local migration, failed-ref
restoration, automatic app-server startup updates, the official upgrade command,
and the actual cached MCP command against a loopback Git server on every target.
Tests use isolated Codex homes and create no model turns. They do not establish
desktop model request counts or promise live migration of active MCP sessions.

Publication uses GitHub's workflow token without a PAT or external runner. Direct
tool exposure and desktop behavior must still be verified in the actual app.
The default package has no lifecycle hooks; the historical 0.3.1 implementation
is preserved on `legacy/stop-hook`.

## Dashboard verification

The Linux x64 alpha test job also runs the embedded MCP Apps dashboard in
Playwright against the real TerminalHook stdio server and a deterministic host
fixture. It verifies the resource, task tools, English-only controls, callback
updates without idle/visibility polling or callback feedback loops, filtering,
compact responsive layout, host themes, escaped output, and absence of external requests or UI model messages. These checks
contribute to `Quality checks`; native desktop rendering remains a separate
host integration check. Node.js 20.19+ or 22.12+ and npm are needed for source
builds; Playwright is used only for verification. The installed MCP binary needs
neither. Before Cargo commands, build the self-contained Svelte/Tailwind bundle:

```sh
npm ci
npm run check:ui
npm run build:ui
cargo build --locked
npx playwright install chromium
npm run test:ui
```

Screenshots and evidence are written under ignored `artifacts/` and uploaded by
CI. Background execution/host-delivery regressions run in the ordinary Python
protocol suite on every native platform. See [background tasks](background-tasks.md).

Edit `ui/src/`; `components.json` configures the checked-in shadcn-svelte
components. `npm run dev:ui` previews layout locally (task data requires an MCP
Apps host). `npm run build:ui` writes ignored `ui/dist/index.html`, with all
scripts, styles, and icons inline. Cargo embeds this file; rebuild it after UI
changes and before compiling Rust. Packaging runs `npm ci`, type checks, and
bundling automatically. No CDN or frontend runtime is needed after installation.

## Local build

Build on the target OS/architecture with Python 3.11+, the pinned Rust toolchain,
and Node.js 20.19+ or 22.12+ with npm:

```sh
python3 scripts/package.py --channel development
python3 scripts/verify_package.py
sh scripts/install.sh --source ./dist/package
```

Windows retains the PowerShell wrappers:

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_package.py
./scripts/install.ps1 -Source ./dist/package
```

Builds stage under `dist/package`, separate from the source plugin. Keep local
installation directories available. Without `--source` / `-Source`, even an
extracted package installer selects Git stable. Plugin installation requires
Codex CLI; Git installation also requires Git. Unix installation uses Python
3.11+ only for installation and Codex configuration editing. The installed Rust
MCP binary has no Rust toolchain, Python, or Node dependency.

## License

Source and release packages use the MIT license in `LICENSE`. Cargo dependency
licenses remain those of their respective authors.
