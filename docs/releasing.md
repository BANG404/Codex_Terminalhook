# Development and releases

English | [简体中文](releasing.zh-CN.md)

TerminalHook uses the stage progression `alpha -> beta -> rc -> main`.
Feature PRs target `alpha`. Promote a tested stage through a same-repository PR;
CI rejects skipped stages and promotions from forks. The initial repository
snapshot seeds all four branches with the same commit. Subsequent changes use PRs.

| Source branch | Artifact version | GitHub Release |
| --- | --- | --- |
| alpha | `<target>-alpha.<commit-count>.g<12-char-sha>` | Prerelease |
| beta | `<target>-beta.<commit-count>.g<12-char-sha>` | Prerelease |
| rc | `<target>-rc.<commit-count>.g<12-char-sha>` | Prerelease |
| main | `<target>` | Stable |
| Feature branch / PR | `<target>-development.<commit-count>.g<12-char-sha>` | Not publishable |

`Cargo.toml`, `Cargo.lock` and the source plugin manifest share a stable version
bootstrap/minimum baseline. Ordinary releases do not require editing those files;
if the minimum is explicitly changed, update all three together. The target and
channel suffixes are derived during packaging without editing tracked files. The binary's `--version`,
MCP server identity, packaged manifest and `build-info.json` agree. Release rejects a
dirty checkout, a mismatched source SHA or a mismatched channel/branch.

## Automatic target version

