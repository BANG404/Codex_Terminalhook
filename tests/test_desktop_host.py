"""Windows Desktop CLI ownership, including forced adapter termination."""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import unittest

from test_mcp import EXE, is_alive, wait_for_exit

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
