# Agent instructions

Use `rg` for text searches, `fd` for file and directory discovery, and `bat` for
reading files. The D: development drive uses ReFS.

Maintain human-facing documentation in English and Simplified Chinese. English
is the default: use `name.md` and `name.zh-CN.md`, with reciprocal language links.
Update both versions when changing documented behavior. Keep agent instructions
(`AGENTS.md`, `SKILL.md`) and workflow scripts in English.

## Task completion

Submitting and merging a verified pull request to `alpha` is part of completing a repository task.
Unless the user explicitly requests local-only work or a different destination,
carry out these steps without asking again for permission to commit, push, or
open the pull request, or merge it after its actual required CI checks succeed:

1. Fetch `origin/alpha` and use a task-specific `codex/` branch based on it. Keep
   unrelated work intact; use an isolated worktree when another task shares the
   checkout. Never force-push or overwrite another task's changes.
2. Finish the requested implementation, update both documentation languages,
   and run checks appropriate to the change. Inspect the diff and stage only
   files or hunks belonging to this task. Exclude generated artifacts, binaries,
   logs, and credentials.
3. Commit the completed changes with an English Conventional Commit subject.
   Write an English PR body describing the resulting behavior and validation to
   an ignored temporary file. Run `python scripts/deliver.py submit --body-file
   <absolute-path>` to push the task branch, create/reuse its alpha PR, follow
   the actual current-source CI, and merge with the exact expected HEAD.
4. Require successful `Quality checks` and `Package verification` from the
   GitHub Actions App, the same latest CI run and attempt, and this PR HEAD.
   Honor review and branch protections. Never force push, bypass protections,
   or push alpha directly. Verify that the actual two-parent merge commit
   retains the submitted source as its second parent before declaring success.
5. Diagnose and repair recoverable delivery failures within the authorized
   scope, then continue through verified merge. Report the pull request URL,
   merge commit, and actual validation state. Use an event or a bounded wait
   for an external prerequisite when possible. Preserve the work and pause
   when recovery requires human input or approval.

Keep one waiter per PR across worktrees. After an interrupted follow-up, use the
printed `scripts/deliver.py follow <number> --head <sha> --merge` command; do not
blindly replay commits, pushes, PR creation, or merge writes. A changed source
requires new checks. Preserve failed PRs and their source for repair.

When following CI or an authorized CD, inspect the actual job graph, timings,
and first failing prerequisite. Prefer early inexpensive gates, shared verified
artifacts, and parallel independent jobs. Fix measurable waste or recoverable
failures within scope and verify the new run. Reconcile the original remote
state before retrying a write. Escalate decisions needing credentials, review,
protection changes, or authorization for a new deployment or release.

Alpha advancement alone does not invalidate successful checks for the original
PR HEAD. Verify the CI package report's tested merge parents and tree, and require
the tested base to remain in current alpha history. Actual conflicts or source
changes require repair and fresh checks. A non-strict required-status-check policy
supports this model; never change protections automatically to make a PR merge.

Automatic task delivery only merges into alpha. Promoting later stages and
publishing a release remain separate actions requiring their own authorization.
For implementation details and the stage policy, read
[Development and releases](docs/releasing.md).
