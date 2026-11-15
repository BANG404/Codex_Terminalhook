"""Assemble only complete, consistently verified native platform distributions."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from distribution import PLATFORMS, archive_name, binary_name, package_files

ROOT = Path(__file__).resolve().parents[1]


def assemble(root=ROOT, tree=None):
    root = Path(root)
    dist = root / "dist"
    tree = tree or subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    reports = {}
    identity = None
    sources = []
    for target in PLATFORMS:
        source = dist / "platforms" / ("terminalhook-" + target)
        info = json.loads((source / "build-info.json").read_text(encoding="utf-8"))
        if identity is None:
            identity = info
        if info != identity:
            raise ValueError("Platform source identities differ")
        report = json.loads((source / "package-validation.json").read_text(encoding="utf-8"))
        install = json.loads((source / "git-marketplace-validation.json").read_text(encoding="utf-8"))
        archive = source / archive_name(target)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if (not report.get("passed") or report.get("platform") != target or report.get("source_tree") != tree
                or report.get("source_revision") != info["revision"] or report.get("version") != info["version"]
                or report.get("archive_sha256") != checksum or report.get("packaged_binary_tests") != "passed"):
            raise ValueError("Native package evidence differs from source or archive")
        if (not install.get("passed") or install.get("platform") != target or install.get("version") != info["version"]
                or install.get("binary_sha256") != report.get("binary_sha256") or not install.get("installed_mcp_launch")):
            raise ValueError("Native installation evidence is incomplete")
        files = package_files(archive, info, target)
        binary = files["plugins/terminalhook/bin/" + binary_name(target)]
        if hashlib.sha256(binary).hexdigest() != report.get("binary_sha256"):
            raise ValueError("Native binary digest differs from protocol evidence")
        if (source / "SHA256SUMS.txt").read_text() != checksum + "  " + archive.name + "\n":
            raise ValueError("Native archive checksum differs")
        reports[target] = report
        sources.append(archive)
    # Validate every platform before producing publishable aggregate metadata.
    for path in sources:
        shutil.copyfile(path, dist / path.name)
    (dist / "build-info.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    (dist / "SHA256SUMS.txt").write_text("".join(reports[target]["archive_sha256"] + "  " + archive_name(target) + "\n" for target in PLATFORMS), encoding="utf-8")
    summary = {"passed": True, "source_tree": tree, "source_revision": identity["revision"], "platforms": reports}
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "package-validation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    assemble()
