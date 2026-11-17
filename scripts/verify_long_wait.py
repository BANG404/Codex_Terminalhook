"""Verify mandatory long background tasks against the final native package."""
import json
import os
from pathlib import Path
import sys
import unittest

from distribution import binary_name

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.environ.update(TERMINALHOOK_LONG_WAIT="1", PYTHONUTF8="1",
                      TERMINALHOOK_EXE=str(ROOT / "dist/package/plugins/terminalhook/bin" / binary_name()))
    sys.path.insert(0, str(ROOT / "tests"))
    from test_background import BackgroundTests
    names = ["test_background_long_wait_survives_disconnect", "test_background_utf8_long_wait"]
    if os.name == "nt":
        names.append("test_background_powershell_utf8_long_wait")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(BackgroundTests(name) for name in names))
    passed = result.wasSuccessful() and result.testsRun == len(names) and not result.skipped
    report = {"passed": passed, "tests": names, "tests_run": result.testsRun,
              "skipped": result.skipped, "route": "submit_task and original-chat completion delivery"}
    output = ROOT / "artifacts/long-wait-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise SystemExit("Mandatory background long-wait checks failed or skipped")


if __name__ == "__main__":
    main()
