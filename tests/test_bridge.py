"""Exercise real proxy process failures, not just a simulated ready host."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_mcp import Client, wait_for_exit

OWNER = "01a1e361-731f-7020-8ba1-5fb700728b78"


class ProxyDiagnosticsTests(unittest.TestCase):
    def call(self, code, name="check_background", arguments=None):
        with patch.dict(os.environ, {
            "TERMINALHOOK_PROXY_COMMAND": json.dumps([sys.executable, "-u", "-c", code]),
        }):
            client = Client()
        try:
            client.send("tools/call", {"name": name, "arguments": arguments or {}, "_meta": {"threadId": OWNER}}, 10)
            result = client.result(25)
            self.assertEqual(result["status"], "error", result)
            return result["message"]
        finally:
            client.close()

    def test_early_exit_reports_real_stderr_and_exit_code(self):
        message = self.call("import sys; print('socket connection refused 中文', file=sys.stderr); sys.exit(23)")
        self.assertIn("socket connection refused 中文", message)
        self.assertRegex(message, r"proxy exit status:.*23")
        self.assertIn(sys.executable, message)
        self.assertIn("stdio-only desktop", message)

    def test_stderr_larger_than_pipe_capacity_is_drained_and_bounded(self):
        message = self.call("import sys; sys.stderr.buffer.write(b'x'*200000 + b'\\xff\\n'); print('last error 中文', file=sys.stderr); sys.exit(24)")
        self.assertIn("last error 中文", message)
        self.assertIn("truncated", message)
        self.assertLess(len(message), 18000)
        self.assertRegex(message, r"proxy exit status:.*24")

    def test_diagnostic_survives_failure_after_successful_websocket_upgrade(self):
        code = """
import base64,hashlib,sys
headers=b''
while not headers.endswith(b'\\r\\n\\r\\n'):
    chunk=sys.stdin.buffer.read(1)
    if not chunk: sys.exit(12)
    headers+=chunk
key=next(line.split(b':',1)[1].strip() for line in headers.split(b'\\r\\n') if line.lower().startswith(b'sec-websocket-key:'))
accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
sys.stdout.buffer.write(b'HTTP/1.1 101 Switching Protocols\\r\\nUpgrade: websocket\\r\\nConnection: Upgrade\\r\\nSec-WebSocket-Accept: '+accept+b'\\r\\n\\r\\n');sys.stdout.buffer.flush()
sys.stderr.write('proxy ended before initialize response\\n');sys.stderr.flush()
sys.exit(25)
"""
        message = self.call(code)
        self.assertIn("proxy ended before initialize response", message)
        self.assertRegex(message, r"proxy exit status:.*25")

    def test_failed_connection_never_runs_the_requested_command(self):
        with tempfile.TemporaryDirectory(prefix="terminalhook proxy failure ") as directory:
            marker = Path(directory) / "must-not-run"
            with patch.dict(os.environ, {"TERMINALHOOK_TASK_DIR": str(Path(directory) / "tasks")}):
                message = self.call("import sys; print('host endpoint unavailable',file=sys.stderr); sys.exit(26)", "submit_task", {
                    "task_key": "no-host", "program": sys.executable,
                    "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"], "cwd": directory,
                })
            self.assertIn("host endpoint unavailable", message)
            self.assertFalse(marker.exists())
            self.assertEqual(list(Path(directory).rglob("request.json")), [])

    def test_proxy_descendants_are_cleaned_after_handshake_failure(self):
        with tempfile.TemporaryDirectory(prefix="terminalhook proxy tree ") as directory:
            marker = Path(directory) / "child-pid"
            code = f"""
import subprocess,sys
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])
Path({str(marker)!r}).write_text(str(child.pid))
print('proxy protocol failure',file=sys.stderr,flush=True)
print('invalid HTTP upgrade',flush=True)
sys.exit(27)
"""
            self.assertIn("proxy protocol failure", self.call(code))
            self.assertTrue(wait_for_exit(int(marker.read_text())))


if __name__ == "__main__":
    unittest.main()
