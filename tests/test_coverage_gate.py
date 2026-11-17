"""Regressions for missing, malformed, and below-threshold coverage evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_coverage", ROOT / "scripts/check_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class CoverageGateTests(unittest.TestCase):
    def report(self):
        return {"data": [{"files": [
            {"filename": str(ROOT / name), "summary": {"lines": {"count": 100, "covered": 90}}}
            for name in coverage.CORE_FILES]}]}

    def test_accepts_threshold_for_every_core_module(self):
        self.assertEqual(set(coverage.validate(self.report())), set(coverage.CORE_FILES))

    def test_one_module_cannot_hide_behind_aggregate_coverage(self):
        report = self.report()
        report["data"][0]["files"][0]["summary"]["lines"] = {"count": 10000, "covered": 8999}
        with self.assertRaises(ValueError):
            coverage.validate(report)

    def test_missing_duplicate_and_invalid_counts_fail_closed(self):
        report = self.report()
        missing = copy.deepcopy(report)
        missing["data"][0]["files"].pop()
        duplicate = copy.deepcopy(report)
        duplicate["data"][0]["files"].append(copy.deepcopy(duplicate["data"][0]["files"][0]))
        for invalid in (missing, duplicate, {"data": []}):
            with self.assertRaises(ValueError):
                coverage.validate(invalid)
        for total, covered in ((0, 0), (100, -1), (100, 101), (100, 90.0), (True, 1)):
            invalid = self.report()
            invalid["data"][0]["files"][0]["summary"]["lines"] = {"count": total, "covered": covered}
            with self.assertRaises(ValueError):
                coverage.validate(invalid)
