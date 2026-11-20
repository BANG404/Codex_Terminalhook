"""Require successful alpha source validation for the exact promotion tree."""
import argparse
from collections import deque
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

from deliver import REQUIRED_CHECKS
from distribution import PLATFORMS

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}")
REFRESH_CHECKS = ("Source refresh quality", "Source refresh verification")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validate_report(report, tree, artifacts):
    if not report.get("passed") or report.get("source_tree") != tree:
        raise ValueError("Package evidence does not verify the promoted source tree")
    if not re.fullmatch(r"[0-9a-f]{40}", report.get("source_revision", "")):
        raise ValueError("Missing tested source revision")
    platforms = report.get("platforms", {})
    if set(platforms) != set(PLATFORMS):
        raise ValueError("Package evidence must cover every supported platform")
    identities = {}
    for target in PLATFORMS:
        native = platforms[target]
        if (not native.get("passed") or native.get("platform") != target or native.get("source_tree") != tree
                or native.get("source_revision") != report["source_revision"]):
            raise ValueError("Native package evidence differs from promoted source")
        for key in ("archive_sha256", "binary_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", native.get(key, "")):
                raise ValueError("Missing package integrity evidence")
        distribution = next((a for a in artifacts if a["name"] == "terminalhook-" + target and not a["expired"]), None)
        if distribution is None:
            raise ValueError("A verified distribution is missing or expired; rerun alpha CI")
        identities[target] = distribution["id"]
    return identities


def promotion_tree(checkout_tree, source_tree):
    if checkout_tree != source_tree:
        raise ValueError("Promotion merge adds changes outside the checked source; update alpha first")
    return checkout_tree


def validation_gate(jobs, run, required_checks):
    completed = {
        job["name"]: job
        for job in jobs
        if job.get("run_id") == run["id"] and job.get("run_attempt") == run["run_attempt"]
        and job.get("head_sha") == run["head_sha"]
        and job.get("status") == "completed" and job.get("conclusion") == "success"
    }
    if all(name in completed for name in required_checks):
        return completed[required_checks[-1]]
    return None


