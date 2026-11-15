"""Install the plugin into an isolated Codex home and inspect resolved configuration.

No changes to the user's Codex config or login. Also checks zip dotfiles and tests
the cached binary after installation, not just the development build.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    archive = ROOT / "dist/terminalhook-windows-x64.zip"
    with zipfile.ZipFile(archive) as package:
        members = set(package.namelist())
        for required in ["plugins/terminalhook/.mcp.json", "plugins/terminalhook/.codex-plugin/plugin.json", "plugins/terminalhook/bin/terminalhook.exe", "plugins/terminalhook/hooks/hooks.json", "plugins/terminalhook/skills/terminal-wait/SKILL.md", "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1", ".agents/plugins/marketplace.json", "install.ps1"]:
            assert required in members, (required, members)
    codex = shutil.which("codex")
    assert codex, "codex not found on PATH"
    with tempfile.TemporaryDirectory(prefix="terminalhook plugin test ", dir=artifacts) as directory:
        extracted = Path(directory) / "unpacked marketplace"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        expected_version = json.loads((extracted / "build-info.json").read_text(encoding="utf-8"))["version"]
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(Path(directory) / "isolated codex home")
        Path(environment["CODEX_HOME"]).mkdir()
        def cli(*args):
            result = subprocess.run([codex, *args], env=environment, cwd=ROOT, capture_output=True,
                                    text=True, encoding="utf-8", timeout=60)
            assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result.stdout
        install = subprocess.run(["pwsh", "-NoProfile", "-File", str(extracted / "install.ps1")], cwd=extracted, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert install.returncode == 0, install.stdout + install.stderr
        cli("plugin", "list", "--json")
        servers = json.loads(cli("mcp", "list", "--json"))
        server = next(s for s in servers if "terminalhook" in s["name"])
        assert server["enabled"], server
        assert server["tool_timeout_sec"] == 660, server
        working_directory = Path(server["transport"]["cwd"])
        assert working_directory.is_absolute(), server
        assert working_directory.is_relative_to(directory), server
        binary = working_directory / "bin/terminalhook.exe"
        assert binary.is_file(), binary
        cached_init = working_directory / "skills/terminal-wait/scripts/Initialize-Utf8.ps1"
        assert cached_init.read_bytes() == (ROOT / "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1").read_bytes()
        assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + expected_version
        # Actually exercise the cached binary; cwd and path contain spaces.
        environment["TERMINALHOOK_EXE"] = str(binary)
        environment["TERMINALHOOK_UTF8_INIT"] = str(cached_init)
        environment.pop("TERMINALHOOK_LONG_WAIT", None)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        summary = {"passed": True, "version":expected_version, "archive_members": sorted(members), "install": install.stdout,
                   "resolved_mcp": server, "cached_binary_tests": result.stderr}
        (artifacts / "plugin-install-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"passed": True, "tool_timeout_sec": server["tool_timeout_sec"],
                          "resolved_cwd_is_cache": True, "cached_binary_protocol_tests": "passed", "zip_dotfiles": "present"}, indent=2))


if __name__ == "__main__":
    main()
