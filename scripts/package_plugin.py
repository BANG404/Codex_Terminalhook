"""Package a native build with reproducible metadata and executable permissions."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from distribution import archive_name, binary_name, executable_path, host_platform, mcp_config

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = host_platform()
    dist = ROOT / "dist"
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    binary = ROOT / "target/release" / binary_name(target)
    assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + info["version"]
    destination = dist / "package"
    # Remove only this task-owned staging tree after checking its resolved parent.
    assert destination.resolve().parent == dist.resolve() and not destination.is_symlink()
    if destination.exists():
        shutil.rmtree(destination)
    plugin = destination / "plugins/terminalhook"
    shutil.copytree(ROOT / "plugins/terminalhook", plugin, ignore=shutil.ignore_patterns("bin", "__pycache__"))
    (plugin / "bin").mkdir()
    shutil.copyfile(binary, plugin / "bin" / binary_name(target))
    manifest_path = plugin / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = info["version"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (plugin / ".mcp.json").write_text(json.dumps(mcp_config(target), indent=2) + "\n", encoding="utf-8")
    (destination / ".agents/plugins").mkdir(parents=True)
    for source, name in ((".agents/plugins/marketplace.json", ".agents/plugins/marketplace.json"),
                           ("scripts/install.ps1", "install.ps1"), ("scripts/Enable-DirectTools.ps1", "Enable-DirectTools.ps1"),
                           ("scripts/install.py", "install.py"), ("scripts/install.sh", "install.sh"),
                           ("scripts/distribution.py", "distribution.py"),
                           ("examples/package/README.md", "README.md"), ("examples/package/README.zh-CN.md", "README.zh-CN.md"),
                           ("LICENSE", "LICENSE"), ("dist/build-info.json", "build-info.json")):
        shutil.copyfile(ROOT / source, destination / name)
    (destination / "platform.json").write_text(json.dumps({"platform": target}) + "\n", encoding="utf-8")
    archive = dist / archive_name(target)
    stamp = datetime.datetime.fromtimestamp(max(315532800, info["sourceDateEpoch"]), datetime.timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                name = path.relative_to(destination).as_posix()
                entry = zipfile.ZipInfo(name, stamp)
                entry.create_system = 3
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (0o100755 if executable_path(name) else 0o100644) << 16
                package.writestr(entry, path.read_bytes())
                if executable_path(name):
                    path.chmod(0o755)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / "SHA256SUMS.txt").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"platform": target, "version": info["version"], "archive": str(archive), "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
