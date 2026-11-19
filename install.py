"""Install the native marketplace using Codex's configuration editor.

Python 3.11+ is needed only for installation. No model turn is created.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from distribution import binary_name, host_platform, marketplace_branch

MARKETPLACE = "terminalhook-local"
PLUGIN = "terminalhook@" + MARKETPLACE


def cli(*args):
    result = subprocess.run(["codex", *args], capture_output=True, text=True, encoding="utf-8", timeout=90)
    if result.returncode:
        raise RuntimeError(f"Codex command failed: {' '.join(args[:3])}")
    return json.loads(result.stdout)


def configure(read_marketplace=False):
    config_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    config_root.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(["codex", "app-server", "--listen", "stdio://"], cwd=config_root,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
    responses = queue.Queue()

    def reader():
        try:
            for line in server.stdout:
                responses.put(json.loads(line))
        finally:
            responses.put(None)

    stdout_thread = threading.Thread(target=reader, daemon=True)
    stderr_thread = threading.Thread(target=server.stderr.read, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def request(ident, method, params):
        server.stdin.write(json.dumps(dict(id=ident, method=method, params=params)) + "\n")
        server.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            value = responses.get(timeout=max(0.01, deadline - time.monotonic()))
            if value is None:
                raise RuntimeError("Codex configuration service closed")
            if value.get("id") == ident:
                if "error" in value:
                    raise RuntimeError(f"Codex rejected configuration request: {method}")
                return value["result"]

    try:
        request(1, "initialize", {"clientInfo": {"name": "terminalhook-installer", "version": "0.4.0"}})
        server.stdin.write('{"method":"initialized"}\n')
        server.stdin.flush()
        snapshot = request(2, "config/read", {"includeLayers": True})
        layers = [layer for layer in snapshot["layers"] if layer["name"]["type"] == "user" and not layer["name"].get("profile")]
        if len(layers) != 1:
            raise RuntimeError("Cannot identify base user config layer")
        layer = layers[0]
        if read_marketplace:
            return layer["config"].get("marketplaces", {}).get(MARKETPLACE)
        existing = layer["config"].get("features", {}).get("code_mode", {}).get("direct_only_tool_namespaces", [])
        if not isinstance(existing, list):
            raise ValueError("Existing direct-only namespaces must be an array")
        edits = []
        if "mcp__terminalhook" not in existing:
            edits.append({"keyPath": "features.code_mode.direct_only_tool_namespaces",
                          "value": [*existing, "mcp__terminalhook"], "mergeStrategy": "replace"})
        server_policy = layer["config"].get("plugins", {}).get(PLUGIN, {}).get("mcp_servers", {}).get("terminalhook", {})
        submit_policy = server_policy.get("tools", {}).get("submit_task", {})
        if submit_policy.get("approval_mode") is None and server_policy.get("default_tools_approval_mode") is None:
            edits.append({"keyPath": f"plugins.{PLUGIN}.mcp_servers.terminalhook.tools.submit_task.approval_mode",
                          "value": "approve", "mergeStrategy": "replace"})
        if edits:
            request(3, "config/batchWrite", {"filePath": layer["name"]["file"],
                                             "expectedVersion": layer["version"], "edits": edits})
    finally:
        server.stdin.close()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        server.stdout.close()
        server.stderr.close()


def install(source=None, channel="production", git_source="https://github.com/BANG404/Codex_Terminalhook.git"):
    target = host_platform()
    if source:
        root = Path(source).resolve(strict=True)
        if not (root / "plugins/terminalhook/bin" / binary_name(target)).is_file():
            raise ValueError("Missing native binary; use a package for this platform")
        if json.loads((root / "platform.json").read_text(encoding="utf-8"))["platform"] != target:
            raise ValueError("Package platform differs from this host")
        arguments = [str(root)]
    else:
        arguments = [git_source, "--ref", marketplace_branch(channel, target)]
    catalog = cli("plugin", "marketplace", "list", "--json")
    existing = [entry for entry in catalog["marketplaces"] if entry["name"] == MARKETPLACE]
    if len(existing) > 1:
        raise ValueError("Ambiguous marketplace registration")
    restore = None
    if existing:
        # Catalog metadata can be nonempty for local sources too. The user config
        # is authoritative and preserves Git refs/sparse paths as well as local roots.
        registration = configure(read_marketplace=True)
        if not registration or not isinstance(registration.get("source"), str):
            raise ValueError("Cannot read the previous marketplace registration")
        if registration.get("source_type") == "git":
            restore = [registration["source"]]
            if registration.get("ref"):
                restore += ["--ref", registration["ref"]]
            for path in registration.get("sparse_paths") or []:
                restore += ["--sparse", path]
        elif registration.get("source_type") in (None, "local"):
            restore = [registration["source"]]
        else:
            raise ValueError("Unsupported previous marketplace source")
        cli("plugin", "marketplace", "remove", MARKETPLACE, "--json")
    added = None
    try:
        added = cli("plugin", "marketplace", "add", *arguments, "--json")
        if added["marketplaceName"] != MARKETPLACE:
            raise ValueError("Unexpected marketplace identity")
        installed = Path(added["installedRoot"])
        if not (installed / "plugins/terminalhook/bin" / binary_name(target)).is_file():
            raise ValueError("Marketplace does not contain the compiled native binary")
        if json.loads((installed / "platform.json").read_text(encoding="utf-8"))["platform"] != target:
            raise ValueError("Marketplace platform differs from this host")
        cli("plugin", "add", PLUGIN, "--json")
        configure()
    except Exception:
        if added and not added.get("alreadyAdded"):
            cli("plugin", "marketplace", "remove", added["marketplaceName"], "--json")
        if restore:
            cli("plugin", "marketplace", "add", *restore, "--json")
        raise
    print(f"TerminalHook installed for {target}. Open a new Codex chat to load the tools.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--source", type=Path)
    group.add_argument("--channel", choices=("production", "alpha", "beta", "rc"), default="production")
    parser.add_argument("--git-source", default="https://github.com/BANG404/Codex_Terminalhook.git")
    args = parser.parse_args()
    if not shutil.which("codex"):
        parser.error("Codex CLI must be installed")
    install(args.source, args.channel, args.git_source)


if __name__ == "__main__":
    main()
