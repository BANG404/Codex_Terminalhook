"""Background-only MCP contract and shared stdio test client; no model calls."""
import ctypes
import json
import os
from pathlib import Path
import queue
import select
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("TERMINALHOOK_EXE", ROOT / "target/debug" / ("terminalhook.exe" if os.name == "nt" else "terminalhook")))
UTF8_INIT = Path(os.environ.get("TERMINALHOOK_UTF8_INIT", ROOT / "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1"))


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class Client:
    def __init__(self, command=None, cwd=None):
        self.proc = subprocess.Popen(command or [str(EXE)], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=0x08000000 if os.name == "nt" else 0)
        self.messages = queue.Queue()
        self.transcript = []
        self.errors = []
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.error_reader = threading.Thread(target=lambda: self.errors.append(self.proc.stderr.read()), daemon=True)
        self.error_reader.start()
        self.send("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "terminalhook-test", "version": "1"}}, 1)
        assert self.receive()["result"]["serverInfo"]["name"] == "terminalhook"
        self.send("notifications/initialized", {})

    def read(self):
        for line in self.proc.stdout:
            message = json.loads(line)
            self.transcript.append({"direction": "received", "at": time.monotonic(), "message": message})
            self.messages.put(message)

    def send(self, method, params, ident=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if ident is not None:
            message["id"] = ident
        self.transcript.append({"direction": "sent", "at": time.monotonic(), "message": message})
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def receive(self, timeout=10):
        return self.messages.get(timeout=timeout)

    def result(self, timeout=10):
        response = self.receive(timeout)
        assert "error" not in response, response
        return response["result"]["structuredContent"]

    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        self.proc.stdout.close()
        self.proc.stderr.close()


def is_alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_for_exit(pid, timeout=2):
    """Wait for actual kernel exit, including an exited process awaiting reaping."""
    if sys.platform == "linux" and hasattr(os, "pidfd_open"):
        try:
            descriptor = os.pidfd_open(pid)
        except ProcessLookupError:
            return True
        try:
            return bool(select.select([descriptor], [], [], timeout)[0])
        finally:
            os.close(descriptor)
    deadline = time.monotonic() + timeout
    while is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not is_alive(pid)


class McpContractTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def tearDown(self):
        self.client.close()

    def test_only_background_tools_are_discoverable(self):
        self.client.send("tools/list", {}, 2)
        tools = self.client.receive()["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {
            "check_background", "submit_task", "list_tasks", "get_task",
            "cancel_task", "retry_delivery", "watch_tasks"})
        watch = next(tool for tool in tools if tool["name"] == "watch_tasks")
        self.assertEqual(watch["_meta"]["ui"]["visibility"], ["app"])
        submit = next(tool for tool in tools if tool["name"] == "submit_task")
        self.assertFalse(submit["inputSchema"].get("additionalProperties", True))
        self.assertNotIn("mode", submit["inputSchema"]["properties"])
        self.assertNotIn("on_match", submit["inputSchema"]["properties"])

    def test_removed_tools_cannot_be_called_even_with_valid_legacy_arguments(self):
        for ident, name in enumerate(("run_until", "wait_output", "write_input", "stop_process"), 2):
            self.client.send("tools/call", {"name": name, "arguments": {
                "program": sys.executable, "cwd": str(ROOT),
                "until": [{"name": "ready", "pattern": "READY"}],
                "process_id": "proc_previous", "cursor": 0, "data": "hello"}}, ident)
            response = self.client.receive()
            self.assertIn("error", response, name)
            self.assertIn("Unknown tool", response["error"]["message"])

    def test_ping_and_dashboard_resource_work_without_a_background_host(self):
        self.client.send("ping", {}, 2)
        self.assertIn("result", self.client.receive())
        self.client.send("resources/list", {}, 3)
        resources = self.client.receive()["result"]["resources"]
        self.assertEqual([resource["uri"] for resource in resources], ["ui://terminalhook/tasks.html"])


if __name__ == "__main__":
    unittest.main()
