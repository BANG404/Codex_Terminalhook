"""Desktop launcher arguments and native CLI process ownership."""
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_mcp import EXE, ROOT, is_alive, wait_for_exit

HOST = EXE.with_name('terminalhook-desktop-host.exe' if os.name == 'nt' else 'terminalhook-desktop-host')


@unittest.skipUnless(os.name == 'nt', 'Windows Desktop process ownership')
class DesktopHostTests(unittest.TestCase):
    def environment(self):
        return dict(os.environ, TERMINALHOOK_DESKTOP_CODEX_BIN=sys.executable)

    def test_delegated_cli_preserves_output_and_exit_status(self):
        result = subprocess.run([str(HOST), '-u', '-c', "import sys;print('NATIVE_STDOUT');print('NATIVE_STDERR',file=sys.stderr);sys.exit(23)"],
                                env=self.environment(), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 23, result)
        self.assertEqual(result.stdout.strip(), 'NATIVE_STDOUT')
        self.assertEqual(result.stderr.strip(), 'NATIVE_STDERR')

    def test_forced_exit_cleans_native_tree_but_preserves_explicit_breakaway(self):
        code = """
import json, os, subprocess, sys, time
ordinary = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'])
detached = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'],
    creationflags=0x01000000 | 0x00000200 | 0x00000008,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    close_fds=True)
print(json.dumps({'native':os.getpid(),'ordinary':ordinary.pid,'detached':detached.pid}),flush=True)
time.sleep(120)
"""
        process = subprocess.Popen([str(HOST), '-u', '-c', code], env=self.environment(),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
        reader.start()
        children = {}
        try:
            children = json.loads(ready.get(timeout=15))
            process.kill()  # How Desktop ends its stdio CLI; no Rust Drop runs.
            process.wait(timeout=10)
            self.assertTrue(wait_for_exit(children['native'], 10), children)
            self.assertTrue(wait_for_exit(children['ordinary'], 10), children)
            self.assertTrue(is_alive(children['detached']), children)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for pid in children.values():
                if is_alive(pid):
                    os.kill(pid, signal.SIGTERM)
                    wait_for_exit(pid, 5)
            reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()


def write_shell_command(path, commands):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + commands + "\n", encoding="utf-8")
    path.chmod(0o755)


@unittest.skipUnless(sys.platform == "darwin", "macOS Desktop launcher")
class MacDesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="terminalhook Desktop "))).resolve()
        self.bundle = directory / "Test Desktop.app"
        self.desktop = self.bundle / "Contents/MacOS/ChatGPT"
        self.cli = self.bundle / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        self.adapter = directory / "host adapter"
        self.bin = directory / "commands"
        for executable in [self.desktop, self.cli, self.adapter]:
            write_shell_command(executable, "exit 99")
        write_shell_command(self.bin / "ps", 'printf "  %s\\n" "${TEST_DESKTOP_PROCESS:-}"')
        write_shell_command(self.bin / "open", 'printf "%s\\n" "$@"')
        self.environment = dict(os.environ, PATH=f"{self.bin}:{os.defpath}",
                                CODEX_HOME=str(directory / "Codex Home"),
                                TERMINALHOOK_DESKTOP_SOCKET="/tmp/test desktop/control.sock")

    def launch(self, **overrides):
        return subprocess.run(
            ["/bin/sh", str(ROOT / "scripts/Start-DesktopSharedHost.sh"),
             "--desktop-app", str(self.bundle), "--host-adapter", str(self.adapter)],
            env=dict(self.environment, **overrides), capture_output=True, text=True, timeout=5,
        )

    def test_launch_services_receives_process_local_overrides_as_distinct_arguments(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "-a", str(self.bundle),
            "--env", f"CODEX_CLI_PATH={self.adapter}",
            "--env", f"TERMINALHOOK_DESKTOP_CODEX_BIN={self.cli}",
            "--env", f"CODEX_HOME={self.environment['CODEX_HOME']}",
            "--env", f"TERMINALHOOK_DESKTOP_SOCKET={self.environment['TERMINALHOOK_DESKTOP_SOCKET']}",
        ])

    def test_leaves_running_desktop_untouched(self):
        result = self.launch(TEST_DESKTOP_PROCESS=str(self.desktop))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Quit Codex Desktop", result.stderr)
        self.assertEqual(result.stdout, "")