`scripts/version.py` reads the highest applicable stable `vMAJOR.MINOR.PATCH`
Git tag and the complete, non-merge commit messages since its retained source.
It applies the largest [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
change once per release cycle:

| Unreleased commit | Target change |
| --- | --- |
| `fix:` or `perf:` (optional scope) | Increment patch |
| `feat:` (optional scope) | Increment minor and reset patch |
| Any type with `!`, `BREAKING CHANGE:`, or `BREAKING-CHANGE:` | Before 1.0, increment minor and reset patch; from 1.0, increment major and reset minor/patch |
| Other types or non-conventional messages | No version increment |

Types are case-insensitive; breaking footers must use the uppercase spelling.
For example, after `v0.5.0`, fixes target `0.5.1`; a feature or breaking change
targets `0.6.0`. Multiple fixes do not increment the patch multiple times.
The pre-1.0 breaking-change rule is this project's policy, consistent with
[SemVer's initial-development stage](https://semver.org/spec/v2.0.0.html#spec-item-4).

Tree-preserving delivery and promotion merges are unwrapped to their retained
source, so a stable tag on main applies even if main was never merged back into
alpha. Alpha, beta and rc share the target for the same source, and production
removes the prerelease suffix. Promotion merge messages do not trigger another
bump. New alpha commits cannot change an older beta/rc candidate. A stable tag
for the same retained source preserves the target and build identity on retries.
A published alpha/beta/rc tag pins that source's target even if an older cycle
goes stable during promotion. Its embedded source SHA and commit count must match;
conflicting targets fail closed. Prerelease tags never become the baseline for
new source; stable tags from unrelated or newer source are ignored.

Fetch full history and all release tags before packaging (`git fetch --tags`,
and `git fetch --unshallow --tags` for a shallow checkout). Shallow histories
are rejected; CI's version-consuming checkouts already use `fetch-depth: 0`.
Without an applicable stable tag the configured baseline bootstraps the first
release. `baseVersion` in `build-info.json` records the resolved target. A cycle
containing only documentation, tooling or other non-release commits keeps the
previous target; immutable publication still refuses to reuse an existing stable
version for a different source. Such a cycle needs a release-triggering commit
before a new production release.

Run the version and release-policy regressions with:

```sh
python3 -m unittest discover -s tests -p 'test_version.py' -v
python3 -m unittest discover -s tests -p 'test_release_policy.py' -v
```

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
GitHub's repository setting **Allow auto-merge** makes **Enable auto-merge**
available on eligible PRs. If an authorized maintainer enables it on a PR, GitHub
waits for configured branch requirements. Select **Merge commit** for the
two-parent history required by alpha delivery. The helper observes that request
and verifies the completed merge and CI evidence for the original HEAD.

Alpha advancement alone does not change the verified PR HEAD or require another
source commit. Following sandboxpi's [source-gate policy](https://github.com/BANG404/sandboxpi/blob/alpha/.agents/skills/git-delivery/references/alpha-pr.md),
the helper downloads `source-validation` from the verified CI run. It can also
read the earlier package report for PRs validated before the workflow split.
The recorded merge candidate must have the PR HEAD as its second parent
and the reported source tree. Its first parent is the tested base: that base may
be older than current alpha, but must remain its ancestor. Missing source evidence,
a mismatched candidate, or an alpha history rewrite blocks merge. This verifies
the saved candidate; it does not claim the latest combined alpha tree was tested.

Required reviews and branch protections still apply. Source-based alpha delivery
uses non-strict required status checks (`strict: false`), retaining both required
checks without an App binding in branch protection. The delivery client still
verifies the actual GitHub Actions run and jobs described above. This removes the requirement
that every PR include the latest alpha commit; it does not remove CI or conflict
checks. Configuration is an explicit repository policy change, not a merge bypass.
If strict protection is still enabled, a `behind` PR stops with a policy diagnostic
rather than being described as conflicted. The helper never changes protection
settings, updates the branch, or reruns CI merely because alpha advances.
Missing/pending checks wait; failed/cancelled checks, real conflicts, changed
source, wrong repositories, and closed unmerged PRs stop delivery. Repairing
conflicts or changing the source requires fresh HEAD checks. No beta/rc/main
promotion or release is triggered by task delivery.

One lock in the common Git directory prevents multiple waiters across worktrees.
An interrupted operation retains its local commit, remote branch, and PR. Resume
the printed identity instead of resubmitting source:

```powershell
python scripts/deliver.py follow 123 --head <40-character-source-sha> --merge
```

Omit `--merge` to return after successful CI verification. Reads have bounded retries for transient
network/server errors. Writes are never blindly retried; an ambiguous creation
or merge response is reconciled against the actual PR. The default follow-up
deadline is one hour, adjustable with `--timeout` in seconds. A stale lock must
be inspected before removal. Run the focused regressions with
`python -m unittest discover -s tests -p test_delivery.py -v`.

### CI and CD follow-up

Keep following the authorized delivery through its verified outcome. Use the
source SHA, run ID, and attempt to locate the earliest failed prerequisite.
Fix recoverable failures within the same task and verify the new attempt.
After a timeout, reconcile the original run and any completed writes before
resuming. Await an external prerequisite with an event or a bounded wait
when possible. Escalate when recovery requires credentials, review, or
authorization for a new deployment or release.

Review the run graph and job timings while following CI or CD. Put inexpensive
checks before costly dependent work, share artifacts when their source and
checksums can be verified, and run independent jobs in parallel. Verify any
optimization against the actual gates and retain the required evidence.

## CI

Alpha PRs run source checks on Linux x64, including dashboard types and build,
formatting, Clippy, Rust unit tests, and Python delivery and release policy
tests. The reusable security workflow runs dependency and script checks.
Promotion PRs check stage order, prove that the source tree passed alpha
validation, and run fresh security checks.

Branch protection requires **Quality checks** and **Package verification**.
The second name is the required status name for CI's evidence gate. It records
the tested merge candidate, Git tree, run ID and attempt in `source-validation`
on alpha and checks the reuse of that evidence on promotion PRs. Failed,
cancelled, or unexpectedly skipped prerequisites fail the gates. CD alone runs
the five-target packaging workflow and produces `package-verification` and
`release-distributions`.

Automatic PR validation runs only when a PR is opened (`opened`) or its source
commits change (`synchronize`). Editing a title or description, reopening a PR,
or marking it ready for review does not start or cancel CI. New source commits
cancel obsolete runs. Use the **CI** manual dispatch on the `alpha` branch to
refresh evidence for that branch's current tree. Actions are pinned to commit SHAs.

### Test layers and gates

The protocol suite supplies black-box, boundary, integration, and
permission/isolation checks. CI and Assurance share the security workflow.
Release calls the reusable Assurance workflow for security, coverage, mutation,
and performance checks. Failures in required verification block publication.

The dashboard's build dependencies temporarily use the pinned
`@ryotsu/picomatch@4.0.7-a` backport for CVE-2026-112366. Remove the override
when the upstream package publishes a fixed release that passes the audit.

| Layer | Execution | Gate |
| --- | --- | --- |
| Boundary and white-box | Rust unit tests in alpha CI and Release; coverage in scheduled or manual Assurance and Release | All tests pass; the coverage job requires 90% LLVM line coverage in each of `spec.rs` and `matcher.rs` |
| Black-box and integration | Release verification on five platforms | Stdio MCP, child cleanup, background bridge, and package/install/update scenarios pass |
| Browser E2E | Release verification on Linux x64 | Real MCP dashboard scenarios pass against the final package |
| Static analysis and dependencies | Clippy in alpha CI and Release; dependency and script scans in every PR, Assurance and Release | Clippy warnings fail. Bandit rules B602/B604/B605/B609 pass. Cargo and npm audits meet their thresholds, including npm build dependencies |
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
`alpha` or run the commands locally. Scheduled runs run the selected Assurance
jobs independently of publication and promotion.

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

Promotion PRs on beta, rc, and main enforce stage order and require the merge tree
to equal the source tree. For that exact tree, CI requires a successful alpha CI
run with an unexpired source report and a completed alpha publication. The alpha
Release workflow must have published the GitHub Release and advanced all five
platform marketplaces in the same attempt. `promotion-validation.json` records
the source validation and the successful alpha publication job.
If that evidence expires while the tree is still on alpha, run **CI** manually
on alpha to refresh it. Workflow and test changes are part of the tree and
require fresh alpha validation.

Compiled binaries, runtime logs, local chat traces, and credentials are excluded
from source commits. Only Release writes binaries to dedicated marketplace branches.

## Promote source

After merging the changes into alpha, run **Release** on the alpha branch with
`channel=alpha` and **publish** enabled. Wait for **Publish verified
distributions** to succeed before promoting the published source to beta. Then
open the successive promotion PRs from an authenticated checkout:

```powershell
./scripts/promote.ps1 -Target beta
./scripts/promote.ps1 -Target rc
./scripts/promote.ps1 -Target main
```

Each command opens the next stage's PR. Review and merge it after CI passes.
If a promotion check ran before alpha publication succeeded, rerun the failed
checks after publication completes.

## CD / manual publication

In GitHub Actions, select **Release**, choose the workflow branch and matching
channel (`production` uses `main`). With **publish** disabled the workflow validates
without changing release or marketplace refs. Enable it to publish the complete
native platform set. All four stages use the same five-target matrix.

The reusable release verification workflow checks the branch and alpha source
evidence. Release runs on beta, rc, and main also require completed alpha
publication for the same source tree. After these checks it builds the dashboard
once and runs native tests, Assurance, and channel packaging for the targets
below. Jobs verify the shared dashboard's source revision and HTML checksum.
Packaging and native tests run in parallel.

| Target | Native runner | Binary |
| --- | --- | --- |
| windows-x64 | windows-2025 | terminalhook.exe |
| linux-x64 | ubuntu-24.04 | terminalhook |
| linux-arm64 | ubuntu-24.04-arm | terminalhook |
| macos-x64 | macos-15-intel | terminalhook |
| macos-arm64 | macos-15 | terminalhook |

Every packaged binary is tested from an extracted path with spaces. The tests
verify protocol, isolated installation, configuration preservation, Git migration
and rollback, startup updates, explicit upgrades, and the actual cached MCP
launch command. Long-wait cases cover a 65-second background task across an MCP
disconnection, 90-second UTF-8 output, and an additional PowerShell test on
Windows. ZIPs and Git trees retain executable permissions. Linux packages use
Ubuntu 24.04 glibc. macOS packages are unsigned and are distributed without notarization.

The aggregate requires the native test and Assurance results, then validates
all source identities, checksums, native launch configs,
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
or lifecycle hook. Release verification pins Codex CLI 0.167.0 and tests local migration, failed-ref
restoration, automatic app-server startup updates, the official upgrade command,
and the actual cached MCP command against a loopback Git server on every target.
Tests use isolated Codex homes and create no model turns. They do not establish
desktop model request counts or promise live migration of active MCP sessions.

Publication uses GitHub's workflow token without a PAT or external runner. Direct
tool exposure and desktop behavior must still be verified in the actual app.
The default package has no lifecycle hooks; the historical 0.3.1 implementation
is preserved on `legacy/stop-hook`.

## Dashboard verification

The Linux x64 Release job runs the embedded MCP Apps dashboard in
Playwright against the real TerminalHook stdio server and a deterministic host
fixture. It verifies the resource, task tools, single-task isolation without list
reads, English-only controls, callback updates without idle/visibility polling or
callback feedback loops, compact responsive layout, host themes, escaped output,
and absence of external requests or UI model messages. These checks
contribute to release verification. Native desktop rendering remains a separate
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
release verification. The Python protocol suite runs background execution and
host delivery regressions against the packaged binary on every native platform.
See [background tasks](background-tasks.md).

Edit `ui/src/`; `components.json` configures the checked-in shadcn-svelte
components. `npm run dev:ui` previews layout locally (task data requires an MCP
Apps host). `npm run build:ui` writes ignored `ui/dist/index.html`, with all
scripts, styles, and icons inline. Cargo embeds this file; rebuild it after UI
changes and before compiling Rust. Packaging runs `npm ci`, type checks, and
bundling automatically. No CDN or frontend runtime is needed after installation.

The release workflow passes `--prebuilt-dashboard` to `scripts/package.py` after downloading
the shared bundle. This option requires `ui/dist/index.html` and its
`build-info.json` manifest to match the current source revision and checksum;
missing or mismatched artifacts fail before Rust compilation. Local packaging
continues to install, check, and build the frontend by default. To create and
verify a reusable bundle explicitly, run `python3 scripts/dashboard.py build`
and `python3 scripts/dashboard.py verify`.

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
