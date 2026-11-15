# Development and releases

TerminalHook uses the stage progression `alpha -> beta -> rc -> main`.
Feature PRs target `alpha`. Promote a tested stage through a same-repository PR;
CI rejects skipped stages and promotions from forks. The initial repository
snapshot seeds all four branches with the same commit. Subsequent changes use PRs.

| Source branch | Artifact version | GitHub Release |
| --- | --- | --- |
| alpha | `0.3.1-alpha.<commit-count>.g<12-char-sha>` | Prerelease |
| beta | `0.3.1-beta.<commit-count>.g<12-char-sha>` | Prerelease |
| rc | `0.3.1-rc.<commit-count>.g<12-char-sha>` | Prerelease |
| main | `0.3.1` | Stable |
| Feature branch / PR | `0.3.1-development.<commit-count>.g<12-char-sha>` | Not publishable |

`Cargo.toml`, `Cargo.lock` and the source plugin manifest share a stable version
baseline. Change them together for the next stable release. Channel suffixes are
derived during packaging without editing tracked files. The binary's `--version`,
MCP server identity, packaged manifest and `build-info.json` agree. CI rejects a
dirty checkout, a mismatched source SHA or a mismatched channel/branch.

## CI

GitHub-hosted Windows runners execute formatting, Clippy, Rust unit tests,
Python protocol/hook/release-policy tests, and a separate distribution build.
The packaged binary is tested from an extracted directory, including spaces in
its path. Artifacts contain the ZIP, SHA256SUMS.txt and source build identity.
Runtime artifacts, local chat traces, credentials and compiled binaries are not
committed to the source repository. Actions are pinned to commit SHAs.

Required branch checks are **Quality checks** and **Package verification**.
Normal pushes and PR merges run CI and never publish a release automatically.

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

The workflow checks the branch, runs the reusable CI with 65-second and 90-second
wait regressions, and reuses that run's verified build artifact. A write-scoped
job creates an annotated `v<version>` tag and uploads the distribution, checksum
and build identity. Non-production releases are prereleases. A rerun may finish
an incomplete draft, but cannot replace an existing asset or retarget a tag.
Stable releases also refuse to reuse a version for a different source commit.

Publication uses GitHub's workflow token; no personal access token or external
runner is required. Desktop Hook trust and absence of model requests must still
be verified in the actual desktop app. Protocol CI does not pretend to prove
desktop behavior; its report explicitly says desktop verification is pending.

## Local build

```powershell
./scripts/package.ps1 -Channel development
python scripts/verify_hook_package.py
```

Builds are staged in `dist/package`, separate from the source plugin directory.
The local installer selects that staged distribution when available. Keep the
installed marketplace directory in place after installation.

## License

Source and release packages use the MIT license in `LICENSE`. Cargo dependency
licenses remain those of their respective authors.