@unittest.skipUnless(os.name == "posix", "Unix Desktop socket ownership")
class UnixDesktopHostTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="th-host-", dir="/tmp")))
        self.socket = root / "shared" / "control.sock"
        self.cli = root / "fake codex"
        self.ready = root / "ready"
        self.environment = dict(os.environ, TERMINALHOOK_DESKTOP_CODEX_BIN=str(self.cli),
                                TERMINALHOOK_DESKTOP_SOCKET=str(self.socket), TEST_CORE_READY=str(self.ready))

    def start_once(self):
        return subprocess.run([str(HOST), "app-server"], stdin=subprocess.DEVNULL,
                              env=self.environment, capture_output=True, text=True, timeout=5)

    def test_private_directory_keeps_existing_permissions(self):
        self.socket.parent.mkdir()
        self.socket.parent.chmod(0o755)
        write_shell_command(self.cli, "exit 99")
        result = self.start_once()
        self.assertNotEqual(result.returncode, 0, result)
        self.assertIn("must be a private directory", result.stderr)
        self.assertEqual(self.socket.parent.stat().st_mode & 0o777, 0o755)

    def test_preserves_a_live_socket_and_recovers_a_stale_socket(self):
        self.check_socket_lifecycle(self.socket)

    def test_preserves_a_live_socket_alias_and_unlinks_only_the_stale_alias(self):
        native_socket = self.socket.parent.parent / "native.sock"
        self.socket.parent.mkdir(mode=0o700)
        self.socket.symlink_to(native_socket)
        self.check_socket_lifecycle(native_socket)
        self.assertTrue(native_socket.is_socket())

    def check_socket_lifecycle(self, endpoint):
        self.socket.parent.mkdir(mode=0o700, exist_ok=True)
        write_shell_command(self.cli, 'printf "%s\\n" STARTED_FAKE_CORE >&2; exit 3')
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(endpoint))
            listener.listen()
            result = self.start_once()
            self.assertNotEqual(result.returncode, 0, result)
            self.assertIn("socket is in use", result.stderr)
            self.assertNotIn("STARTED_FAKE_CORE", result.stderr)
            self.assertTrue(self.socket.is_socket())
        restarted = self.start_once()
        self.assertIn("STARTED_FAKE_CORE", restarted.stderr)
        self.assertFalse(self.socket.exists())

    def test_unlinks_a_dangling_socket_alias_but_preserves_other_entries(self):
        self.socket.parent.mkdir(mode=0o700)
        self.socket.symlink_to(self.socket.parent / "native.sock")
        write_shell_command(self.cli, 'printf "%s\\n" STARTED_FAKE_CORE >&2; exit 3')
        result = self.start_once()
        self.assertIn("STARTED_FAKE_CORE", result.stderr)
        self.assertFalse(self.socket.is_symlink())

        target = self.socket.parent / "unrelated"
        target.write_text("untouched", encoding="utf-8")
        self.socket.symlink_to(target)
        result = self.start_once()
        self.assertIn("socket path is occupied", result.stderr)
        self.assertTrue(self.socket.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_termination_during_startup_cleans_up_native_core(self):
        write_shell_command(self.cli, """
if [ "$2" = proxy ]; then
    exec cat
fi
printf '%s\\n' "$$" "$TERMINALHOOK_CODEX_BIN" "$TERMINALHOOK_CODEX_SOCKET" "$@" > "$TEST_CORE_READY.tmp"
mv "$TEST_CORE_READY.tmp" "$TEST_CORE_READY"
exec sleep 90
""")
        process = subprocess.Popen([str(HOST), "app-server", "--listen", "stdio://"],
                                   env=self.environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        core = None
        try:
            deadline = time.monotonic() + 10
            while not self.ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            self.assertTrue(self.ready.is_file(), "Adapter did not reach the mock native core")
            pid, *values = self.ready.read_text(encoding="utf-8").splitlines()
            core = int(pid)
            self.assertEqual(values, [str(self.cli), str(self.socket),
                                      "app-server", "--listen", f"unix://{self.socket}"])
            process.terminate()
            self.assertEqual(process.wait(timeout=5), 0, process.stderr.read())
            self.assertTrue(wait_for_exit(core, 5), core)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()
            if core is not None and is_alive(core):
                os.kill(core, signal.SIGKILL)
                wait_for_exit(core, 5)