class GitHub:
    def __init__(self, repository):
        self.repository = repository

    def api(self, suffix):
        return json.loads(subprocess.check_output(["gh", "api", f"repos/{self.repository}/{suffix}"], text=True, encoding="utf-8"))

    def pages(self, suffix, key=None):
        command = ["gh", "api", f"repos/{self.repository}/{suffix}", "--paginate", "--slurp"]
        pages = json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))
        return [item for page in pages for item in (page if key is None else page[key])]

    def alpha_pull_requests(self, revision):
        return [pr for pr in self.pages(f"commits/{revision}/pulls?per_page=100")
                if pr.get("merged_at") and pr["merge_commit_sha"] == revision
                and pr["base"]["ref"] == "alpha" and pr["base"]["repo"]["full_name"] == self.repository]

    def report(self, artifact_id, filename):
        archive = subprocess.check_output(["gh", "api", f"repos/{self.repository}/actions/artifacts/{artifact_id}/zip"])
        with zipfile.ZipFile(io.BytesIO(archive)) as files:
            return json.loads(files.read(filename))

    def latest_run(self, candidate, pr):
        event = "pull_request" if pr else "workflow_dispatch"
        branch = pr["head"]["ref"] if pr else "alpha"
        head_repo = pr["head"]["repo"] if pr else {"full_name": self.repository}
        query = f"actions/workflows/ci.yml/runs?event={event}&head_sha={candidate}&per_page=100"
        matching = []
        for run in self.pages(query, "workflow_runs"):
            if (run["head_sha"] != candidate or run["event"] != event or run["head_branch"] != branch
                    or run["path"] != ".github/workflows/ci.yml"
                    or run["repository"]["full_name"] != self.repository):
                continue
            if head_repo and (run.get("head_repository") or {}).get("full_name") != head_repo["full_name"]:
                continue
            if pr and run.get("pull_requests") and not any(
                    item["number"] == pr["number"] and item["base"]["ref"] == "alpha"
                    and item["head"]["sha"] == candidate for item in run["pull_requests"]):
                continue
            matching.append(run)
        return max(matching, key=lambda run: run["id"], default=None)

    def proof(self, candidate, tree, source, pr=None):
        run = self.latest_run(candidate, pr)
        if not run or run["status"] != "completed" or run.get("conclusion") != "success":
            return None
        run_id, attempt = run["id"], run["run_attempt"]
        jobs = self.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
        gate = validation_gate(jobs, run, REQUIRED_CHECKS if pr else REFRESH_CHECKS)
        if gate is None:
            return None
        artifacts = self.pages(f"actions/runs/{run_id}/artifacts?per_page=100", "artifacts")
        source_artifacts = [item for item in artifacts if item["name"] == "source-validation"]
        if source_artifacts:
            name, filename = "source-validation", "source-validation.json"
        elif pr:
            name, filename = "package-verification", "package-validation.json"
        else:
            return None
        artifact = next((item for item in artifacts
                         if item["name"] == name and not item["expired"]
                         and gate["started_at"] <= item["created_at"] <= gate["completed_at"]), None)
        if artifact is None:
            return None
        report = self.report(artifact["id"], filename)
        if report.get("passed") is not True or report.get("source_tree") != tree:
            return None
        tested = report.get("source_revision", "")
        if not SHA.fullmatch(tested):
            return None
        if source_artifacts:
            if report.get("run_id") != run_id or report.get("run_attempt") != attempt:
                return None
            verification = "alpha_pr_source" if pr else "alpha_source_refresh"
        else:
            validate_report(report, tree, artifacts)
            verification = "legacy_alpha_package"
        if pr:
            commit = self.api(f"git/commits/{tested}")
            parents = [parent["sha"] for parent in commit["parents"]]
            if (commit["tree"]["sha"] != tree or len(parents) != 2 or parents[1] != candidate
                    or git("merge-base", parents[0], source) != parents[0]):
                return None
        elif tested != candidate:
            return None
        return {"passed": True, "verification": verification, "source_tree": tree,
                "tested_revision": tested, "verified_run_id": run_id, "verified_run_attempt": attempt,
                "verified_run_url": run["html_url"], "source_artifact_id": artifact["id"]}


def find_proof(source, tree, github):
    pending = deque([source])
    visited, tested = set(), set()
    while pending and len(visited) < 32:
        commit = pending.popleft()
        if commit in visited:
            continue
        visited.add(commit)
        proof = github.proof(commit, tree, source)
        if proof:
            proof["promotion_source"] = source
            return proof
        # Associated alpha PRs include squash/rebase merges whose feature head
        # is not retained as a second parent in the stage's Git history.
        for pr in github.alpha_pull_requests(commit):
            if pr["number"] in tested:
                continue
            tested.add(pr["number"])
            proof = github.proof(pr["head"]["sha"], tree, source, pr)
            if proof:
                proof["promotion_source"] = source
                return proof
        # Stage merge commits may have different SHAs but must retain the tested tree.
        for parent in git("show", "-s", "--format=%P", commit).split():
            if git("rev-parse", f"{parent}^{{tree}}") == tree:
                pending.append(parent)
    raise ValueError("No successful CI source evidence proves this tree; validate the alpha PR or refresh this source on alpha")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/promotion-validation.json")
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event.get("pull_request")
    source = pr["head"]["sha"] if pr else os.environ["GITHUB_SHA"]
    if not SHA.fullmatch(source):
        raise ValueError("Invalid promotion revision")
    tree = promotion_tree(git("rev-parse", "HEAD^{tree}"), git("rev-parse", f"{source}^{{tree}}"))
    proof = find_proof(source, tree, GitHub(os.environ["GITHUB_REPOSITORY"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key in ("verified_run_id", "verified_run_url", "verified_run_attempt", "source_artifact_id"):
                output.write(f"{key}={proof[key]}\n")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
