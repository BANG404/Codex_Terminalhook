"""End-to-end stdio MCP tests. Uses only Python's standard library.

python -m unittest discover -s tests -v
TERMINALHOOK_LONG_WAIT=1 enables the 65-second regression test.
"""
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
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

    def run(self, code, ident=2, **overrides):
        args = {"program": sys.executable, "args": ["-u", "-c", code], "cwd": str(ROOT),
                "until": [{"name": "ready", "pattern": "READY"}], "timeout_ms": 8000}
        args.update(overrides)
        self.send("tools/call", {"name": "run_until", "arguments": args}, ident)

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


class McpTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def tearDown(self):
        self.client.close()

    def call(self, tool, arguments, ident=3):
        self.client.send("tools/call", {"name":tool,"arguments":arguments}, ident)
        return self.client.result()

    def test_same_chunk_cursor_preserves_following_marker(self):
        self.client.run("import os,time; os.write(1,b'READY NEXT FINAL'); time.sleep(120)", on_match="keep_running")
        ready = self.client.result()
        next_result = self.call("wait_output", {"process_id":ready["process_id"], "cursor":ready["cursor"], "until":[{"name":"next","pattern":"NEXT"}], "timeout_ms":500})
        self.assertEqual(next_result["status"], "matched")
        final = self.call("wait_output", {"process_id":ready["process_id"], "cursor":next_result["cursor"], "until":[{"name":"final","pattern":"FINAL"}], "timeout_ms":500}, 4)
        self.assertEqual(final["status"], "matched")
        self.assertGreater(final["cursor"], next_result["cursor"])
        self.assertGreater(next_result["cursor"], ready["cursor"])

    def test_atomic_input_wait_and_eof(self):
        self.client.run("import sys; print('READY'); line=sys.stdin.readline().strip(); print('ANSWER:'+line); rest=sys.stdin.read(); print('EOF:'+rest)", on_match="keep_running")
        ready = self.client.result()
        answer = self.call("write_input", {"process_id":ready["process_id"], "data":"hello\n", "wait":{"until":[{"name":"answer","pattern":"ANSWER:hello"}],"timeout_ms":1000}})
        self.assertEqual(answer["status"], "matched")
        eof = self.call("write_input", {"process_id":ready["process_id"], "data":"tail", "eof":True, "wait":{"until":[{"name":"eof","pattern":"EOF:tail"}],"timeout_ms":1000}}, 4)
        self.assertEqual(eof["status"], "matched")
        exited = self.call("wait_output", {"process_id":ready["process_id"], "cursor":eof["cursor"], "until":[{"name":"never","pattern":"NEVER"}], "timeout_ms":1000}, 5)
        self.assertEqual(exited["status"], "exited_unmatched")
        self.assertEqual(exited["exit_code"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell encoding regression")
    def test_powershell_utf8_interaction_pipe_and_pty(self):
        text = "你好 TerminalHook 中文U0001f30f"
        for program in ("pwsh", "powershell"):
            self.assertIsNotNone(shutil.which(program), f"{program} is required on Windows")
            for mode in ("pipe", "pty"):
                with self.subTest(program=program, mode=mode):
                    code = (f". {ps_literal(UTF8_INIT)}; Write-Output 'READY'; "
                            "$line = [Console]::ReadLine(); Write-Output ('ACK:' + $line); "
                            "[Console]::Error.WriteLine('STDERR:' + $line); exit 0")
                    self.client.run("", program=program, args=["-NoProfile", "-Command", code],
                                    mode=mode, on_match="keep_running", timeout_ms=30000)
                    # Hosted runners may cold-start Windows PowerShell 5.1 slowly.
                    # Keep startup bounded separately from the 5s input-response assertions.
                    ready = self.client.result(timeout=35)
                    self.assertEqual(ready["status"], "matched", ready)
                    ack = self.call("write_input", {
                        "process_id": ready["process_id"],
                        "data": text + ("\r" if mode == "pty" else "\n"),
                        "wait": {"until": [{"name": "ack", "pattern": "^ACK:" + re.escape(text) + "$"}],
                                 "streams": ["terminal" if mode == "pty" else "stdout"],
                                 "timeout_ms": 5000}})
                    self.assertEqual(ack["status"], "matched", ack)
                    error = self.call("wait_output", {
                        # Pipe streams can be delivered in either order. Replay stderr
                        # from readiness even if its event arrived before the stdout ACK.
                        "process_id": ready["process_id"], "cursor": ready["cursor"],
                        "until": [{"name": "stderr", "pattern": "^STDERR:" + re.escape(text) + "$"}],
                        "streams": ["terminal" if mode == "pty" else "stderr"], "timeout_ms": 5000})
                    self.assertEqual(error["status"], "matched", error)
                    exited = self.call("wait_output", {
                        "process_id": ready["process_id"], "cursor": error["cursor"],
                        "until": [{"name": "never", "pattern": "NEVER"}], "timeout_ms": 5000})
                    self.assertEqual(exited["status"], "exited_unmatched", exited)
                    self.assertEqual(exited["exit_code"], 0)
                    self.assertFalse(is_alive(ready["os_pid"]))

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell encoding regression")
    def test_powershell_utf8_native_pipeline(self):
        text = "原生管道中文U0001f30f"
        native_code = ("import sys; data=sys.stdin.buffer.readline().decode('utf-8').strip(); "
                       "sys.stdout.buffer.write(('NATIVE:'+data+chr(10)).encode('utf-8'))")
        for program in ("pwsh", "powershell"):
            with self.subTest(program=program):
                code = (f". {ps_literal(UTF8_INIT)}; {ps_literal(text)} | "
                        f"& {ps_literal(sys.executable)} -c {ps_literal(native_code)}; exit $LASTEXITCODE")
                self.client.run("", program=program, args=["-NoProfile", "-Command", code],
                                until=[{"name": "native", "pattern": "^NATIVE:" + re.escape(text) + "$"}],
                                on_match="keep_running", timeout_ms=30000)
                native = self.client.result(timeout=35)
                self.assertEqual(native["status"], "matched", native)
                exited = self.call("wait_output", {
                    "process_id": native["process_id"], "cursor": native["cursor"],
                    "until": [{"name": "never", "pattern": "NEVER"}], "timeout_ms": 5000})
                self.assertEqual(exited["exit_code"], 0, exited)
                self.assertFalse(is_alive(native["os_pid"]))

    @unittest.skipUnless(os.name == "nt" and os.environ.get("TERMINALHOOK_LONG_WAIT") == "1",
                         "set TERMINALHOOK_LONG_WAIT=1 on Windows")
    def test_powershell_utf8_long_wait(self):
        text = "中文长任务U0001f30f"
        artifacts = ROOT / "artifacts"
        code = (f". {ps_literal(UTF8_INIT)}; Write-Output 'READY'; "
                "$line = [Console]::ReadLine(); Write-Output ('ACK:' + $line); "
                "for ($step = 1; $step -le 9; $step++) { Start-Sleep -Seconds 10; "
                "Write-Output ('进度:' + $step + '/9'); "
                "[Console]::Error.WriteLine('阶段:' + $step) }; "
                "Write-Output ('完成:' + $line); exit 0")
        self.client.run("", program="pwsh", args=["-NoProfile", "-Command", code],
                        on_match="keep_running", log_dir=str(artifacts / "powershell-long-logs"))
        ready = self.client.result()
        self.assertEqual(ready["status"], "matched", ready)
        ack = self.call("write_input", {
            "process_id": ready["process_id"], "data": text + "\n", "eof": True,
            "wait": {"until": [{"name": "ack", "pattern": "^ACK:" + re.escape(text) + "$"}],
                     "timeout_ms": 5000}})
        self.assertEqual(ack["status"], "matched", ack)
        started = time.monotonic()
        self.client.send("tools/call", {"name": "wait_output", "arguments": {
            "process_id": ready["process_id"], "cursor": ack["cursor"],
            "streams": ["stdout"], "until": [{"name": "done", "pattern": "^完成:" + re.escape(text) + "$"}],
            "timeout_ms": 110000}}, 4)
        done = self.client.result(timeout=115)
        waited = time.monotonic() - started
        self.assertEqual(done["status"], "matched", done)
        self.assertGreaterEqual(waited, 89)
        exited = self.call("wait_output", {
            "process_id": ready["process_id"], "cursor": done["cursor"],
            "until": [{"name": "never", "pattern": "NEVER"}], "timeout_ms": 5000}, 5)
        self.assertEqual(exited["status"], "exited_unmatched", exited)
        self.assertEqual(exited["exit_code"], 0)
        self.assertFalse(exited["process_running"])
        self.assertFalse(is_alive(exited["os_pid"]))
        log_dir = Path(exited["log_dir"])
        stdout = (log_dir / "stdout.log").read_text(encoding="utf-8")
        stderr = (log_dir / "stderr.log").read_text(encoding="utf-8")
        self.assertEqual(stdout.count("进度:"), 9)
        self.assertEqual(stderr.count("阶段:"), 9)
        self.assertIn("完成:" + text, stdout)
        calls = [t for t in self.client.transcript if t["direction"] == "sent" and t["message"]["method"] == "tools/call"]
        self.assertEqual([t["message"]["params"]["name"] for t in calls],
                         ["run_until", "write_input", "wait_output", "wait_output"])
        long_call = next(t for t in calls if t["message"]["id"] == 4)
        replies = [t for t in self.client.transcript if t["direction"] == "received" and t["message"].get("id") == 4]
        self.assertEqual(len(replies), 1)
        intermediate = [t for t in self.client.transcript if t["direction"] == "received"
                        and long_call["at"] < t["at"] < replies[0]["at"]]
        self.assertEqual(intermediate, [])
        (artifacts / "powershell-long-summary.json").write_text(json.dumps({
            "passed": True, "executable": str(EXE), "initializer": str(UTF8_INIT),
            "wait_elapsed_seconds": waited, "tools_call_count": len(calls),
            "long_wait_call_count": 1, "long_wait_result_count": len(replies),
            "intermediate_messages": len(intermediate), "stdout_progress_lines": 9,
            "stderr_progress_lines": 9, "exit_code": exited["exit_code"],
            "process_running": exited["process_running"], "log_dir": str(log_dir),
            "transcript": self.client.transcript}, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_wait_timeout_keeps_process_and_does_not_repeat_old_match(self):
        self.client.run("import time; print('READY'); time.sleep(120)", on_match="keep_running")
        ready = self.client.result()
        result = self.call("wait_output", {"process_id":ready["process_id"], "cursor":ready["cursor"], "until":[{"name":"ready","pattern":"READY"}], "timeout_ms":100})
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["process_running"])
        self.assertTrue(is_alive(result["os_pid"]))

    def test_run_timeout_can_keep_process_for_recovery(self):
        self.client.run("import time; print('READY'); time.sleep(120)", until=[{"name":"never","pattern":"NEVER"}], timeout_ms=100, on_timeout="keep_running")
        result = self.client.result()
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["process_running"])
        recovered = self.call("wait_output", {"process_id":result["process_id"], "cursor":0, "until":[{"name":"ready","pattern":"READY"}], "timeout_ms":500})
        self.assertEqual(recovered["status"], "matched")

    def test_cancel_wait_preserves_interactive_process(self):
        self.client.run("import sys; print('READY'); print('ANSWER:'+sys.stdin.readline().strip()); sys.stdin.read()", on_match="keep_running")
        ready = self.client.result()
        self.client.send("tools/call", {"name":"wait_output", "arguments":{"process_id":ready["process_id"],"cursor":ready["cursor"],"until":[{"name":"never","pattern":"NEVER"}],"timeout_ms":5000}}, 3)
        self.client.send("notifications/cancelled", {"requestId":3})
        result = self.call("write_input", {"process_id":ready["process_id"],"data":"ok\n","wait":{"until":[{"name":"answer","pattern":"ANSWER:ok"}],"timeout_ms":1000}}, 4)
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["process_running"])

    def test_window_mode_matches_multiple_lines(self):
        self.client.run("import os,time; os.write(1,b'BEGIN\\n'); time.sleep(.1); os.write(1,b'VALUE=42\\nEND'); time.sleep(120)", until=[{"name":"block","pattern":"BEGIN\\nVALUE=(\\d+)\\nEND"}], match_mode="window", window_bytes=256)
        result = self.client.result()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["captures"], ["42"])

    def test_ignored_stream_long_line_does_not_fail_match(self):
        self.client.run("import os,time; os.write(1,b'x'*100000); os.write(2,b'READY'); time.sleep(120)", streams=["stderr"])
        self.assertEqual(self.client.result()["status"], "matched")

    def test_logs_persist_when_requested_and_are_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            self.client.run("import os,time; os.write(1,b'\\x1b[32mREADY\\x1b[0m'); time.sleep(120)", log_dir=directory)
            result = self.client.result()
            log_dir = Path(result["log_dir"])
            self.assertTrue(log_dir.is_relative_to(directory))
            self.assertEqual((log_dir / "stdout.log").read_bytes(), b'\x1b[32mREADY\x1b[0m')
            self.client.close()
            self.assertTrue(log_dir.is_dir())

    def test_cursor_expiration_and_rotating_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            code = "import os,sys,time; print('READY'); sys.stdin.readline(); [os.write(1,b'x'*4095+b'\\n') for _ in range(1300)]; print('FINISHED'); sys.stdin.read()"
            self.client.run(code, on_match="keep_running", log_dir=directory)
            ready = self.client.result()
            result = self.call("write_input", {"process_id":ready["process_id"],"data":"go\n","wait":{"until":[{"name":"done","pattern":"FINISHED"}],"timeout_ms":10000}})
            self.assertEqual(result["status"], "matched")
            expired = self.call("wait_output", {"process_id":ready["process_id"],"cursor":0,"until":[{"name":"ready","pattern":"READY"}],"timeout_ms":1000}, 4)
            self.assertEqual(expired["status"], "cursor_expired")
            self.assertGreater(expired["oldest_cursor"], 0)
            self.call("stop_process", {"process_id":ready["process_id"]}, 5)
            log_dir = Path(result["log_dir"])
            self.assertTrue((log_dir / "stdout.log.1").exists())
            self.assertLessEqual((log_dir / "stdout.log").stat().st_size, 2*1024*1024)
            self.assertLessEqual((log_dir / "stdout.log.1").stat().st_size, 2*1024*1024)

    def test_pty_is_a_real_terminal_and_interactive(self):
        self.client.run("import sys; print('TTY:'+str(sys.stdin.isatty())+':'+str(sys.stdout.isatty())); print('READY'); line=input(); print('ANSWER:'+line); input()", mode="pty", on_match="keep_running")
        ready = self.client.result()
        self.assertEqual(ready["status"], "matched", ready)
        self.assertEqual(ready["stream"], "terminal")
        self.assertIn("TTY:True:True", ready["output_tail"]["terminal"])
        result = self.call("write_input", {"process_id":ready["process_id"],"data":"hello\r","wait":{"until":[{"name":"answer","pattern":"ANSWER:hello"}],"timeout_ms":3000}})
        self.assertEqual(result["status"], "matched", result)
        stopped = self.call("stop_process", {"process_id":ready["process_id"]}, 4)
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(is_alive(ready["os_pid"]))

    def test_pty_natural_exit_preserves_final_output(self):
        self.client.run("print('READY')", mode="pty")
        self.assertEqual(self.client.result()["status"], "matched")

    def test_pty_timeout_and_disconnect_clean_up(self):
        self.client.run("import time; print('waiting'); time.sleep(120)", mode="pty", timeout_ms=250)
        result = self.client.result()
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(is_alive(result["os_pid"]))
        self.client.run("import time; print('READY'); time.sleep(120)", ident=3, mode="pty", on_match="keep_running")
        ready = self.client.result()
        self.client.close()
        self.assertFalse(is_alive(ready["os_pid"]))

    def test_natural_exit_cleans_descendants_holding_pipes(self):
        self.client.run("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); print(p.pid)", until=[{"name":"never","pattern":"NEVER"}], timeout_ms=1000)
        result = self.client.result()
        self.assertEqual(result["status"], "exited_unmatched")
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(is_alive(int(result["output_tail"]["stdout"].strip())))

    def test_process_limit_releases_slot_after_stop(self):
        processes = []
        for index in range(8):
            self.client.run("import time;print('READY');time.sleep(120)", ident=10+index, on_match="keep_running")
            result = self.client.result()
            self.assertEqual(result["status"], "matched")
            processes.append(result["process_id"])
        self.client.run("print('READY')", ident=19)
        error = self.client.receive()["result"]
        self.assertTrue(error["isError"])
        self.assertIn("8 active", error["structuredContent"]["message"])
        self.call("stop_process", {"process_id":processes[0]}, 20)
        self.client.run("print('READY')", ident=21)
        self.assertEqual(self.client.result()["status"], "matched")

    @unittest.skipUnless(os.name == "nt", "Windows job close semantics")
    def test_server_crash_kills_job_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            self.client.run("import subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);print('READY:'+str(p.pid)+'!');time.sleep(120)", until=[{"name":"ready","pattern":"READY:(\\d+)!"}], on_match="keep_running", log_dir=directory)
            result = self.client.result()
            self.assertEqual(result["status"], "matched")
            pids = [result["os_pid"], int(result["captures"][0])]
            self.client.proc.kill()
            self.client.proc.wait(timeout=5)
            deadline = time.monotonic()+5
            while any(is_alive(p) for p in pids) and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertFalse(any(is_alive(p) for p in pids))

    def test_unknown_arguments_and_zero_width_regex_rejected(self):
        for index, extra in enumerate([{"unexpected":True}, {"until":[{"name":"zero","pattern":"\\b"}]}]):
            self.client.run("print('READY')", ident=10+index, **extra)
            response = self.client.receive()["result"]
            self.assertTrue(response["isError"], (extra, response))

    def test_pty_unicode_input(self):
        self.client.run("import sys;print('READY');print('收到:'+input());sys.stdin.read()", mode="pty", on_match="keep_running")
        ready = self.client.result()
        result = self.call("write_input", {"process_id":ready["process_id"],"data":"你好\r","wait":{"until":[{"name":"ack","pattern":"收到:你好"}],"timeout_ms":2000}})
        self.assertEqual(result["status"], "matched", result)

    def test_byte_cursor_handles_unicode_inside_same_chunk(self):
        self.client.run("import os,time;os.write(1,'就绪继续完成'.encode());time.sleep(120)", until=[{"name":"ready","pattern":"就绪"}], on_match="keep_running")
        ready = self.client.result()
        result = self.call("wait_output", {"process_id":ready["process_id"],"cursor":ready["cursor"],"until":[{"name":"next","pattern":"继续"}],"timeout_ms":200})
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["cursor"]-ready["cursor"],len('继续'.encode()))

    def test_wait_no_intermediate_messages(self):
        self.client.run("import time;print('READY');time.sleep(.5);print('LATER');time.sleep(120)", on_match="keep_running")
        ready = self.client.result()
        self.client.send("tools/call", {"name":"wait_output","arguments":{"process_id":ready["process_id"],"cursor":ready["cursor"],"until":[{"name":"later","pattern":"LATER"}],"timeout_ms":2000}}, 3)
        with self.assertRaises(queue.Empty): self.client.receive(timeout=.15)
        self.assertEqual(self.client.result()["status"], "matched")

    def test_session_eviction_removes_only_temporary_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            self.client.run("print('READY')", ident=2, log_dir=directory)
            persistent = self.client.result()
            self.client.run("print('READY')", ident=3)
            temporary = self.client.result()
            for index in range(64):
                self.client.run("print('READY')", ident=10+index)
                self.assertEqual(self.client.result()["status"], "matched")
            self.assertFalse(Path(temporary["log_dir"]).exists())
            self.assertTrue(Path(persistent["log_dir"]).exists())
            expired = self.call("wait_output", {"process_id":persistent["process_id"],"cursor":0,"until":[{"name":"ready","pattern":"READY"}],"timeout_ms":200}, 100)
            self.assertEqual(expired["status"], "error")

    def test_discovery(self):
        self.client.send("tools/list", {}, 2)
        tools = self.client.receive()["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"run_until", "wait_output", "write_input", "stop_process", "submit_task", "get_task", "cancel_task", "retry_delivery", "check_background", "list_tasks"})
        self.assertIn("until", tools[0]["inputSchema"]["required"])

    def test_no_intermediate_response_and_concurrent_ping(self):
        self.client.run("import time; print('starting'); time.sleep(1.2); print('READY'); time.sleep(20)")
        with self.assertRaises(queue.Empty):
            self.client.receive(timeout=0.25)
        self.client.send("ping", {}, 9)
        self.assertEqual(self.client.receive()["id"], 9)
        result = self.client.result()
        self.assertEqual(result["status"], "matched")
        self.assertGreaterEqual(result["elapsed_ms"], 1100)
        self.assertFalse(is_alive(result["os_pid"]))
        calls = [t for t in self.client.transcript if t["direction"] == "sent" and t["message"]["method"] == "tools/call"]
        self.assertEqual(len(calls), 1)

    def test_split_utf8_ansi_and_unfinished_line(self):
        code = "import os,time; b='\\x1b[32m就绪:5173\\x1b[0m'.encode(); os.write(1,b[:7]); time.sleep(.1); os.write(1,b[7:]); time.sleep(20)"
        self.client.run(code, until=[{"name": "ready", "pattern": "就绪:(\\d+)"}])
        result = self.client.result()
        self.assertEqual(result["match"], "就绪:5173")
        self.assertEqual(result["captures"], ["5173"])
        self.assertNotIn("\x1b", result["output_tail"]["stdout"])

    def test_streams_do_not_create_false_matches(self):
        self.client.run("import os; os.write(1,b'REA'); os.write(2,b'DY')")
        self.assertEqual(self.client.result()["status"], "exited_unmatched")

    def test_select_stderr_and_drain_stdout(self):
        self.client.run("import os,time; [os.write(1,b'noise\\n'*600) for _ in range(100)]; os.write(2,b'READY'); time.sleep(20)", streams=["stderr"])
        result = self.client.result()
        self.assertEqual(result["stream"], "stderr")
        self.assertLessEqual(len(result["output_tail"]["stdout"].encode()), 4096)

    def test_nonzero_exit(self):
        self.client.run("import sys; print('failure'); sys.exit(7)")
        result = self.client.result()
        self.assertEqual(result["status"], "exited_unmatched")
        self.assertEqual(result["exit_code"], 7)

    def test_timeout_kills_process_tree(self):
        self.client.run("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); print(p.pid); time.sleep(120)", timeout_ms=500)
        result = self.client.result()
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(is_alive(result["os_pid"]))
        self.assertFalse(is_alive(int(result["output_tail"]["stdout"].strip())))

    def test_keep_running_drains_and_stop(self):
        self.client.run("import os,time; os.write(1,b'READY\\n'); time.sleep(.1); [os.write(1,b'x'*4096) for _ in range(1024)]; time.sleep(120)", on_match="keep_running")
        result = self.client.result()
        self.assertTrue(result["process_running"])
        self.assertTrue(is_alive(result["os_pid"]))
        self.client.send("tools/call", {"name": "stop_process", "arguments": {"process_id": result["process_id"]}}, 3)
        self.assertEqual(self.client.result()["status"], "stopped")
        self.assertFalse(is_alive(result["os_pid"]))

    def test_disconnect_cleans_retained_process(self):
        self.client.run("import time; print('READY'); time.sleep(120)", on_match="keep_running")
        result = self.client.result()
        self.client.close()
        self.assertFalse(is_alive(result["os_pid"]))

    def test_cancel_cleans_pending_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pids"
            # Publish readiness atomically; file creation alone can precede the PID write.
            staging = path.with_suffix(".tmp")
            code = f"import os,pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); pathlib.Path({str(staging)!r}).write_text(str(os.getpid())+','+str(p.pid)); pathlib.Path({str(staging)!r}).replace({str(path)!r}); time.sleep(120)"
            self.client.run(code)
            # Test fixture readiness only; the MCP client does not poll tools.
            deadline = time.monotonic() + 5
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            pids = [int(p) for p in path.read_text().split(",")]
            self.client.send("notifications/cancelled", {"requestId": 2, "reason": "test cancellation"})
            deadline = time.monotonic() + 5
            while any(is_alive(p) for p in pids) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(any(is_alive(p) for p in pids))
            self.client.send("ping", {}, 3)
            self.assertEqual(self.client.receive()["id"], 3)

    def test_invalid_regex_is_tool_error(self):
        self.client.run("print('READY')", until=[{"name": "bad", "pattern": "(?=READY)"}])
        response = self.client.receive()["result"]
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["status"], "error")

    def test_long_line_fails_explicitly(self):
        self.client.run("import os,time; os.write(1,b'x'*70000); time.sleep(120)")
        result = self.client.result()
        self.assertEqual(result["status"], "error")
        self.assertIn("65536", result["message"])
        self.assertLessEqual(len(result["output_tail"]["stdout"].encode()), 4096)
        self.assertFalse(is_alive(result["os_pid"]))

    @unittest.skipUnless(os.environ.get("TERMINALHOOK_LONG_WAIT") == "1", "set TERMINALHOOK_LONG_WAIT=1")
    def test_utf8_interactive_long_wait(self):
        code = ("import sys,time;print('READY');line=input();print('ACK:'+line);"
                "[(time.sleep(10),print('进度:'+str(i),flush=True),print('阶段:'+str(i),file=sys.stderr,flush=True)) for i in range(9)];"
                "print('完成:'+line)")
        self.client.run(code, on_match="keep_running", env={"PYTHONIOENCODING": "utf-8"})
        ready = self.client.result()
        self.assertEqual(ready["status"], "matched", ready)
        ack = self.call("write_input", {"process_id": ready["process_id"], "data": "中文长任务🌏\n", "eof": True,
                        "wait": {"until": [{"name": "ack", "pattern": "ACK:中文长任务🌏"}], "timeout_ms": 5000}})
        self.assertEqual(ack["status"], "matched", ack)
        started = time.monotonic()
        self.client.send("tools/call", {"name": "wait_output", "arguments": {
            "process_id": ready["process_id"], "cursor": ack["cursor"], "streams": ["stdout"],
            "until": [{"name": "done", "pattern": "完成:中文长任务🌏"}], "timeout_ms": 110000}}, 4)
        done = self.client.result(timeout=115)
        self.assertEqual(done["status"], "matched", done)
        self.assertGreaterEqual(time.monotonic() - started, 89)
        replies = [t for t in self.client.transcript if t["direction"] == "received" and t["message"].get("id") == 4]
        self.assertEqual(len(replies), 1)
        exited = self.call("wait_output", {"process_id": ready["process_id"], "cursor": done["cursor"],
                           "until": [{"name": "never", "pattern": "NEVER"}], "timeout_ms": 5000}, 5)
        self.assertEqual(exited["status"], "exited_unmatched", exited)
        self.assertEqual(exited["exit_code"], 0)

    @unittest.skipUnless(os.environ.get("TERMINALHOOK_LONG_WAIT") == "1", "set TERMINALHOOK_LONG_WAIT=1")
    def test_wait_over_default_client_timeout(self):
        start = time.monotonic()
        self.client.run("import time; print('starting'); time.sleep(65); print('READY'); time.sleep(20)", timeout_ms=75000)
        result = self.client.result(timeout=80)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 65)
        self.assertEqual(result["status"], "matched")
        calls = [t for t in self.client.transcript if t["direction"] == "sent" and t["message"]["method"] == "tools/call"]
        replies = [t for t in self.client.transcript if t["direction"] == "received" and t["message"].get("id") == 2]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(self.client.transcript), 5)  # initialize, result, initialized, call, result
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / "protocol-long-wait.json").write_text(json.dumps({"elapsed_seconds": elapsed, "tools_call_count": len(calls), "tools_result_count": len(replies), "intermediate_messages": 0, "transcript": self.client.transcript}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
