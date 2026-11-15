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

from distribution import archive_name, binary_name, extract_package, host_platform

ROOT = Path(__file__).resolve().parents[1]


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    target = host_platform()
    native_binary = "plugins/terminalhook/bin/" + binary_name(target)
    archive = ROOT / "dist" / archive_name(target)
    with zipfile.ZipFile(archive) as package:
        members = set(package.namelist())
        for required in ["plugins/terminalhook/.mcp.json", "plugins/terminalhook/.codex-plugin/plugin.json", native_binary, "plugins/terminalhook/skills/terminal-wait/SKILL.md", "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1", ".agents/plugins/marketplace.json", "install.ps1", "Enable-DirectTools.ps1", "install.py", "install.sh", "distribution.py", "platform.json"]:
            assert required in members, (required, members)
        assert not any("/hooks/" in member for member in members)
    codex = shutil.which("codex")
    assert codex, "codex not found on PATH"
    with tempfile.TemporaryDirectory(prefix="terminalhook plugin test ", dir=artifacts) as directory:
        extracted = Path(directory) / "unpacked marketplace"
        extract_package(archive, extracted)
        expected_version = json.loads((extracted / "build-info.json").read_text(encoding="utf-8"))["version"]
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(Path(directory) / "isolated codex home")
        Path(environment["CODEX_HOME"]).mkdir()
        config_path = Path(environment["CODEX_HOME"]) / "config.toml"
        config_path.write_text('# 保留原配置\n[features.code_mode]\ndirect_only_tool_namespaces = ["mcp__existing"]\n', encoding="utf-8")
        def cli(*args):
            result = subprocess.run([codex, *args], env=environment, cwd=ROOT, capture_output=True,
                                    text=True, encoding="utf-8", timeout=60)
            assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result.stdout
        # Simulate an existing local installation registered at a different path.
        previous_source = Path(directory) / "previous marketplace location"
        extract_package(archive, previous_source)
        cli("plugin", "marketplace", "add", str(previous_source), "--json")
        cli("plugin", "add", "terminalhook@terminalhook-local", "--json")
        def installer(source):
            return (["pwsh", "-NoProfile", "-File", str(extracted / "install.ps1"), "-Source", str(source)] if os.name == "nt"
                    else ["sh", str(extracted / "install.sh"), "--source", str(source)])
        install = subprocess.run(installer(extracted), cwd=extracted, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert install.returncode == 0, install.stdout + install.stderr
        installed_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert installed_config["features"]["code_mode"]["direct_only_tool_namespaces"] == ["mcp__existing", "mcp__terminalhook"]
        assert "# 保留原配置" in config_path.read_text(encoding="utf-8")
        config_bytes = config_path.read_bytes()
        setup_command = (["pwsh", "-NoProfile", "-File", str(extracted / "Enable-DirectTools.ps1")] if os.name == "nt"
                         else [sys.executable, "-c", "from install import configure; configure()"])
        setup = subprocess.run(setup_command, cwd=extracted,
                               env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert setup.returncode == 0, setup.stdout + setup.stderr
        assert config_path.read_bytes() == config_bytes, "Repeated setup changed user config"
        assert os.path.samefile(installed_config["marketplaces"]["terminalhook-local"]["source"], extracted)
        # If the new source is invalid, source migration must restore the old one.
        broken_source = Path(directory) / "broken replacement marketplace"
        shutil.copytree(extracted, broken_source)
        (broken_source / ".agents/plugins/marketplace.json").write_text("invalid json", encoding="utf-8")
        failed = subprocess.run(installer(broken_source),
                                cwd=extracted, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert failed.returncode != 0, "Invalid marketplace source was accepted"
        restored = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert os.path.samefile(restored["marketplaces"]["terminalhook-local"]["source"], extracted)
        assert restored["plugins"]["terminalhook@terminalhook-local"]["enabled"]
        assert restored["features"]["code_mode"]["direct_only_tool_namespaces"] == ["mcp__existing", "mcp__terminalhook"]
        cli("plugin", "list", "--json")
        servers = json.loads(cli("mcp", "list", "--json"))
        server = next(s for s in servers if "terminalhook" in s["name"])
        assert server["enabled"], server
        assert server["tool_timeout_sec"] == 660, server
        working_directory = Path(server["transport"]["cwd"])
        assert working_directory.is_absolute(), server
        assert working_directory.is_relative_to(directory), server
        binary = working_directory / "bin" / binary_name(target)
        assert binary.is_file(), binary
        cached_init = working_directory / "skills/terminal-wait/scripts/Initialize-Utf8.ps1"
        assert cached_init.read_bytes() == (ROOT / "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1").read_bytes()
        assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + expected_version
        # Actually exercise the cached binary; cwd and path contain spaces.
        environment["TERMINALHOOK_EXE"] = str(binary)
        environment["TERMINALHOOK_UTF8_INIT"] = str(cached_init)
        environment.pop("TERMINALHOOK_LONG_WAIT", None)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        summary = {"passed": True, "version":expected_version, "archive_members": sorted(members), "install": install.stdout,
                   "resolved_mcp": server, "cached_binary_tests": result.stderr}
        (artifacts / "plugin-install-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"passed": True, "tool_timeout_sec": server["tool_timeout_sec"],
                          "resolved_cwd_is_cache": True, "cached_binary_protocol_tests": "passed", "zip_dotfiles": "present"}, indent=2))


if __name__ == "__main__":
    main()
