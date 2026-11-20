import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location("promotion_proof", Path(__file__).resolve().parents[1] / "scripts/verify_promotion.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
sys.path.pop(0)


class PromotionProofTests(unittest.TestCase):
    def setUp(self):
        self.tree = "a" * 40
        self.tested = "b" * 40
        self.head = "c" * 40
        self.base = "d" * 40
        self.source = "e" * 40
        self.repository = "owner/repository"
        repo = dict(full_name=self.repository)
        self.pr = dict(number=13, merged_at="now", merge_commit_sha=self.source,
                       base=dict(ref="alpha", repo=repo), head=dict(ref="codex/source", sha=self.head, repo=repo))
        self.run = dict(id=42, run_attempt=2, head_sha=self.head, head_branch="codex/source",
                        event="pull_request", status="completed", conclusion="success", repository=repo,
                        head_repository=repo, path=".github/workflows/ci.yml", pull_requests=[self.pr],
                        html_url="https://github.com/owner/repository/actions/runs/42")
        self.jobs = [dict(name=name, run_id=42, run_attempt=2, head_sha=self.head, status="completed",
                          conclusion="success", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:20Z")
                     for name in proof.REQUIRED_CHECKS]
        self.source_report = dict(passed=True, source_tree=self.tree, source_revision=self.tested,
                                  run_id=42, run_attempt=2)
        self.source_artifact = dict(id=5, name="source-validation", expired=False, created_at="2026-01-01T00:00:10Z")
        self.report = dict(passed=True, source_tree=self.tree, source_revision=self.tested,
                           platforms={target: dict(passed=True, platform=target, source_tree=self.tree,
                                      source_revision=self.tested, archive_sha256="c" * 64, binary_sha256="d" * 64)
                                      for target in proof.PLATFORMS})
        self.artifacts = [dict(id=index + 7, name="terminalhook-" + target, expired=False) for index, target in enumerate(proof.PLATFORMS)]
        self.hub = proof.GitHub(self.repository)
        self.tested_commit = dict(tree=dict(sha=self.tree), parents=[dict(sha=self.base), dict(sha=self.head)])

    def validate(self, *, run=None, runs=None, jobs=None, artifacts=None, report=None, commit=None, refresh=False):
        run = self.run if run is None else run
        runs = [run] if runs is None else runs
        jobs = self.jobs if jobs is None else jobs
        artifacts = [self.source_artifact] if artifacts is None else artifacts
        report = self.source_report if report is None else report
        commit = self.tested_commit if commit is None else commit
        candidate = self.source if refresh else self.head
        event = "workflow_dispatch" if refresh else "pull_request"
        responses = {
            (f"actions/workflows/ci.yml/runs?event={event}&head_sha={candidate}&per_page=100", "workflow_runs"): runs,
            (f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", "jobs"): jobs,
            (f"actions/runs/{run['id']}/artifacts?per_page=100", "artifacts"): artifacts,
        }
        with (patch.object(self.hub, "pages", side_effect=lambda *args: responses[args]),
              patch.object(self.hub, "report", return_value=report),
              patch.object(self.hub, "api", return_value=commit),
              patch.object(proof, "git", return_value=self.base)):
            return self.hub.proof(candidate, self.tree, self.source, None if refresh else self.pr)

    def test_validated_package_keeps_original_artifact_identity(self):
        self.assertEqual(proof.validate_report(self.report, self.tree, self.artifacts), dict(zip(proof.PLATFORMS, range(7, 12))))

    def test_rejects_changed_merge_tree_or_package_evidence(self):
        with self.assertRaises(ValueError):
            proof.promotion_tree(self.tree, "b" * 40)
        for change in (dict(passed=False), dict(source_tree="b" * 40), dict(source_revision=""), dict(platforms={})):
            with self.subTest(change=change), self.assertRaises(ValueError):
                proof.validate_report(dict(self.report, **change), self.tree, self.artifacts)

    def test_expired_or_missing_distribution_cannot_be_promoted(self):
        for artifacts in ([], [dict(self.artifacts[0], expired=True)]):
            with self.assertRaises(ValueError):
                proof.validate_report(self.report, self.tree, artifacts)

    def test_one_missing_or_mismatched_platform_blocks_promotion(self):
        for target in proof.PLATFORMS:
            with self.subTest(target=target), self.assertRaises(ValueError):
                proof.validate_report(self.report, self.tree, [a for a in self.artifacts if a["name"] != "terminalhook-" + target])
            native = dict(self.report["platforms"][target], source_tree="e" * 40)
            report = dict(self.report, platforms=dict(self.report["platforms"], **{target: native}))
            with self.assertRaises(ValueError):
                proof.validate_report(report, self.tree, self.artifacts)

    def test_accepts_same_attempt_source_proof_without_native_artifacts(self):
        found = self.validate()
        self.assertEqual(found["verification"], "alpha_pr_source")
        self.assertEqual(found["source_artifact_id"], 5)
        self.assertEqual(found["verified_run_attempt"], 2)
        self.assertEqual(found["tested_revision"], self.tested)
        run_without_associations = dict(self.run, pull_requests=[])
        self.assertEqual(self.validate(run=run_without_associations), found)

    def test_requires_latest_run_and_both_jobs_from_latest_attempt(self):
        for status in ("in_progress", "completed"):
            newer = dict(self.run, id=43, status=status, conclusion="failure")
            with self.subTest(status=status):
                self.assertIsNone(self.validate(runs=[self.run, newer]))
        self.assertIsNone(self.validate(jobs=self.jobs[:1]))
        for gate in (dict(conclusion="skipped"), dict(run_attempt=1), dict(head_sha=self.base)):
            with self.subTest(gate=gate):
                self.assertIsNone(self.validate(jobs=[self.jobs[0], dict(self.jobs[1], **gate)]))

    def test_requires_ci_workflow_and_association_when_reported(self):
        for identity in (dict(path=".github/workflows/assurance.yml"), dict(event="workflow_dispatch"),
                         dict(pull_requests=[dict(self.pr, number=12)]),
                         dict(pull_requests=[dict(self.pr, base=dict(ref="beta"))])):
            with self.subTest(identity=identity):
                self.assertIsNone(self.validate(run=dict(self.run, **identity)))

    def test_requires_current_report_for_exact_source(self):
        for change in (dict(run_attempt=1), dict(run_id=41), dict(source_tree=self.base), dict(passed=False)):
            with self.subTest(change=change):
                self.assertIsNone(self.validate(report=dict(self.source_report, **change)))
        for change in (dict(expired=True), dict(created_at="2025-12-31T23:59:59Z")):
            with self.subTest(change=change):
                self.assertIsNone(self.validate(artifacts=[dict(self.source_artifact, **change)]))
        self.assertIsNone(self.validate(commit=dict(self.tested_commit, parents=[dict(sha=self.base), dict(sha=self.source)])))
        self.assertIsNone(self.validate(commit=dict(self.tested_commit, tree=dict(sha=self.head))))

    def test_supports_historical_package_proof_for_same_alpha_run(self):
        legacy = dict(self.source_artifact, name="package-verification")
        artifacts = [legacy, *self.artifacts]
        self.assertEqual(self.validate(artifacts=artifacts, report=self.report)["verification"], "legacy_alpha_package")
        self.assertIsNone(self.validate(artifacts=[*artifacts, dict(self.source_artifact, expired=True)], report=self.report))

    def test_accepts_source_refresh_only_from_exact_alpha_revision(self):
        refresh_run = dict(self.run, event="workflow_dispatch", head_sha=self.source, head_branch="alpha", pull_requests=[])
        jobs = [dict(job, name=name, head_sha=self.source) for name, job in zip(proof.REFRESH_CHECKS, self.jobs)]
        report = dict(self.source_report, source_revision=self.source)
        found = self.validate(run=refresh_run, jobs=jobs, report=report, refresh=True)
        self.assertEqual(found["verification"], "alpha_source_refresh")
        self.assertIsNone(self.validate(run=dict(refresh_run, head_branch="beta"), jobs=jobs, report=report, refresh=True))
        self.assertIsNone(self.validate(run=refresh_run, jobs=jobs, report=self.source_report, refresh=True))
        self.assertIsNone(self.validate(run=refresh_run, jobs=jobs[:1], report=report, refresh=True))

    def test_only_merged_alpha_prs_for_the_actual_revision_are_eligible(self):
        pulls = [self.pr, dict(self.pr, merged_at=None), dict(self.pr, merge_commit_sha=self.base),
                 dict(self.pr, base=dict(self.pr["base"], ref="beta"))]
        with patch.object(self.hub, "pages", return_value=pulls):
            self.assertEqual(self.hub.alpha_pull_requests(self.source), [self.pr])

    def test_follows_unchanged_stage_tree_and_alpha_squash_origin(self):
        class GitHub:
            def alpha_pull_requests(self, revision):
                return [dict(number=6, head=dict(sha="alpha-feature"))] if revision == "alpha-squash" else []
            def proof(self, candidate, tree, source, pr=None):
                return dict(verified_run_id=42, source_tree=tree) if candidate == "alpha-feature" else None
        def git(*args):
            if args[0] == "show":
                return "alpha-squash" if args[-1] == "stage" else ""
            return self.tree
        with patch.object(proof, "git", side_effect=git):
            found = proof.find_proof("stage", self.tree, GitHub())
        self.assertEqual(found["verified_run_id"], 42)
        self.assertEqual(found["promotion_source"], "stage")

    def test_changed_ancestor_does_not_supply_validation(self):
        class GitHub:
            def alpha_pull_requests(self, revision):
                return []
            def proof(self, candidate, tree, source, pr=None):
                return dict(verified_run_id=42) if candidate == "different-tree" else None
        def git(*args):
            return "different-tree" if args[0] == "show" else "b" * 40
        with patch.object(proof, "git", side_effect=git), self.assertRaisesRegex(ValueError, "No successful CI"):
            proof.find_proof("stage", self.tree, GitHub())


if __name__ == "__main__":
    unittest.main()
