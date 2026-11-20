"""Shared platform identities and safe, executable-preserving ZIP extraction."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import platform
import stat
import zipfile

PLATFORMS = ("windows-x64", "linux-x64", "linux-arm64", "macos-x64", "macos-arm64")
CHANNELS = {"production": "marketplace", "alpha": "marketplace-alpha",
            "beta": "marketplace-beta", "rc": "marketplace-rc"}
PLUGIN_ICON = "assets/terminalhook-logo.svg"
COMMON_FILES = {
    ".agents/plugins/marketplace.json", "build-info.json", "platform.json", "LICENSE",
    "README.md", "README.zh-CN.md", "install.ps1", "Enable-DirectTools.ps1",
    "install.py", "install.sh", "distribution.py", "Start-DesktopSharedHost.ps1",
    "plugins/terminalhook/.codex-plugin/plugin.json", "plugins/terminalhook/.mcp.json",
    "plugins/terminalhook/" + PLUGIN_ICON,
    "plugins/terminalhook/skills/terminal-wait/SKILL.md",
    "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1",
}
MACOS_DESKTOP_LAUNCHER = "Start-DesktopSharedHost.sh"


def host_platform(system=None, machine=None):
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    operating_system = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(system)
    architecture = {"AMD64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    target = f"{operating_system}-{architecture}"
    if target not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {system}/{machine}")
    return target


def binary_name(target=None):
    return "terminalhook.exe" if (target or host_platform()).startswith("windows-") else "terminalhook"


def desktop_host_binary_name(target=None):
    return binary_name(target).replace("terminalhook", "terminalhook-desktop-host")


def archive_name(target=None):
    return f"terminalhook-{target or host_platform()}.zip"


def marketplace_branch(channel, target=None):
    target = target or host_platform()
    if target not in PLATFORMS:
        raise ValueError("Unsupported distribution target")
    branch = CHANNELS[channel]
    return branch if target == "windows-x64" else f"{branch}-{target}"


def executable_path(name):
    return name.endswith(".sh") or name in {"plugins/terminalhook/bin/terminalhook", "plugins/terminalhook/bin/terminalhook-desktop-host"}


def git_mode(name):
    return "100755" if executable_path(name) else "100644"


def mcp_config(target):
    server = {"command": "cmd.exe", "args": ["/d", "/c", "bin\\terminalhook.exe"]} if target.startswith("windows-") else {
        "command": "./bin/terminalhook", "args": []}
    server.update(cwd=".", startup_timeout_sec=10, tool_timeout_sec=660,
                  env_vars=["CODEX_HOME", "CODEX_APP_RUNTIME_CLI_PATH", "TERMINALHOOK_CODEX_BIN", "TERMINALHOOK_CODEX_SOCKET"])
    return {"mcpServers": {"terminalhook": server}}


def expected_files(target):
    if target not in PLATFORMS:
        raise ValueError("Unsupported distribution target")
    files = COMMON_FILES | {"plugins/terminalhook/bin/" + name for name in (binary_name(target), desktop_host_binary_name(target))}
    if target.startswith("macos-"):
        files.add(MACOS_DESKTOP_LAUNCHER)
    return files


def package_files(archive, info, target="windows-x64"):
    """Validate the exact frozen package before publishing any release or Git ref."""
    required = expected_files(target)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if {entry.filename for entry in entries} != required or len(entries) != len(required):
            raise ValueError("Unexpected, missing, or duplicate distribution files")
        for entry in entries:
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_IFMT(mode) != stat.S_IFREG:
                raise ValueError("Distribution must contain only regular files")
            if mode & 0o777 != int(git_mode(entry.filename)[-3:], 8):
                raise ValueError("Distribution executable permissions differ")
        files = {entry.filename: package.read(entry) for entry in entries}
    if json.loads(files["build-info.json"]) != info:
        raise ValueError("Packaged source identity differs")
    if json.loads(files["platform.json"]) != {"platform": target}:
        raise ValueError("Packaged platform differs")
    manifest = json.loads(files["plugins/terminalhook/.codex-plugin/plugin.json"])
    if manifest["name"] != "terminalhook" or manifest["version"] != info["version"] or "hooks" in manifest:
        raise ValueError("Packaged plugin identity differs")
    interface = manifest.get("interface", {})
    if any(interface.get(field) != "./" + PLUGIN_ICON for field in ("logo", "composerIcon")):
        raise ValueError("Packaged plugin icon paths differ")
    catalog = json.loads(files[".agents/plugins/marketplace.json"])
    if (catalog["name"] != "terminalhook-local" or len(catalog["plugins"]) != 1
            or catalog["plugins"][0]["name"] != "terminalhook"
            or catalog["plugins"][0]["source"] != {"source": "local", "path": "./plugins/terminalhook"}):
        raise ValueError("Packaged marketplace identity differs")
    if json.loads(files["plugins/terminalhook/.mcp.json"]) != mcp_config(target):
        raise ValueError("Packaged MCP launch configuration differs")
    magic = {"windows": (b"MZ",), "linux": (b"\x7fELF",),
             "macos": (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")}[target.split("-")[0]]
    for name in (binary_name(target), desktop_host_binary_name(target)):
        if not files["plugins/terminalhook/bin/" + name].startswith(magic):
            raise ValueError("Missing native binary or wrong binary format")
    return files


def release_assets(dist, info):
    """Require the complete platform set and an exact checksum manifest."""
    dist = Path(dist)
    archives = [dist / archive_name(target) for target in PLATFORMS]
    sums = "".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in archives)
    if (dist / "SHA256SUMS.txt").read_text(encoding="utf-8") != sums:
        raise ValueError("Release checksums do not cover the exact platform set")
    if json.loads((dist / "build-info.json").read_text(encoding="utf-8")) != info:
        raise ValueError("Release source identity differs")
    for target, path in zip(PLATFORMS, archives):
        package_files(path, info, target)
    return [*archives, dist / "SHA256SUMS.txt", dist / "build-info.json"]


def extract_package(archive, destination):
    """Reject traversal/symlinks and restore Unix executable bits Python's extractor drops."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as package:
        seen = set()
        for entry in package.infolist():
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or ":" in entry.filename or entry.filename in seen
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.is_dir()):
                raise ValueError("Unsafe or duplicate package entry")
            seen.add(entry.filename)
            output = destination.joinpath(*path.parts)
            if not output.resolve().is_relative_to(destination):
                raise ValueError("Package entry escapes destination")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(package.read(entry))
            if os.name != "nt":
                output.chmod(0o755 if executable_path(entry.filename) else 0o644)
