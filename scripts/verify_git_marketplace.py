"""Verify local-to-Git migration and official upgrades against a loopback Git server.

Uses a temporary Codex home, no credentials, external repository writes, or model turns.
"""
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

from distribution import archive_name, binary_name, extract_package, host_platform, marketplace_branch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "terminalhook@terminalhook-local"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    target = host_platform()
    native_binary = "plugins/terminalhook/bin/" + binary_name(target)
    stable_branch = marketplace_branch("production", target)
    beta_branch = marketplace_branch("beta", target)
    archive = ROOT / "dist" / archive_name(target)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    codex = shutil.which("codex")
    assert codex, "codex not found"
    with tempfile.TemporaryDirectory(prefix="terminalhook git marketplace ", dir=artifacts) as directory:
        task_root = Path(directory)
        package = task_root / "release package"
        extract_package(archive, package)
        info = json.loads((package / "build-info.json").read_text(encoding="utf-8"))
        environment = os.environ.copy()
        environment.update(CODEX_HOME=str(task_root / "codex home"), PYTHONUTF8="1")
        config = Path(environment["CODEX_HOME"]) / "config.toml"
        config.parent.mkdir()
        config.write_text('# Preserve settings\n[features.code_mode]\ndirect_only_tool_namespaces = ["mcp__existing"]\n', encoding="utf-8")

        def command(args, cwd=task_root, check=True):
            result = subprocess.run(args, cwd=cwd, env=environment, text=True,
                                    encoding="utf-8", capture_output=True, timeout=90)
            if check:
                assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result

        def cli(*args):
            return json.loads(command([codex, *args, "--json"]).stdout)

        def git(*args, cwd=task_root):
            return command(["git", "-c", "core.autocrlf=false", "-c", "user.name=TerminalHook Test",
                            "-c", "user.email=test@example.invalid", *args], cwd).stdout.strip()

        def install(*args, check=True):
            if os.name == "nt":
                launch = ["pwsh", "-NoProfile", "-File", str(package / "install.ps1")]
            else:
                launch = ["sh", str(package / "install.sh")]
                args = tuple({"-Source": "--source", "-GitSource": "--git-source", "-Channel": "--channel"}.get(arg, arg) for arg in args)
            return command([*launch, *args], check=check)

        def snapshot():
            return tomllib.loads(config.read_text(encoding="utf-8"))

        def cached_binary():
            servers = cli("mcp", "list")
            server = next(s for s in servers if "terminalhook" in s["name"])
            assert server["enabled"] and server["tool_timeout_sec"] == 660
            return Path(server["transport"]["cwd"]) / "bin" / binary_name(target)

        def startup_update():
            # Wait locally for the automatic task's actual cache write. The host
            # need not notify a client with no open chats. No model turn is created.
            process = subprocess.Popen([codex, "-c", "features.plugin_discovery_prefetch=false",
                                        "app-server", "--listen", "stdio://"],
                                       cwd=task_root, env=environment, text=True, encoding="utf-8",
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            messages = queue.Queue()

            def read_messages():
                for line in process.stdout:
                    messages.put(json.loads(line))
                messages.put(None)

            reader = threading.Thread(target=read_messages, daemon=True)
            reader.start()
            stderr_reader = threading.Thread(target=process.stderr.read, daemon=True)
            stderr_reader.start()
            try:
                process.stdin.write(json.dumps({"id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "terminalhook-marketplace-verifier", "version": "1.0.0"},
                    "capabilities": {"experimentalApi": True}}}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 45
                while True:
                    message = messages.get(timeout=max(0.1, deadline - time.monotonic()))
                    assert message is not None, "Codex exited before automatic marketplace update"
                    if message.get("id") == 1:
                        assert "result" in message, message
                        process.stdin.write('{"method":"initialized"}\n')
                        process.stdin.flush()
                        break
                target_path = config.parent / "plugins/cache/terminalhook-local/terminalhook/0.0.1/bin" / binary_name(target)
                while time.monotonic() < deadline:
                    if target_path.is_file() and target_path.read_bytes() == b"Startup update sentinel; never executed":
                        return
                    assert process.poll() is None, "Codex exited before automatic marketplace update"
                    time.sleep(0.05)
                raise AssertionError("Codex startup did not update the cached binary")
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(timeout=5)
                stderr_reader.join(timeout=5)
                process.stdout.close()
                process.stderr.close()

        # A real HTTP Git remote exercises the supported URL transport, with no hosted writes.
        web = task_root / "web"
        web.mkdir()
        remote = web / "marketplace.git"
        work = task_root / "distribution"
        shutil.copytree(package, work)
        git("init", "--bare", str(remote))
        git("init", "-b", stable_branch, cwd=work)
        manifest_path = work / "plugins/terminalhook/.codex-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "0.0.0"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (work / native_binary).write_bytes(b"Old cache sentinel; never executed")
        git("add", ".", cwd=work)
        git("commit", "-m", "Old binary distribution", cwd=work)
        git("push", str(remote), stable_branch, cwd=work)
        git("symbolic-ref", "HEAD", "refs/heads/" + stable_branch, cwd=remote)
        git("update-server-info", cwd=remote)
        handler = functools.partial(QuietHandler, directory=str(web))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = f"http://127.0.0.1:{server.server_port}/marketplace.git"
        try:
            # Preserve the legacy ID while replacing the user's local registration.
            install("-Source", str(package))
            install("-GitSource", source)
            current = snapshot()
            assert current["marketplaces"]["terminalhook-local"]["source_type"] == "git"
            assert current["marketplaces"]["terminalhook-local"]["source"] == source
            assert current["marketplaces"]["terminalhook-local"]["ref"] == stable_branch
            assert current["plugins"][PLUGIN]["enabled"]
            assert current["features"]["code_mode"]["direct_only_tool_namespaces"] == ["mcp__existing", "mcp__terminalhook"]
            assert cached_binary().read_bytes().startswith(b"Old cache sentinel")
            # A failed channel switch must restore the exact Git source/ref and usable cache.
            failed = install("-GitSource", source, "-Channel", "beta", check=False)
            assert failed.returncode != 0, "Missing channel was accepted"
            assert snapshot()["marketplaces"] == current["marketplaces"], (current["marketplaces"], snapshot()["marketplaces"], failed.stdout, failed.stderr)
            assert cached_binary().read_bytes().startswith(b"Old cache sentinel")
            # A valid catalog without its EXE must fail before replacing the installed cache.
            git("checkout", "-b", beta_branch, cwd=work)
            git("rm", native_binary, cwd=work)
            git("commit", "-m", "Invalid channel missing its binary", cwd=work)
            git("push", str(remote), beta_branch, cwd=work)
            git("checkout", stable_branch, cwd=work)
            git("update-server-info", cwd=remote)
            failed = install("-GitSource", source, "-Channel", "beta", check=False)
            assert failed.returncode != 0 and ("compiled Windows binary" in failed.stderr or "compiled native binary" in failed.stderr), failed.stdout + failed.stderr
            assert snapshot()["marketplaces"] == current["marketplaces"]
            assert cached_binary().read_bytes().startswith(b"Old cache sentinel")
            # Prove the automatic startup path, separately from the explicit command.
            manifest["version"] = "0.0.1"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (work / native_binary).write_bytes(b"Startup update sentinel; never executed")
            git("add", ".", cwd=work)
            git("commit", "-m", "Automatic startup update fixture", cwd=work)
            git("push", str(remote), stable_branch, cwd=work)
            git("update-server-info", cwd=remote)
            startup_update()
            assert cached_binary().read_bytes().startswith(b"Startup update sentinel"), "Automatic startup did not refresh the binary"
            # Advance the remote with the exact packaged EXE and manifest.
            for name in (native_binary, "plugins/terminalhook/.codex-plugin/plugin.json"):
                shutil.copyfile(package / name, work / name)
            git("add", ".", cwd=work)
            git("commit", "-m", "Verified binary distribution", cwd=work)
            git("push", str(remote), stable_branch, cwd=work)
            git("update-server-info", cwd=remote)
            # Only the official command performs this update; no installer or custom downloader.
            result = cli("plugin", "marketplace", "upgrade", "terminalhook-local")
            assert result["selectedMarketplaces"] == ["terminalhook-local"] and not result["errors"], result
            binary = cached_binary()
            expected = package / native_binary
            assert hashlib.sha256(binary.read_bytes()).digest() == hashlib.sha256(expected.read_bytes()).digest()
            assert command([str(binary), "--version"]).stdout.strip() == "terminalhook " + info["version"]
            # Exercise the actual MCP command resolved from the installed plugin cache.
            sys.path.insert(0, str(ROOT / "tests"))
            from test_mcp import Client
            transport = next(s for s in cli("mcp", "list") if "terminalhook" in s["name"])["transport"]
            client = Client([transport["command"], *transport.get("args", [])], cwd=transport["cwd"])
            try:
                client.run("print('READY')")
                assert client.result()["status"] == "matched", "Installed MCP launch route failed"
            finally:
                client.close()
            assert snapshot()["features"] == current["features"]
            install("-GitSource", source)
            assert snapshot()["marketplaces"] == current["marketplaces"]
            summary = {"passed": True, "platform": target, "version": info["version"], "installed_mcp_launch": True, "local_to_git_migration": True,
                       "failed_channel_restores_git_ref": True, "official_startup_updates_binary": True,
                       "missing_binary_restores_git_ref": True,
                       "official_upgrade_updates_binary": True,
                       "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                       "model_requests": "no model turn started", "live_desktop_auto_update_verified": False}
            (artifacts / "git-marketplace-validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
