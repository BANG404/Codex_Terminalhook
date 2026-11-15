"""Run release waits against the packaged binary on every native platform."""
import os
from pathlib import Path
import subprocess
import sys

from distribution import binary_name

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    environment = dict(os.environ, TERMINALHOOK_LONG_WAIT="1", PYTHONUTF8="1",
                       TERMINALHOOK_EXE=str(ROOT / "dist/package/plugins/terminalhook/bin" / binary_name()))
    for test in ("test_wait_over_default_client_timeout", "test_utf8_interactive_long_wait", "test_powershell_utf8_long_wait"):
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-k", test, "-v"],
                       cwd=ROOT, env=environment, check=True)
