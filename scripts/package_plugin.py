"""Package the frozen Windows build with its channel identity and MIT license."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    dist = ROOT / "dist"
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    binary = ROOT / "target/release/terminalhook.exe"
    assert subprocess.check_output([str(binary), "--version"], text=True).strip() == "terminalhook " + info["version"]
    destination = dist / "package"
    # Remove only this task-owned staging tree after checking its resolved parent.
    assert destination.resolve().parent == dist.resolve() and not destination.is_symlink()
    if destination.exists():
        shutil.rmtree(destination)
    plugin = destination / "plugins/terminalhook"
    shutil.copytree(ROOT / "plugins/terminalhook", plugin, ignore=shutil.ignore_patterns("bin", "__pycache__"))
    (plugin / "bin").mkdir()
    shutil.copyfile(binary, plugin / "bin/terminalhook.exe")
    manifest_path = plugin / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = info["version"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (destination / ".agents/plugins").mkdir(parents=True)
    for source, target in ((".agents/plugins/marketplace.json", ".agents/plugins/marketplace.json"),
                           ("scripts/install.ps1", "install.ps1"), ("scripts/Enable-DirectTools.ps1", "Enable-DirectTools.ps1"), ("examples/PACKAGE_README.md", "README.md"),
                           ("LICENSE", "LICENSE"), ("dist/build-info.json", "build-info.json")):
        shutil.copyfile(ROOT / source, destination / target)
    archive = dist / "terminalhook-windows-x64.zip"
    stamp = datetime.datetime.fromtimestamp(max(315532800, info["sourceDateEpoch"]), datetime.timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(destination).as_posix(), stamp)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                package.writestr(entry, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / "SHA256SUMS.txt").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"version": info["version"], "archive": str(archive), "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
