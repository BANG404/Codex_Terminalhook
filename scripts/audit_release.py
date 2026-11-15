"""Audit the current package against its exact source and protocol evidence."""
import hashlib
import json
from pathlib import Path
import zipfile

from version import build_info

ROOT = Path(__file__).resolve().parents[1]


def main():
    archive = ROOT / "dist/terminalhook-windows-x64.zip"
    info = json.loads((ROOT / "dist/build-info.json").read_text(encoding="utf-8"))
    assert info == build_info(info["channel"]), "Build identity differs from the current source"
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (ROOT / "dist/SHA256SUMS.txt").read_text().split() == [checksum, archive.name]
    evidence = json.loads((ROOT / "artifacts/package-validation.json").read_text(encoding="utf-8"))
    assert evidence["passed"] and evidence["version"] == info["version"]
    assert evidence["archive_sha256"] == checksum, "Protocol evidence belongs to another archive"
    with zipfile.ZipFile(archive) as package:
        assert json.loads(package.read("build-info.json")) == info
        manifest = json.loads(package.read("plugins/terminalhook/.codex-plugin/plugin.json"))
        assert manifest["version"] == info["version"]
        assert package.read("LICENSE") == (ROOT / "LICENSE").read_bytes()
        assert hashlib.sha256(package.read("plugins/terminalhook/bin/terminalhook.exe")).hexdigest() == evidence["binary_sha256"]
    result = {"passed": True, "build": info, "archive_sha256": checksum,
              "packaged_protocol_verified": True,
              "desktop_verified": evidence.get("desktop_verified", False)}
    (ROOT / "artifacts/verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
