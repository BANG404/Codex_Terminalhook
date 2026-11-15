"""Verify the packaged Stop-hook plugin and protocol without launching Codex CLI."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = ROOT / "dist/terminalhook-windows-x64.zip"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terminalhook stop hook package ") as directory:
        unpacked = Path(directory)
        with zipfile.ZipFile(archive) as package:
            package.extractall(unpacked)
        plugin = unpacked / "plugins/terminalhook"
        info = json.loads((unpacked / "build-info.json").read_text(encoding="utf-8"))
        assert (unpacked / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        assert manifest["version"] == info["version"]
        hooks = json.loads((plugin / manifest["hooks"]).read_text(encoding="utf-8"))["hooks"]
        assert set(hooks) == {"PreToolUse", "Stop", "PreModelRequest", "PostModelRequest"}
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    assert handler["type"] == "mcp_tool" and handler["server"] == "terminalhook"
                    assert not handler.get("async", False)
        assert hooks["Stop"][0]["hooks"][0]["tool"] == "await_tasks"
        binary = plugin / "bin/terminalhook.exe"
        assert digest(binary) == digest(ROOT / "target/release/terminalhook.exe")
        assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + manifest["version"]
        environment = os.environ.copy()
        environment.update(TERMINALHOOK_EXE=str(binary),
                           TERMINALHOOK_UTF8_INIT=str(plugin / "skills/terminal-wait/scripts/Initialize-Utf8.ps1"),
                           TERMINALHOOK_AUDIT_DIR=str(unpacked / "audit"), PYTHONUTF8="1")
        environment.pop("TERMINALHOOK_LONG_WAIT", None)
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                               cwd=ROOT, env=environment, text=True, encoding="utf-8", capture_output=True, timeout=45)
        (artifacts / "stop-hook-protocol-tests.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
        assert tests.returncode == 0, tests.stdout + tests.stderr
        summary = {"passed": True, "version": manifest["version"], "archive_sha256": digest(archive),
                   "binary_sha256": digest(binary), "hook_events": list(hooks),
                   "packaged_binary_tests": "passed", "codex_cli_used": False,
                   "desktop_verified": False, "desktop_status": "pending desktop hook trust and actual long-task run"}
        (artifacts / "stop-hook-validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
