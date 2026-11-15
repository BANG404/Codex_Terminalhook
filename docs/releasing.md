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

## Task completion and alpha pull requests

Repository tasks finish by submitting their changes to `alpha`. The agent follows
the checked-in [AGENTS.md](../AGENTS.md): complete the implementation and relevant
checks, commit only the task's changes on a `codex/` branch based on the latest
`origin/alpha`, push that branch, and create or update its pull request with
`alpha` as the base. This submission is automatic at task completion unless the
user requests local-only work or another destination; no additional confirmation
is needed for the commit, push, or PR.

Keep unrelated work intact and use a separate worktree for concurrent tasks.
Write the PR title and description in English and include validation results.
Verify its base/head, changed files, and initial CI status, then report its URL
and actual CI state. A submitted PR may still have checks running. If submission
fails, retain the work and report the blocker. Do not report local-only changes
as submitted. Merging, later-stage promotion, and release publication remain
separate actions.

## CI

Alpha PRs use Windows runners for formatting, Clippy, Rust unit tests,
Python protocol/release-policy tests, and a separate distribution build.
The packaged binary is tested from an extracted directory, including spaces in
its path. Artifacts contain the ZIP, SHA256SUMS.txt and source build identity.
Runtime artifacts, local chat traces, credentials and compiled binaries are not
committed to the source repository. Actions are pinned to commit SHAs.

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
validation run. Enable it explicitly to publish a GitHub Release.

The workflow checks the branch, reuses alpha source validation, builds the final
channel version once and checks the packaged binary, including explicit
65-second and 90-second wait regressions. It publishes that verified artifact;
promotion never relabels an alpha binary as a stable one. A write-scoped
job creates an annotated `v<version>` tag and uploads the distribution, checksum
and build identity. Non-production releases are prereleases. A rerun may finish
an incomplete draft, but cannot replace an existing asset or retarget a tag.
Stable releases also refuse to reuse a version for a different source commit.

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
The local installer selects that staged distribution when available. Keep the
installed marketplace directory in place after installation.

## License

Source and release packages use the MIT license in `LICENSE`. Cargo dependency
licenses remain those of their respective authors.
