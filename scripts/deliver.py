"""Submit a clean codex/ task branch to alpha and merge after its actual CI passes."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "BANG404/Codex_Terminalhook"
REQUIRED_CHECKS = ("Quality checks", "Package verification")
ACTIONS_APP_ID = 15368
SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"codex/[A-Za-z0-9][A-Za-z0-9._/-]*")


def command(args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd or ROOT, text=True, encoding="utf-8", capture_output=True, timeout=60)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result


def git(*args):
    return command(["git", *args]).stdout.strip()


class GitHub:
    def gh(self, *args):
        return command(["gh", *args]).stdout.strip()

    def read_json(self, *args):
        # Retry only reads, and only connection/server errors. Never replay writes.
        for attempt in range(4):
            try:
                return json.loads(self.gh(*args))
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                if attempt == 3 or not re.search(r"timeout|timed out|EOF|reset|HTTP (429|5\d\d)|TLS handshake", str(error), re.I):
                    raise
                time.sleep(2 ** attempt)

    def api(self, suffix, body=None, method="GET"):
        args = ["api", "--hostname", "github.com", f"repos/{REPOSITORY}/{suffix}".rstrip("/")]
        if body is None:
            return self.read_json(*args)
        with tempfile.TemporaryDirectory(prefix="terminalhook-api-") as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps(body), encoding="utf-8")
            return json.loads(self.gh(*args, "--method", method, "--input", str(request)))

    def pages(self, suffix, key):
        pages = self.read_json("api", "--hostname", "github.com", f"repos/{REPOSITORY}/{suffix}", "--paginate", "--slurp")
        return [item for page in pages for item in page[key]]

    def reviews(self, number):
        return self.read_json("pr", "view", str(number), "--repo", REPOSITORY,
                              "--json", "reviewDecision")["reviewDecision"]

    def source_report(self, run_id, attempt):
        artifacts = self.pages(f"actions/runs/{run_id}/artifacts?name=source-validation&per_page=100", "artifacts")
        source = [artifact for artifact in artifacts if artifact["name"] == "source-validation"]
        if len(source) > 1 or (source and source[0].get("expired")):
            raise ValueError("CI source-validation artifact is ambiguous or expired")
        name, filename = ("source-validation", "source-validation.json") if source else (
            "package-verification", "package-validation.json")
        with tempfile.TemporaryDirectory(prefix="terminalhook-delivery-proof-") as directory:
            self.gh("run", "download", str(run_id), "--repo", REPOSITORY,
                    "--name", name, "--dir", directory)
            report = json.loads((Path(directory) / filename).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("CI source report must be a JSON object")
        if source and (type(report.get("run_id")) is not int or report["run_id"] != run_id
                       or type(report.get("run_attempt")) is not int or report["run_attempt"] != attempt):
            raise ValueError("CI source report does not identify the latest verified run and attempt")
        return report


def validate_pr(pr, head, branch=None):
    if (not SHA.fullmatch(head) or pr["head"]["sha"] != head
            or pr["head"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["ref"] != "alpha"
            or not BRANCH.fullmatch(pr["head"]["ref"])
            or (branch is not None and pr["head"]["ref"] != branch)):
        raise ValueError("PR repository, branch, alpha base, or expected HEAD changed")
    if pr["state"] != "open" and not pr.get("merged"):
        raise ValueError("PR closed without merging; original source retained")
    if pr.get("draft"):
        raise ValueError("Draft PRs are not eligible for automatic delivery")
    return pr["head"]["ref"]


def check_progress(checks, jobs, head, run_id, attempt):
    latest = {}
    for check in checks:
        if (check.get("name") not in REQUIRED_CHECKS or check.get("head_sha") != head
                or check.get("app", {}).get("id") != ACTIONS_APP_ID):
            continue
        name = check["name"]
        if name not in latest or check["id"] > latest[name]["id"]:
            latest[name] = check
    for name in REQUIRED_CHECKS:
        check = latest.get(name)
        if not check:
            return f"{name}: missing"
        if not re.fullmatch(rf"https://github.com/{re.escape(REPOSITORY)}/actions/runs/{run_id}/job/\d+",
                            check.get("details_url", "")):
            return "Waiting for both required checks from the latest CI run"
        job = next((job for job in jobs if job["name"] == name
                    and job.get("check_run_url", "").endswith(f"/check-runs/{check['id']}")), None)
        if not job:
            return "Waiting for actual jobs from the latest CI attempt"
        if check["status"] != "completed":
            return f"{name}: {check['status']}"
        if check.get("conclusion") != "success":
            raise ValueError(f"CI run {run_id}, attempt {attempt}: {name}: {check.get('conclusion')}; "
                             f"inspect {check['details_url']}")
        if job["status"] != "completed" or job["conclusion"] != "success":
            return "Waiting for successful jobs from the latest CI attempt"
    return None


def first_failure(jobs):
    failed = []
    for job in jobs:
        if job.get("conclusion") not in {"failure", "timed_out", "startup_failure"}:
            continue
        step = next((step for step in job.get("steps", []) if step.get("conclusion") == "failure"), {})
        finished = step.get("completed_at") or job.get("completed_at")
        failed.append((finished, job, step))
    if not failed:
        return ""
    finished, job, step = min(failed, key=lambda item: item[0] or "9999")
    name = f"{job['name']} / {step['name']}" if step else job["name"]
    time_label = f" at {finished}" if finished else ""
    return f"; first failed job: {name} ({job['conclusion']}){time_label} {job.get('html_url', '')}".rstrip()


def ci_progress(hub, number, head, branch):
    runs = hub.pages(f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100", "workflow_runs")
    matching = [run for run in runs if run["head_sha"] == head and run["head_branch"] == branch]
    if not matching:
        return "Waiting for a CI run for the original source", None
    run_id = max(run["id"] for run in matching)
    run = hub.api(f"actions/runs/{run_id}")
    if (run["head_sha"] != head or run["head_branch"] != branch or run["event"] != "pull_request"
            or run["path"] != ".github/workflows/ci.yml"
            or run["repository"]["full_name"] != REPOSITORY):
        raise ValueError("CI run does not verify this repository, source, and workflow")
    if run.get("pull_requests") and not any(
            pr["number"] == number and pr["head"]["sha"] == head and pr["base"]["ref"] == "alpha"
            for pr in run["pull_requests"]):
        raise ValueError("CI run belongs to a different PR")
    attempt = run["run_attempt"]
    jobs = hub.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
    label = f"CI run {run_id}, attempt {attempt}"
    if run["status"] != "completed":
        return f"{label}: {run['status']}{first_failure(jobs)}", None
    if run["conclusion"] != "success":
        url = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}"
        raise ValueError(f"{label}: {run['conclusion']}{first_failure(jobs)}; inspect {url}")
    checks = hub.pages(f"commits/{head}/check-runs?filter=all&per_page=100", "check_runs")
    pending = check_progress(checks, jobs, head, run_id, attempt)
    if pending:
        return f"{label}: {pending}", None
    return f"{label}: both required checks succeeded", (run_id, attempt)


def verify_merge(hub, pr, head, branch, tested, verified_base=None):
    validate_pr(pr, head, branch)
    if not pr.get("merged") or not SHA.fullmatch(pr.get("merge_commit_sha") or ""):
        raise ValueError("GitHub has not confirmed the merge")
    commit = hub.api("git/commits/" + pr["merge_commit_sha"])
    parents = commit.get("parents", [])
    if len(parents) != 2 or parents[1]["sha"] != head:
        raise ValueError("Merged commit does not retain the expected source as its second parent")
    if parents[0]["sha"] != verified_base:
        verify_base_history(hub, tested, parents[0]["sha"])
    return {"pr": pr["html_url"], "head": head, "merge_commit": pr["merge_commit_sha"], "merged": True}


def tested_base(hub, ci, head):
    # The aggregate records the actual tested merge candidate, even when the
    # Actions run's pull_requests list is empty or alpha has since advanced.
    report = hub.source_report(*ci)
    revision, tree = report.get("source_revision", ""), report.get("source_tree", "")
    if report.get("passed") is not True or not all(isinstance(sha, str) and SHA.fullmatch(sha) for sha in (revision, tree)):
        raise ValueError("CI source report lacks a successful tested source identity")
    commit = hub.api("git/commits/" + revision)
    parents = commit.get("parents", [])
    if (len(parents) != 2 or parents[1].get("sha") != head
            or not SHA.fullmatch(parents[0].get("sha", "")) or commit.get("tree", {}).get("sha") != tree):
        raise ValueError("CI source report does not prove this PR HEAD and tested merge tree")
    return parents[0]["sha"]


def verify_base_history(hub, tested, current):
    if not SHA.fullmatch(current or ""):
        raise ValueError("PR has no valid current alpha base")
    if tested != current:
        comparison = hub.api(f"compare/{tested}...{current}")
        if comparison.get("merge_base_commit", {}).get("sha") != tested:
            raise ValueError("Alpha history no longer contains the tested base; inspect the history rewrite")


def merge_if_ready(hub, number, head, branch, ci):
    decision = hub.reviews(number)
    if decision == "CHANGES_REQUESTED":
        raise ValueError("Changes requested; automatic merge stopped")
    if decision == "REVIEW_REQUIRED":
        return None

    tested = tested_base(hub, ci, head)
    _, current_ci = ci_progress(hub, number, head, branch)
    if current_ci != ci or hub.reviews(number) in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
        return None
    # Detect a native queue or completed merge as close to the SHA-bound write as possible.
    fresh = hub.api(f"pulls/{number}")
    validate_pr(fresh, head, branch)
    if fresh.get("merged"):
        return verify_merge(hub, fresh, head, branch, tested)
    if fresh.get("auto_merge"):
        return None
    if fresh.get("mergeable_state") == "behind":
        raise ValueError("PR is behind alpha, not necessarily conflicted. Strict branch protection requires an update; "
                         "retain this HEAD and CI. Source-based alpha delivery requires an explicitly approved "
                         "non-strict status-check policy; this helper never changes protections or rewrites the source")
    if fresh.get("mergeable") is not True or fresh.get("mergeable_state") not in {"clean", "unstable", "has_hooks"}:
        return None
    base = fresh["base"].get("sha")
    verify_base_history(hub, tested, base)

    run_id, attempt = ci
    try:
        result = hub.api(f"pulls/{number}/merge", {
            "sha": head, "merge_method": "merge",
            "commit_title": f"chore(delivery): merge verified alpha PR #{number}",
            "commit_message": f"Source commit: {head}\nVerified CI run: {run_id}, attempt {attempt}\n",
        }, method="PUT")
        if result.get("merged") is not True:
            raise RuntimeError("GitHub did not accept the SHA-bound merge")
    except (RuntimeError, subprocess.TimeoutExpired):
        # A response can be lost after acceptance. Reconcile without replaying the write.
        observed = hub.api(f"pulls/{number}")
        validate_pr(observed, head, branch)
        if observed.get("merged"):
            return verify_merge(hub, observed, head, branch, tested, base)
        if observed.get("auto_merge"):
            return None
        raise
    return verify_merge(hub, hub.api(f"pulls/{number}"), head, branch, tested, base)


def follow(hub, number, head, timeout=3600, merge=False, pause=time.sleep, now=time.monotonic):
    deadline = now() + timeout
    branch = validate_pr(hub.api(f"pulls/{number}"), head)
    previous = None
    while now() < deadline:
        pr = hub.api(f"pulls/{number}")
        validate_pr(pr, head, branch)
        merged = pr.get("merged")
        if not merged and (pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty"):
            raise ValueError("PR has conflicts; resolve and verify a new source commit")
        status, ci = ci_progress(hub, number, head, branch)
        queued = not merged and pr.get("auto_merge")
        if merge and queued:
            status += "; waiting for GitHub auto-merge"
        if status != previous:
            print(status, flush=True)
            previous = status
        if ci and (merged or not merge):
            tested = tested_base(hub, ci, head)
            if merged:
                return verify_merge(hub, pr, head, branch, tested)
            verify_base_history(hub, tested, pr["base"].get("sha"))
            run_id, attempt = ci
            return {"pr": pr["html_url"], "head": head, "run_id": run_id, "run_attempt": attempt, "merged": False}
        if ci and not queued:
            result = merge_if_ready(hub, number, head, branch, ci)
            if result:
                return result
        pause(min(15, max(0, deadline - now())))
    raise TimeoutError(f"Timed out; retain PR #{number} and resume follow for HEAD {head}")


@contextmanager
def delivery_lock(number, head):
    # The common Git directory shares the lock across all worktrees.
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    directory = common / "terminalhook-delivery"
    directory.mkdir(exist_ok=True)
    lock = directory / f"pr-{number}.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(f"Another waiter owns {lock}; reuse its terminal, or inspect a stale lock before removing it") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"pid": os.getpid(), "head": head}, output)
        yield
    finally:
        lock.unlink()


def find_pr(hub, branch, head):
    prs = json.loads(hub.gh("pr", "list", "--repo", REPOSITORY, "--head", branch, "--state", "all",
                            "--limit", "1000", "--json", "number,headRefOid,headRefName,baseRefName,isCrossRepository"))
    if len(prs) > 1:
        raise ValueError("Multiple PRs use this task branch; inspect them before retrying")
    if not prs:
        return None
    pr = prs[0]
    if pr["headRefOid"] != head or pr["headRefName"] != branch or pr["baseRefName"] != "alpha" or pr["isCrossRepository"]:
        raise ValueError("Existing task PR identity differs")
    validate_pr(hub.api(f"pulls/{pr['number']}"), head, branch)
    return pr["number"]


def submit(hub, body_path, timeout):
    body = body_path.read_text(encoding="utf-8")
    if not body.strip():
        raise ValueError("Provide a PR body describing behavior and validation")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Commit only this task's changes in a clean isolated worktree before delivery")
    branch, head = git("branch", "--show-current"), git("rev-parse", "HEAD")
    if not BRANCH.fullmatch(branch):
        raise ValueError("Delivery requires a dedicated codex/ task branch")
    title = git("log", "-1", "--format=%s")
    if len(title) > 100 or not re.fullmatch(r"(feat|fix|docs|refactor|perf|test|build|ci|chore|style|revert)(\([a-z0-9._/-]+\))?!?: \S.*", title):
        raise ValueError("Use an English Conventional Commit subject of at most 100 characters")
    for url in (git("remote", "get-url", "origin"), git("remote", "get-url", "--push", "--all", "origin")):
        if not re.fullmatch(rf"(?:https://github.com/|git@github.com:|ssh://git@github.com/){re.escape(REPOSITORY)}(?:\.git)?", url):
            raise ValueError("Origin fetch/push must identify the expected GitHub repository")
    settings = hub.api("")
    if not settings.get("allow_merge_commit"):
        raise ValueError("Enable merge commits before alpha delivery")
    git("fetch", "origin", "alpha")
    candidate = command(["git", "merge-tree", "--write-tree", "origin/alpha", head], check=False)
    if candidate.returncode:
        raise ValueError("Source conflicts with alpha; resolve and verify before delivery")
    remote = git("ls-remote", "--heads", "origin", "refs/heads/" + branch).split()
    if remote and remote[0] != head:
        git("fetch", "origin", branch)
        git("merge-base", "--is-ancestor", remote[0], head)
    if git("rev-parse", "HEAD") != head or git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Source changed during delivery; nothing was pushed")
    if not remote or remote[0] != head:
        try:
            git("push", "origin", f"{head}:refs/heads/{branch}")
        except RuntimeError:
            if git("ls-remote", "--heads", "origin", "refs/heads/" + branch).split()[:1] != [head]:
                raise
    if git("ls-remote", "--heads", "origin", "refs/heads/" + branch).split()[:1] != [head]:
        raise ValueError("Remote task head changed after push")
    number = find_pr(hub, branch, head)
    if number is None:
        with tempfile.TemporaryDirectory(prefix="terminalhook-pr-") as directory:
            file = Path(directory) / "body.md"
            file.write_text(body.rstrip() + f"\n\nSource commit: `{head}`\n", encoding="utf-8")
            try:
                hub.gh("pr", "create", "--repo", REPOSITORY, "--base", "alpha", "--head", branch,
                       "--title", title, "--body-file", str(file))
            except (RuntimeError, subprocess.TimeoutExpired):
                number = find_pr(hub, branch, head)
                if number is None:
                    raise
        number = number or find_pr(hub, branch, head)
    if number is None:
        raise RuntimeError("PR creation returned without the matching PR; retain the remote branch")
    print(f"PR: https://github.com/{REPOSITORY}/pull/{number}\nHEAD: {head}", flush=True)
    print(f"Resume: python scripts/deliver.py follow {number} --head {head} --merge", flush=True)
    with delivery_lock(number, head):
        return follow(hub, number, head, timeout, merge=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    delivery = commands.add_parser("submit")
    delivery.add_argument("--body-file", type=Path, required=True)
    delivery.add_argument("--timeout", type=int, default=3600)
    waiter = commands.add_parser("follow")
    waiter.add_argument("number", type=int)
    waiter.add_argument("--head", required=True)
    waiter.add_argument("--merge", action="store_true")
    waiter.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    hub = GitHub()
    if args.command == "submit":
        result = submit(hub, args.body_file, args.timeout)
    else:
        if args.number < 1 or not SHA.fullmatch(args.head):
            parser.error("provide a positive PR number and full expected HEAD SHA")
        with delivery_lock(args.number, args.head):
            result = follow(hub, args.number, args.head, args.timeout, args.merge)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
