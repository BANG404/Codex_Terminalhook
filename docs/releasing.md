# Development and releases

English | [简体中文](releasing.zh-CN.md)

TerminalHook uses the stage progression `alpha -> beta -> rc -> main`.
Feature PRs target `alpha`. Promote a tested stage through a same-repository PR;
CI rejects skipped stages and promotions from forks. The initial repository
snapshot seeds all four branches with the same commit. Subsequent changes use PRs.

| Source branch | Artifact version | GitHub Release |
| --- | --- | --- |
| alpha | `0.4.0-alpha.<commit-count>.g<12-char-sha>` | Prerelease |
| beta | `0.4.0-beta.<commit-count>.g<12-char-sha>` | Prerelease |
| rc | `0.4.0-rc.<commit-count>.g<12-char-sha>` | Prerelease |
| main | `0.4.0` | Stable |
| Feature branch / PR | `0.4.0-development.<commit-count>.g<12-char-sha>` | Not publishable |

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

Alpha PRs use Windows runners for formatting, Clippy, Rust unit tests,
Python protocol/release-policy tests, and a separate distribution build.
The packaged binary is tested from an extracted directory, including spaces in
its path. Artifacts contain the ZIP, SHA256SUMS.txt and source build identity.
Runtime artifacts, local chat traces, credentials and compiled binaries are not
committed to source branches. Compiled binaries are committed only to dedicated
marketplace distribution branches by the Release workflow. Actions are pinned to commit SHAs.

Required branch checks remain **Quality checks** and **Package verification**.
Changes use PRs; push events no longer duplicate PR validation. Updated PRs
cancel their obsolete CI runs. Alpha PRs run the full checks once. Promotion
PRs on beta, rc and main use lightweight Linux jobs: enforce the stage order,
require the merge tree to equal the source tree, locate a successful alpha CI
package with exactly that tested tree, and retain its run/artifact/checksum
identity. Missing, expired or mismatched evidence blocks promotion. No Rust
compilation, protocol rerun or renamed binary occurs during promotion.
`promotion-validation.json` links to the original evidence. A tree includes
workflow and test files, so changing CI also requires a fresh alpha validation.

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
channel (`production` uses `main`). Leave **publish** disabled for an end-to-end
validation run. Enable it explicitly to publish a GitHub Release and advance its Git marketplace channel.

The workflow checks the branch, reuses alpha source validation, builds the final
channel version once and checks the packaged binary, including explicit
65-second and 90-second wait regressions. It publishes that verified artifact;
promotion never relabels an alpha binary as a stable one. A write-scoped
job creates an annotated `v<version>` tag and uploads the distribution, checksum
and build identity. Non-production releases are prereleases. A rerun may finish
an incomplete draft, but cannot replace an existing asset or retarget a tag.
Stable releases also refuse to reuse a version for a different source commit.

After the immutable release is published, `scripts/publish_marketplace.py` verifies
that the downloaded release ZIP matches the validated local artifact. It copies
the ZIP's complete file set into a Git tree, including `terminalhook.exe`, plus
the release `SHA256SUMS.txt`. It never compiles or downloads a different binary.

| Release channel | Distribution branch | Installer option |
| --- | --- | --- |
| production | `marketplace` | default / `-Channel production` |
| alpha | `marketplace-alpha` | `-Channel alpha` |
| beta | `marketplace-beta` | `-Channel beta` |
| rc | `marketplace-rc` | `-Channel rc` |

The first distribution commit has no source-history parent. Later commits parent
the current distribution head and update the branch without force. Concurrent
writers are rejected. Identical reruns are no-ops; changed files for an existing
version, source-history rollbacks, version downgrades, and cross-channel updates
are rejected. Obsolete files do not carry over from the previous tree. Branches
are created on the first publication, so a channel cannot be installed beforehand.
Do not target these branches with source PRs or edit them manually. Repository
rules must allow the release token to create and fast-forward these four branches.

A failed marketplace step leaves the already published immutable GitHub Release
intact. Rerun the same Release request to finish distribution; it must not replace
release assets or overwrite a newer marketplace head. With **publish** disabled,
neither releases nor Git marketplace refs change.

The catalog keeps the historical name `terminalhook-local`, preserving installed
plugin IDs and settings. `source.path` is local **within the Git snapshot**; the
marketplace registration itself is a Git URL with a moving channel branch, not
a pinned release tag or commit. Codex owns startup refreshes and installed-cache
updates. The project does not add an updater daemon or lifecycle hook.

CI pins Codex CLI 0.167.0 for `scripts/verify_git_marketplace.py`. A loopback HTTP
Git fixture verifies migration from a local install, failed-channel recovery,
automatic app-server startup updates, and the official marketplace upgrade
command replacing the binary. It uses an isolated Codex home and no model turns.
This is not a claim that every desktop version hot-swaps active MCP connections.

Publication uses GitHub's workflow token; no personal access token or external
runner is required. Direct tool exposure and absence of model requests must still
be verified in the actual desktop app. Protocol CI does not pretend to prove
desktop behavior. The default package has no lifecycle hooks; the historical
0.3.1 implementation is preserved on `legacy/stop-hook`.

## Local build

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_package.py
```

Builds are staged in `dist/package`, separate from the source plugin directory.
Use `./scripts/install.ps1 -Source ./dist/package` for a local installation and
keep that directory available. Without `-Source`, the installer tracks the Git
stable channel, even when run from a source checkout or extracted ZIP.

## License

Source and release packages use the MIT license in `LICENSE`. Cargo dependency
licenses remain those of their respective authors.
