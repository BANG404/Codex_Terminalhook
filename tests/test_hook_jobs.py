"""Protocol tests for synchronous Stop hooks. No Codex CLI or model runs."""
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_mcp import Client, ROOT, is_alive


class HookJobsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"TERMINALHOOK_AUDIT_DIR": self.directory.name}):
            self.client = Client()

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def call(self, name, arguments, ident=2):
        self.client.send("tools/call", {"name": name, "arguments": arguments}, ident)
        return self.client.result()

    def submit(self, code, session="chat_a", **overrides):
        arguments = {"program": sys.executable, "args": ["-u", "-c", code], "cwd": str(ROOT),
                     "until": [{"name": "never", "pattern": "NEVER_EMITTED"}], "timeout_ms": 5000,
                     "env": {"PYTHONUTF8": "1"}}
        arguments.update(overrides)
        prepared = self.call("prepare_task", {"session_id": session, "turn_id": "turn_a", "tool_input": arguments})
        rewritten = prepared["hookSpecificOutput"]["updatedInput"]
        return self.call("submit_task", rewritten)

    def wait(self, session="chat_a"):
        return self.call("await_tasks", {"session_id": session, "turn_id": "turn_a"})

    def test_local_wait_one_result_and_exactly_once_continuation(self):
        start = time.monotonic()
        submitted = self.submit("import time;print('开始中文');time.sleep(.7);print('完成中文')")
        self.assertEqual(submitted["status"], "submitted", submitted)
        self.assertLess(time.monotonic() - start, .5)
        self.client.send("tools/call", {"name": "await_tasks", "arguments": {"session_id": "chat_a", "turn_id": "turn_a"}}, 3)
        with self.assertRaises(queue.Empty):
            self.client.receive(timeout=.2)
        result = self.client.result()
        self.assertEqual(result["decision"], "block", result)
        self.assertIn("完成中文", result["reason"])
        self.assertIn('"exit_code":0', result["reason"])
        self.assertEqual(self.wait(), {})
        events = [json.loads(line) for line in Path(submitted["audit_path"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["task_prepared", "task_submitted", "hook_wait_started", "task_completed", "hook_wait_finished"])

    def test_binding_overrides_forged_model_context(self):
        result = self.call("prepare_task", {"session_id": "real_chat", "turn_id": "real_turn", "tool_input": {"session_id": "fake", "turn_id": "fake", "program": "test"}})
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(result["hookSpecificOutput"]["updatedInput"], {"session_id": "real_chat", "turn_id": "real_turn", "program": "test"})

    def test_missing_hook_fails_before_starting(self):
        result = self.call("submit_task", {"program": sys.executable, "args": ["-c", "raise RuntimeError()"], "cwd": str(ROOT), "until": [{"name": "x", "pattern": "x"}]})
        self.assertEqual(result["status"], "error")
        self.assertIn("PreToolUse", result["message"])

    def test_sessions_do_not_block_or_collect_each_other(self):
        self.submit("import time;time.sleep(.7);print('SLOW')", session="chat_slow")
        self.client.send("tools/call", {"name": "await_tasks", "arguments": {"session_id": "chat_slow", "turn_id": "turn_a"}}, 30)
        self.submit("print('FAST')", session="chat_fast")
        result = self.wait("chat_fast")
        self.assertIn("FAST", result["reason"])
        self.assertNotIn("SLOW", result["reason"])
        slow = self.client.result()
        self.assertIn("SLOW", slow["reason"])
        self.assertEqual(self.wait("chat_fast"), {})

    def test_concurrent_stop_hooks_continue_only_once(self):
        self.submit("import time;time.sleep(.3);print('DONE')")
        for ident in (30, 31):
            self.client.send("tools/call", {"name": "await_tasks", "arguments": {"session_id": "chat_a", "turn_id": "turn_a"}}, ident)
        results = [self.client.result(), self.client.result()]
        self.assertEqual(sum(r.get("decision") == "block" for r in results), 1)
        self.assertIn({}, results)

    def test_timeout_stops_process_and_reports_result(self):
        self.submit("import time;time.sleep(120)", timeout_ms=100)
        result = self.wait()
        jobs = json.loads(result["reason"].split("\n", 1)[1])
        task = jobs[0]["result"]
        self.assertEqual(task["status"], "timeout", task)
        self.assertFalse(task["process_running"])
        self.assertFalse(is_alive(task["os_pid"]))

    def test_nonzero_exit_and_start_failure_are_delivered(self):
        for arguments in ({"args": ["-c", "raise SystemExit(7)"]}, {"program": "terminalhook-nonexistent-program"}):
            with self.subTest(arguments=arguments):
                self.submit("", **arguments)
                result = self.wait()
                self.assertEqual(result["decision"], "block")
                jobs = json.loads(result["reason"].split("\n", 1)[1])
                task = jobs[0]["result"]
                if "program" in arguments and os.name == "nt":
                    self.assertNotEqual(task["exit_code"], 0, task)
                    self.assertIn("program not found", task["output_tail"]["stderr"])
                else:
                    self.assertTrue(task.get("exit_code") == 7 or task["status"] == "error", task)

    def test_audit_records_model_boundaries_and_validates_paths(self):
        for event in ("model_request_started", "model_request_finished"):
            self.assertEqual(self.call("audit_event", {"session_id": "chat_a", "turn_id": "turn_a", "event": event}), {})
        for session in ("../escape", "", "a/b"):
            result = self.call("audit_event", {"session_id": session, "turn_id": "turn_a", "event": "model_request_started"})
            self.assertEqual(result["status"], "error")
        events = [json.loads(line) for line in (Path(self.directory.name) / "chat_a.jsonl").read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["model_request_started", "model_request_finished"])

    def test_cancelled_stop_wait_cleans_task_process(self):
        path = Path(self.directory.name) / "task-pid"
        self.submit(f"import os,pathlib,time;pathlib.Path({str(path)!r}).write_text(str(os.getpid()));time.sleep(120)")
        self.client.send("tools/call", {"name": "await_tasks", "arguments": {"session_id": "chat_a", "turn_id": "turn_a"}}, 30)
        with self.assertRaises(queue.Empty):
            self.client.receive(timeout=.3)
        pid = int(path.read_text())
        self.client.send("notifications/cancelled", {"requestId": 30})
        result = self.call("await_tasks", {"session_id": "chat_a", "turn_id": "turn_a"}, 31)
        self.assertEqual(result["decision"], "block", result)
        self.assertIn("cancelled", result["reason"])
        self.assertFalse(is_alive(pid))

    def test_rejects_retained_processes_and_unknown_arguments(self):
        for extra in ({"on_match": "keep_running"}, {"on_timeout": "keep_running"}, {"unknown": True}):
            with self.subTest(extra=extra):
                result = self.submit("print('DONE')", **extra)
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(self.wait(), {})


if __name__ == "__main__":
    unittest.main()
