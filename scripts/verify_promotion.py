"""Reuse an alpha CI package only when its tested tree equals the promotion tree."""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validate_report(report, tree, artifacts):
    if not report.get("passed") or report.get("source_tree") != tree:
        raise ValueError("Package evidence does not verify the promoted source tree")
    for key in ("archive_sha256", "binary_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", report.get(key, "")):
            raise ValueError("Missing package integrity evidence")
    if not re.fullmatch(r"[0-9a-f]{40}", report.get("source_revision", "")):
        raise ValueError("Missing tested source revision")
    distribution = next((a for a in artifacts if a["name"] == "terminalhook-windows-x64" and not a["expired"]), None)
    if distribution is None:
        raise ValueError("The verified distribution is missing or expired; rerun alpha CI")
    return distribution["id"]


def promotion_tree(checkout_tree, source_tree):
    if checkout_tree != source_tree:
        raise ValueError("Promotion merge adds changes outside the checked source; update alpha first")
    return checkout_tree


def verified_jobs(jobs):
    return all(any(j["name"] == name and j.get("conclusion") == "success" for j in jobs)
               for name in ("Quality checks", "Package verification"))


class GitHub:
    def __init__(self, repository):
        self.repository = repository

    def api(self, suffix):
        return json.loads(subprocess.check_output(["gh", "api", f"repos/{self.repository}/{suffix}"], text=True, encoding="utf-8"))

    def proof(self, candidate, tree):
        runs = self.api(f"actions/workflows/ci.yml/runs?head_sha={candidate}&status=success&per_page=100")["workflow_runs"]
        for run in runs:
            if run["head_sha"] != candidate or run.get("conclusion") != "success":
                continue
            artifacts = self.api(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
            if not any(a["name"] == "terminalhook-windows-x64" and not a["expired"] for a in artifacts):
                continue  # Lightweight promotion runs do not produce a new binary.
            if not any(a["name"] == "package-verification" and not a["expired"] for a in artifacts):
                continue
            if not verified_jobs(self.api(f"actions/runs/{run['id']}/jobs?per_page=100")["jobs"]):
                continue
            with tempfile.TemporaryDirectory(prefix="terminalhook-ci-proof-") as directory:
                subprocess.run(["gh", "run", "download", str(run["id"]), "--repo", self.repository,
                                "--name", "package-verification", "--dir", directory], check=True,
                               capture_output=True, text=True, encoding="utf-8")
                evidence = Path(directory) / "package-validation.json"
                if not evidence.is_file():
                    continue
                report = json.loads(evidence.read_text(encoding="utf-8"))
            if report.get("source_tree") != tree:
                continue
            artifact_id = validate_report(report, tree, artifacts)
            return {"passed": True, "verification": "reused_alpha_package", "source_tree": tree,
                    "tested_revision": report["source_revision"], "verified_run_id": run["id"],
                    "verified_run_url": run["html_url"], "distribution_artifact_id": artifact_id,
                    "archive_sha256": report["archive_sha256"], "rebuilt": False}
        return None


def find_proof(source, tree, github):
    pending = deque([source])
    visited, tested = set(), set()
    while pending and len(visited) < 32:
        commit = pending.popleft()
        if commit in visited:
            continue
        visited.add(commit)
        # Associated alpha PRs also cover squash/rebase merges whose feature head
        # is not retained as a second parent in the stage's Git history.
        candidates = [commit]
        for pr in github.api(f"commits/{commit}/pulls?per_page=100"):
            if pr.get("merged_at") and pr["base"]["ref"] == "alpha":
                candidates.append(pr["head"]["sha"])
        for candidate in candidates:
            if candidate in tested:
                continue
            tested.add(candidate)
            proof = github.proof(candidate, tree)
            if proof:
                proof["promotion_source"] = source
                return proof
        # Stage merge commits may have different SHAs but must retain the tested tree.
        for parent in git("show", "-s", "--format=%P", commit).split():
            if git("rev-parse", f"{parent}^{{tree}}") == tree:
                pending.append(parent)
    raise ValueError("No successful CI package proves this tree; run a full alpha PR validation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/promotion-validation.json")
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event.get("pull_request")
    source = pr["head"]["sha"] if pr else os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Invalid promotion revision")
    tree = promotion_tree(git("rev-parse", "HEAD^{tree}"), git("rev-parse", f"{source}^{{tree}}"))
    proof = find_proof(source, tree, GitHub(os.environ["GITHUB_REPOSITORY"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key in ("verified_run_id", "verified_run_url", "archive_sha256"):
                output.write(f"{key}={proof[key]}\n")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
