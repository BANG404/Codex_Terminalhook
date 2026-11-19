"""Verify the background-only package and protocol without launching a Codex model."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from distribution import MACOS_DESKTOP_LAUNCHER, archive_name, binary_name, desktop_host_binary_name, extract_package, host_platform, mcp_config

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = host_platform()
    archive = ROOT / "dist" / archive_name(target)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terminalhook direct package ") as directory:
        unpacked = Path(directory)
        extract_package(archive, unpacked)
        plugin = unpacked / "plugins/terminalhook"
        info = json.loads((unpacked / "build-info.json").read_text(encoding="utf-8"))
        assert (unpacked / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
        for name in ("README.md", "README.zh-CN.md"):
            assert (unpacked / name).read_bytes() == (ROOT / "examples/package" / name).read_bytes()
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        assert manifest["version"] == info["version"]
        assert "hooks" not in manifest
        assert not (plugin / "hooks").exists(), "Obsolete hooks must not ship"
        assert (unpacked / "Enable-DirectTools.ps1").is_file()
        if target.startswith("macos-"):
            launcher = unpacked / MACOS_DESKTOP_LAUNCHER
            assert os.access(launcher, os.X_OK), "Extracted Desktop launcher is not executable"
            assert launcher.read_bytes() == (ROOT / "scripts" / MACOS_DESKTOP_LAUNCHER).read_bytes()
        assert json.loads((unpacked / "platform.json").read_text())["platform"] == target
        assert json.loads((plugin / ".mcp.json").read_text()) == mcp_config(target)
        binary = plugin / "bin" / binary_name(target)
        assert digest(binary) == digest(ROOT / "target/release" / binary_name(target))
        assert os.access(binary, os.X_OK), "Extracted native binary is not executable"
        assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + manifest["version"]
        desktop_host = plugin / "bin" / desktop_host_binary_name(target)
        assert digest(desktop_host) == digest(ROOT / "target/release" / desktop_host_binary_name(target))
        assert os.access(desktop_host, os.X_OK)
        assert subprocess.check_output([str(desktop_host), "--host-version"], text=True).strip() == "terminalhook-desktop-host " + manifest["version"]
        sys.path.insert(0, str(ROOT / "tests"))
        from test_mcp import Client
        client = Client([str(binary)], cwd=plugin)
        try:
            client.send("tools/list", {}, 2)
            tool_names = sorted(tool["name"] for tool in client.receive()["result"]["tools"])
            assert set(tool_names) == {"check_background", "submit_task", "list_tasks", "get_task",
                                       "cancel_task", "retry_delivery", "watch_tasks"}, tool_names
        finally:
            client.close()
        environment = os.environ.copy()
        environment.update(TERMINALHOOK_EXE=str(binary),
                           TERMINALHOOK_UTF8_INIT=str(plugin / "skills/terminal-wait/scripts/Initialize-Utf8.ps1"),
                           PYTHONUTF8="1")
        environment.pop("TERMINALHOOK_LONG_WAIT", None)
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                               cwd=ROOT, env=environment, text=True, encoding="utf-8", capture_output=True, timeout=120)
        (artifacts / "package-protocol-tests.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
        assert tests.returncode == 0, tests.stdout + tests.stderr
        summary = {"passed": True, "platform": target, "version": manifest["version"], "archive_sha256": digest(archive),
                   "source_revision": info["revision"],
                   "source_tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
                   "binary_sha256": digest(binary), "hook_events": [], "default_route": "direct",
                   "packaged_binary_tests": "passed", "mcp_tools": tool_names,
                   "legacy_process_tools_removed": True, "codex_cli_used": False,
                   "desktop_verified": False, "desktop_status": "protocol verification does not establish desktop model request counts"}
        (artifacts / "package-validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
