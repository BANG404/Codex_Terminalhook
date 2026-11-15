import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("promotion_proof", Path(__file__).resolve().parents[1] / "scripts/verify_promotion.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class PromotionProofTests(unittest.TestCase):
    def setUp(self):
        self.tree = "a" * 40
        self.report = dict(passed=True, source_tree=self.tree, source_revision="b" * 40,
                           archive_sha256="c" * 64, binary_sha256="d" * 64)
        self.artifacts = [dict(id=7, name="terminalhook-windows-x64", expired=False)]

    def test_validated_package_keeps_original_artifact_identity(self):
        self.assertEqual(proof.validate_report(self.report, self.tree, self.artifacts), 7)

    def test_rejects_changed_merge_tree_or_package_evidence(self):
        with self.assertRaises(ValueError):
            proof.promotion_tree(self.tree, "b" * 40)
        for change in (dict(passed=False), dict(source_tree="b" * 40), dict(source_revision=""), dict(archive_sha256="")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                proof.validate_report(dict(self.report, **change), self.tree, self.artifacts)

    def test_expired_or_missing_distribution_cannot_be_promoted(self):
        for artifacts in ([], [dict(self.artifacts[0], expired=True)]):
            with self.assertRaises(ValueError):
                proof.validate_report(self.report, self.tree, artifacts)

    def test_requires_both_successful_jobs(self):
        jobs = [dict(name="Quality checks", conclusion="success"), dict(name="Package verification", conclusion="success")]
        self.assertTrue(proof.verified_jobs(jobs))
        self.assertFalse(proof.verified_jobs(jobs[:1]))
        jobs[1]["conclusion"] = "skipped"
        self.assertFalse(proof.verified_jobs(jobs))

    def test_follows_unchanged_stage_tree_and_alpha_squash_origin(self):
        class GitHub:
            def api(self, path):
                return [dict(merged_at="now", base=dict(ref="alpha"), head=dict(sha="alpha-feature"))] if "alpha-squash" in path else []
            def proof(self, candidate, tree):
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
            def api(self, path):
                return []
            def proof(self, candidate, tree):
                return dict(verified_run_id=42) if candidate == "different-tree" else None
        def git(*args):
            return "different-tree" if args[0] == "show" else "b" * 40
        with patch.object(proof, "git", side_effect=git), self.assertRaisesRegex(ValueError, "No successful CI"):
            proof.find_proof("stage", self.tree, GitHub())


if __name__ == "__main__":
    unittest.main()
