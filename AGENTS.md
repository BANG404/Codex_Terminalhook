# Agent instructions

Use `rg` for text searches, `fd` for file and directory discovery, and `bat` for
reading files. The D: development drive uses ReFS.

Maintain human-facing documentation in English and Simplified Chinese. English
is the default: use `name.md` and `name.zh-CN.md`, with reciprocal language links.
Update both versions when changing documented behavior. Keep agent instructions
(`AGENTS.md`, `SKILL.md`) and workflow scripts in English.

## Task completion

Submitting a pull request to `alpha` is part of completing a repository task.
Unless the user explicitly requests local-only work or a different destination,
carry out these steps without asking again for permission to commit, push, or
open the pull request:

1. Fetch `origin/alpha` and use a task-specific `codex/` branch based on it. Keep
   unrelated work intact; use an isolated worktree when another task shares the
   checkout. Never force-push or overwrite another task's changes.
2. Finish the requested implementation, update both documentation languages,
   and run checks appropriate to the change. Inspect the diff and stage only
   files or hunks belonging to this task. Exclude generated artifacts, binaries,
   logs, and credentials.
3. Commit the completed changes and push the task branch to `origin`.
4. Create a pull request targeting `alpha`, or update the existing open pull
   request for that branch. Describe the resulting behavior and validation in
   English. Check its target, head, changed files, and initial CI status.
5. Report the pull request URL and actual validation/CI state. If submission is
   blocked by authentication, network access, conflicts, or failed checks,
   report the concrete blocker and retain the work for recovery; do not claim
   the task was submitted.

Do not push task commits directly to stage branches. Creating this pull request
does not authorize merging it, promoting later stages, or publishing a release.
For implementation details and the stage policy, read
[Development and releases](docs/releasing.md).
