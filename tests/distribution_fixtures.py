"""Frozen package fixtures for integrity and publication tests; never executed."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from distribution import PLATFORMS, archive_name, binary_name, desktop_host_binary_name, expected_files, git_mode, mcp_config
sys.path.pop(0)


def package_data(info, target="windows-x64"):
    files = {name: b"runtime asset" for name in expected_files(target)}
    files["build-info.json"] = json.dumps(info).encode()
    files["platform.json"] = json.dumps({"platform": target}).encode()
    files["plugins/terminalhook/.codex-plugin/plugin.json"] = json.dumps({"name": "terminalhook", "version": info["version"]}).encode()
    files[".agents/plugins/marketplace.json"] = json.dumps({"name": "terminalhook-local", "plugins": [
        {"name": "terminalhook", "source": {"source": "local", "path": "./plugins/terminalhook"}}]}).encode()
    files["plugins/terminalhook/.mcp.json"] = json.dumps(mcp_config(target)).encode()
    magic = b"MZ" if target.startswith("windows") else b"\x7fELF" if target.startswith("linux") else b"\xcf\xfa\xed\xfe"
    files["plugins/terminalhook/bin/" + binary_name(target)] = magic + b" verified binary"
    files["plugins/terminalhook/bin/" + desktop_host_binary_name(target)] = magic + b" verified desktop host"
    return files


def archive_data(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = int(git_mode(name), 8) << 16
            package.writestr(entry, data)
    buffer.seek(0)
    return buffer


def write_release(root, info):
    files = {archive_name(target): archive_data(package_data(info, target)).getvalue() for target in PLATFORMS}
    files["SHA256SUMS.txt"] = "".join(hashlib.sha256(files[archive_name(t)]).hexdigest() + "  " + archive_name(t) + "\n" for t in PLATFORMS).encode()
    files["build-info.json"] = json.dumps(info).encode()
    (root / "dist").mkdir(exist_ok=True)
    for name, data in files.items():
        (root / "dist" / name).write_bytes(data)
    return files
