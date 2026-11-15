"""Exercise real delivery policy with fake GitHub evidence and local Git remotes."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deliver", ROOT / "scripts/deliver.py")
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)
HEAD, BASE, MERGE = "a" * 40, "b" * 40, "c" * 40
REPO = delivery.REPOSITORY


class FakeGitHub:
    def __init__(self, head=HEAD):
        self.pr = {"number": 11, "state": "open", "merged": False, "draft": False,
                   "head": {"sha": head, "ref": "codex/task", "repo": {"full_name": REPO}},
                   "base": {"sha": BASE, "ref": "alpha", "repo": {"full_name": REPO}},
                   "mergeable": True, "mergeable_state": "clean", "auto_merge": None,
                   "html_url": f"https://github.com/{REPO}/pull/11"}
        self.checks = [{"id": index, "name": name, "head_sha": head, "app": {"id": 15368},
                        "status": "completed", "conclusion": "success",
                        "details_url": f"https://github.com/{REPO}/actions/runs/50/job/{index}"}
                       for index, name in enumerate(delivery.REQUIRED_CHECKS, 10)]
        self.run = {"id": 50, "head_sha": head, "head_branch": "codex/task", "event": "pull_request",
                    "path": ".github/workflows/ci.yml", "repository": {"full_name": REPO},
                    "status": "completed", "conclusion": "success", "run_attempt": 2, "pull_requests": [deepcopy(self.pr)]}
        self.runs = [self.run]
        self.jobs = [{"name": check["name"], "status": "completed", "conclusion": "success",
                      "check_run_url": f"https://api.github.com/repos/{REPO}/check-runs/{check['id']}"}
                     for check in self.checks]
        self.parents = [{"sha": BASE}, {"sha": head}]
        self.decision = ""
        self.writes = []
        self.lose_merge_response = False

    def api(self, path, body=None, method="GET"):
        if path == "":
            return {"allow_merge_commit": True}
        if path == "pulls/11/merge":
            self.writes.append((body, method))
            self.pr.update(merged=True, state="closed", merge_commit_sha=MERGE)
            if self.lose_merge_response:
                raise RuntimeError("HTTP 502 response lost")
            return {"merged": True, "sha": MERGE}
        if path == "pulls/11":
            return deepcopy(self.pr)
        if path == "actions/runs/50":
            return deepcopy(self.run)
        if path == "git/commits/" + MERGE:
            return {"parents": deepcopy(self.parents)}
        raise AssertionError("Unexpected GitHub read " + path)

    def pages(self, path, _key):
        if path.startswith("commits/"):
            return deepcopy(self.checks)
        if path.startswith("actions/workflows/"):
            return deepcopy(self.runs)
        if path == "actions/runs/50/attempts/2/jobs?per_page=100":
            return deepcopy(self.jobs)
        raise AssertionError("Unexpected GitHub listing " + path)

    def reviews(self, _number):
        return self.decision


class DeliveryGateTests(unittest.TestCase):
    def follow(self, hub, **kwargs):
        clock = [0]
        def pause(seconds):
            clock[0] += seconds
        with redirect_stdout(io.StringIO()):
            return delivery.follow(hub, 11, HEAD, timeout=1, now=lambda: clock[0], pause=pause, **kwargs)

    def test_merges_only_after_actual_jobs_and_proves_two_parents(self):
        hub = FakeGitHub()
        result = self.follow(hub, merge=True)
        self.assertEqual(result["merge_commit"], MERGE)
        self.assertEqual(len(hub.writes), 1)
        self.assertEqual(hub.writes[0][0]["sha"], HEAD)
        self.assertEqual(hub.writes[0][0]["merge_method"], "merge")
        self.assertEqual(hub.writes[0][1], "PUT")

    def test_missing_wrong_source_or_wrong_app_evidence_never_merges(self):
        for change in ({"head_sha": BASE}, {"app": {"id": 42}}, {"name": "Fake quality checks"}):
            with self.subTest(change=change):
                hub = FakeGitHub()
                hub.checks[0].update(change)
                with self.assertRaises(TimeoutError):
                    self.follow(hub, merge=True)
                self.assertEqual(hub.writes, [])

    def test_new_pending_or_failed_check_supersedes_old_green(self):
        for state in ("queued", "failure", "cancelled", "skipped"):
            with self.subTest(state=state):
                hub = FakeGitHub()
                check = dict(hub.checks[0], id=99, status="queued" if state == "queued" else "completed", conclusion=state)
                hub.checks.append(check)
                with self.assertRaises(TimeoutError if state == "queued" else ValueError):
                    self.follow(hub, merge=True)
                self.assertEqual(hub.writes, [])

    def test_new_run_and_attempt_cannot_reuse_old_success(self):
        for change in ("run", "attempt", "mixed"):
            with self.subTest(change=change):
                hub = FakeGitHub()
                if change == "run":
                    hub.runs.append(dict(hub.run, id=51, status="queued"))
                elif change == "attempt":
                    hub.jobs[0]["check_run_url"] = "https://api.github.com/repos/x/check-runs/99"
                else:
                    hub.checks[0]["details_url"] = f"https://github.com/{REPO}/actions/runs/51/job/10"
                with self.assertRaises(TimeoutError):
                    self.follow(hub, merge=True)
                self.assertEqual(hub.writes, [])

    def test_wrong_workflow_or_event_cannot_authorize_merge(self):
        for change in ({"event": "workflow_dispatch"}, {"path": ".github/workflows/fake.yml"}):
            hub = FakeGitHub()
            hub.run.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "CI run does not verify"):
                self.follow(hub, merge=True)
            self.assertEqual(hub.writes, [])

    def test_identity_change_conflicts_reviews_and_protections(self):
        for change in ("head", "base", "fork", "draft", "closed", "conflict", "native", "review", "behind"):
            hub = FakeGitHub()
            if change == "head":
                hub.pr["head"]["sha"] = BASE
            elif change == "base":
                hub.pr["base"]["ref"] = "main"
            elif change == "fork":
                hub.pr["head"]["repo"]["full_name"] = "fork/terminalhook"
            elif change == "review":
                hub.decision = "CHANGES_REQUESTED"
            else:
                hub.pr.update({"draft": {"draft": True}, "closed": {"state": "closed"},
                               "conflict": {"mergeable": False}, "native": {"auto_merge": {"enabled": True}},
                               "behind": {"mergeable_state": "behind"}}[change])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.follow(hub, merge=True)
            self.assertEqual(hub.writes, [])

    def test_rechecks_head_immediately_before_merge(self):
        hub = FakeGitHub()
        original = hub.api
        reads = [0]
        def api(path, *args, **kwargs):
            if path == "pulls/11":
                reads[0] += 1
                if reads[0] == 3:
                    hub.pr["head"]["sha"] = BASE
            return original(path, *args, **kwargs)
        hub.api = api
        with self.assertRaisesRegex(ValueError, "HEAD changed"):
            self.follow(hub, merge=True)
        self.assertEqual(hub.writes, [])

    def test_observation_and_required_reviews_do_not_write(self):
        for merge, decision in ((False, ""), (True, "REVIEW_REQUIRED")):
            hub = FakeGitHub()
            hub.decision = decision
            with self.subTest(merge=merge), self.assertRaises(TimeoutError):
                self.follow(hub, merge=merge)
            self.assertEqual(hub.writes, [])

    def test_lost_merge_response_is_reconciled_without_replaying_write(self):
        hub = FakeGitHub()
        hub.lose_merge_response = True
        self.assertTrue(self.follow(hub, merge=True)["merged"])
        self.assertEqual(len(hub.writes), 1)

    def test_merge_requires_real_second_source_parent(self):
        hub = FakeGitHub()
        hub.parents = [{"sha": BASE}]
        with self.assertRaisesRegex(ValueError, "second parent"):
            self.follow(hub, merge=True)


class DeliverySubmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="terminalhook delivery ")
        self.task_root = Path(self.directory.name)
        self.work = self.task_root / "work"
        self.remote = self.task_root / "origin.git"
        self.work.mkdir()
        self.run_git("init", "--bare", str(self.remote))
        self.run_git("init", "-b", "alpha", cwd=self.work)
        (self.work / "source.txt").write_text("initial\n")
        self.run_git("add", ".", cwd=self.work)
        self.run_git("commit", "-m", "chore: initial", cwd=self.work)
        self.run_git("remote", "add", "origin", str(self.remote), cwd=self.work)
        self.run_git("push", "origin", "alpha", cwd=self.work)
        self.run_git("checkout", "-b", "codex/task", cwd=self.work)
        (self.work / "source.txt").write_text("task change\n")
        self.run_git("add", ".", cwd=self.work)
        self.run_git("commit", "-m", "feat: deliver task", cwd=self.work)
        self.head = self.run_git("rev-parse", "HEAD", cwd=self.work)
        self.body = self.task_root / "body.md"
        self.body.write_text("Result and validation. Literal `$(never-run)` stays text.\n")
        self.created = False
        self.creates = 0
        self.hub = FakeGitHub(self.head)

    def tearDown(self):
        self.directory.cleanup()

    def run_git(self, *args, cwd=None):
        return subprocess.check_output(["git", "-c", "user.name=Delivery Test", "-c", "user.email=test@example.invalid",
                                        *args], cwd=cwd or self.task_root, text=True, stderr=subprocess.DEVNULL).strip()

    def submit(self, lost_creation=False):
        original_git = delivery.git
        def git(*args):
            if args[:2] == ("remote", "get-url"):
                return "https://github.com/" + REPO + ".git"
            return original_git(*args)
        def gh(*args):
            if args[:2] == ("pr", "list"):
                return json.dumps([{"number": 11, "headRefOid": self.head, "headRefName": "codex/task",
                                    "baseRefName": "alpha", "isCrossRepository": False}] if self.created else [])
            if args[:2] == ("pr", "create"):
                self.creates += 1
                self.assertEqual(args[args.index("--base") + 1], "alpha")
                self.assertIn("`$(never-run)`", Path(args[args.index("--body-file") + 1]).read_text())
                self.created = True
                if lost_creation:
                    raise RuntimeError("response lost after creation")
                return self.hub.pr["html_url"]
            self.fail("Unexpected GitHub command")
        self.hub.gh = gh
        with patch.object(delivery, "ROOT", self.work), patch.object(delivery, "git", side_effect=git), \
                patch.object(delivery, "follow", return_value={"merged": True}) as follow, redirect_stdout(io.StringIO()):
            result = delivery.submit(self.hub, self.body, 60)
            follow.assert_called_once_with(self.hub, 11, self.head, 60, merge=True)
            return result

    def test_pushes_real_task_branch_and_reuses_exact_pr(self):
        self.assertTrue(self.submit()["merged"])
        self.assertEqual(self.run_git("--git-dir", str(self.remote), "rev-parse", "refs/heads/codex/task"), self.head)
        self.submit()
        self.assertEqual(self.creates, 1)

    def test_lost_creation_response_reconciles_without_second_create(self):
        self.submit(lost_creation=True)
        self.assertEqual(self.creates, 1)

    def test_dirty_worktree_is_retained_and_not_pushed(self):
        (self.work / "unrelated.txt").write_text("preserve me")
        with self.assertRaisesRegex(ValueError, "clean isolated worktree"):
            self.submit()
        self.assertEqual((self.work / "unrelated.txt").read_text(), "preserve me")
        self.assertEqual(self.creates, 0)

    def test_single_waiter_lock_is_shared_and_released(self):
        with patch.object(delivery, "ROOT", self.work):
            with delivery.delivery_lock(11, self.head):
                with self.assertRaisesRegex(RuntimeError, "Another waiter"):
                    with delivery.delivery_lock(11, self.head):
                        self.fail("A second waiter acquired the same PR")
            with delivery.delivery_lock(11, self.head):
                pass


if __name__ == "__main__":
    unittest.main()
