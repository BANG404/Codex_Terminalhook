"""Verify detached execution and the host wake boundary, without model calls."""
import json
import queue
import shutil
import threading
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_mcp import Client, EXE, ROOT, is_alive, wait_for_exit

OWNER = "01a1e361-731f-7020-8ba1-5fb700728b78"
OTHER = "01a1e361-731f-7020-8ba1-5fb700728b79"


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="terminalhook background ")
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "TERMINALHOOK_TASK_DIR": str(self.root / "tasks"),
            "TERMINALHOOK_PROXY_COMMAND": json.dumps([sys.executable, str(ROOT / "tests/fake_codex_proxy.py"), "--proxy"]),
            "TERMINALHOOK_FAKE_STATE_DIR": str(self.root),
            "TERMINALHOOK_FAKE_THREAD_ID": OWNER,
        })
        self.environment.start()
        self.host = subprocess.Popen([sys.executable, str(ROOT / "tests/fake_codex_proxy.py"), "--host"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(self.host.stdout.readline()), daemon=True).start()
        os.environ["TERMINALHOOK_FAKE_PORT"] = str(json.loads(ready.get(timeout=10))["port"])
        self.client = Client()
        self.jobs = []
        self.counter = 2

    def tearDown(self):
        known = {job["task_id"] for job in self.jobs}
        for directory in (self.root / "tasks").glob("task_*"):
            if directory.name not in known and (directory / "state.json").exists():
                self.jobs.append({"task_id": directory.name})
        for job in self.jobs:
            path = self.root / "tasks" / job["task_id"]
            try:
                if self.state(job)["status"] in ("queued", "running"):
                    self.call("cancel_task", {"task_id": job["task_id"]})
                self.await_state(job, lambda s: s["status"] not in ("queued", "running"))
                # The execution result is written before the delivery owner releases its lock.
                self.await_state(job, lambda s: s["delivery"] in ("delivered", "failed", "uncertain"))
                pid = self.state(job).get("worker_pid")
                if pid:
                    deadline = time.monotonic() + 10
                    while is_alive(pid) and time.monotonic() < deadline:
                        time.sleep(0.02)
            except (OSError, AssertionError):
                pass
        if self.client:
            self.client.close()
        self.host.terminate()
        self.host.wait(timeout=5)
        self.host.stdout.close()
        self.host.stderr.close()
        self.environment.stop()
        self.temp.cleanup()

    def call(self, name, arguments=None, owner=OWNER):
        self.counter += 1
        params = {"name": name, "arguments": arguments or {}}
        if owner:
            params["_meta"] = {"threadId": owner}
        self.client.send("tools/call", params, self.counter)
        return self.client.result(25)

    def submit(self, code="print('finished')", **overrides):
        request = {"task_key": "task-" + str(len(self.jobs)), "program": sys.executable,
                   "args": ["-u", "-c", code], "cwd": str(self.root)}
        request.update(overrides)
        job = self.call("submit_task", request)
        self.assertEqual(job["status"], "submitted", job)
        self.jobs.append(job)
        return job

    def state(self, job):
        # Use the real API's shared-delete file access on Windows. Python's
        # ordinary file reader can otherwise prevent the writer's atomic rename.
        return self.call("get_task", {"task_id": job["task_id"]})

    def await_state(self, job, predicate, seconds=12):
        # One local bounded wait, never an agent/model polling loop.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.state(job)
            if predicate(state):
                return state
            time.sleep(0.03)
        diagnostics = []
        for path in (self.root / "tasks" / job["task_id"] / "worker.log",
                     self.root / "command-events.jsonl"):
            if path.is_file():
                diagnostics.append(f"{path.name}: {path.read_text(encoding='utf-8', errors='replace')[-4096:]}")
        self.fail(f"task did not settle: {self.state(job)}\n" + "\n".join(diagnostics))

    def deliveries(self):
        path = self.root / "deliveries.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_survives_mcp_disconnect_and_wakes_only_original_chat_once(self):
        job = self.submit("import time; print('started'); time.sleep(.7); print('completed 中文')")
        self.client.close()
        self.client = Client()
        settled = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(settled["status"], "completed")
        result = self.call("get_task", {"task_id": job["task_id"]})
        self.assertEqual(result["result"]["exit_code"], 0)
        self.assertIn("completed 中文", result["result"]["output_tail"]["stdout"])
        self.assertEqual(len(self.deliveries()), 1)
        retry = self.call("retry_delivery", {"task_id": job["task_id"]})
        self.assertEqual(retry["delivery"], "delivered")
        self.assertEqual(len(self.deliveries()), 1)

    def test_fork_cannot_read_watch_cancel_or_redeliver_original_task(self):
        job = self.submit("import time; time.sleep(.7); print('original chat result')")
        for name in ("get_task", "watch_tasks", "cancel_task", "retry_delivery"):
            args = {"task_id": job["task_id"]}
            if name == "watch_tasks":
                args["after_revision"] = self.state(job)["revision"]
            denied = self.call(name, args, owner=OTHER)
            self.assertEqual(denied, {"status": "error", "code": "task_chat_mismatch",
                                     "message": "task belongs to a different chat"})
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["thread_id"], OWNER)
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(self.call("list_tasks", owner=OTHER)["tasks"], [])

    def test_delivery_retains_proxy_diagnostics_in_saved_state(self):
        started = self.root / "command-started"
        job = self.submit(
            f"from pathlib import Path; import time; Path({str(started)!r}).touch(); "
            "time.sleep(.7); print('saved before delivery')"
        )
        self.await_state(job, lambda state: started.exists())
        (self.root / "proxy-error").write_text("original host connection refused 中文", encoding="utf-8")
        state = self.await_state(job, lambda state: state["delivery"] == "failed")
        self.assertEqual(state["status"], "completed")
        self.assertIn("original host connection refused 中文", state["delivery_error"])
        self.assertRegex(state["delivery_error"], r"proxy exit status:.*42")
        saved = json.loads((self.root / "tasks" / job["task_id"] / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["delivery_error"], state["delivery_error"])

    def test_ui_subscription_waits_for_completion_and_returns_saved_state(self):
        job = self.submit("import time; time.sleep(1); print('event completion')")
        snapshot = self.call("list_tasks")
        watcher = Client()
        try:
            for ident in range(20, 30):
                watcher.send("tools/call", {"name": "watch_tasks", "arguments": {
                    "after_revision": snapshot["revision"]}, "_meta": {"threadId": OWNER}}, ident)
                snapshot = watcher.result(12)
                task = next(task for task in snapshot["tasks"] if task["task_id"] == job["task_id"])
                if task["status"] == "completed" and task["delivery"] == "delivered":
                    self.assertEqual(task["exit_code"], 0)
                    break
            else:
                self.fail("completion/delivery state did not reach the event subscriber")
        finally:
            watcher.close()

    def test_ui_subscription_ignores_other_chat_and_is_cancelled_on_disconnect(self):
        snapshot = self.call("list_tasks")
        watcher = Client()
        watcher.send("tools/call", {"name": "watch_tasks", "arguments": {
            "after_revision": snapshot["revision"]}, "_meta": {"threadId": OWNER}}, 20)
        directory = self.root / "tasks" / ("task_" + "f" * 32)
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({"task_id": directory.name,
            "thread_id": OTHER, "request": {"task_key": "other", "program": sys.executable,
            "cwd": str(self.root)}}), encoding="utf-8")
        (directory / "state.json").write_text(json.dumps({"status": "completed", "delivery": "delivered"}), encoding="utf-8")
        try:
            with self.assertRaises(queue.Empty):
                watcher.receive(.3)
        finally:
            watcher.close()
            (directory / "state.json").unlink()
            (directory / "request.json").unlink()
            directory.rmdir()
        self.assertEqual(self.call("list_tasks")["tasks"], [])

    def test_single_task_subscription_isolates_same_chat_tasks_and_returns_details(self):
        release = self.root / "release-current"
        job = self.submit(f"from pathlib import Path; import time; p=Path({str(release)!r}); deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\nprint('current finished')")
        snapshot = self.await_state(job, lambda s: s["status"] == "running")
        self.assertEqual(snapshot["task_key"], "task-0")
        self.assertEqual(snapshot["cwd"], str(self.root))
        watcher = Client()
        try:
            watcher.send("tools/call", {"name": "watch_tasks", "arguments": {
                "task_id": job["task_id"], "after_revision": snapshot["revision"]},
                "_meta": {"threadId": OWNER}}, 20)
            other = self.submit("print('unrelated finished')")
            self.await_state(other, lambda s: s["delivery"] == "delivered")
            with self.assertRaises(queue.Empty):
                watcher.receive(.3)
            # A state change before the next watch registers must also be seen.
            release.touch()
            snapshot = watcher.result(12)
            for ident in range(21, 31):
                self.assertEqual(snapshot["task_id"], job["task_id"])
                self.assertNotIn("tasks", snapshot)
                if snapshot["status"] == "completed" and snapshot["delivery"] == "delivered":
                    self.assertIn("current finished", snapshot["result"]["output_tail"]["stdout"])
                    break
                watcher.send("tools/call", {"name": "watch_tasks", "arguments": {
                    "task_id": job["task_id"], "after_revision": snapshot["revision"]},
                    "_meta": {"threadId": OWNER}}, ident)
                snapshot = watcher.result(12)
            else:
                self.fail("single-task subscription did not settle")
            denied = self.call("watch_tasks", {"task_id": job["task_id"],
                "after_revision": snapshot["revision"]}, owner=OTHER)
            self.assertIn("different chat", denied["message"])
            invalid = self.call("watch_tasks", {"task_id": "../task", "after_revision": snapshot["revision"]})
            self.assertIn("invalid task_id", invalid["message"])
        finally:
            release.touch()
            watcher.close()

    def test_successful_proxy_continuously_drains_large_stderr(self):
        code = "import sys,runpy; sys.stderr.buffer.write(b'x'*200000); sys.stderr.flush(); runpy.run_path(sys.argv[1],run_name='__main__')"
        self.client.close()
        with patch.dict(os.environ, {"TERMINALHOOK_PROXY_COMMAND": json.dumps([sys.executable, "-u", "-c", code, str(ROOT / "tests/fake_codex_proxy.py")])}):
            self.client = Client()
        job = self.submit()
        state = self.await_state(job, lambda state: state["delivery"] == "delivered")
        self.assertEqual(state["result"]["exit_code"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows MCP Job Object containment")
    def test_host_launch_survives_strict_mcp_job_termination(self):
        import ctypes
        from fake_codex_proxy import windows_job
        handle = windows_job(int(self.client.proc._handle), allow_breakaway=False)
        try:
            job = self.submit("import time; time.sleep(.7); print('survived strict MCP job')")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            self.assertTrue(kernel.TerminateJobObject(handle, 99))
            self.client.close()
            self.client = Client()
            self.await_state(job, lambda s: s["delivery"] == "delivered")
            self.assertEqual(len(self.deliveries()), 1)
        finally:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

    def test_idempotency_and_cross_chat_isolation(self):
        count = self.root / "count"
        code = f"from pathlib import Path; import time; p=Path({str(count)!r}); p.open('a').write('one'); time.sleep(.3)"
        job = self.submit(code, task_key="stable")
        same = self.call("submit_task", {"task_key": "stable", "program": sys.executable,
                         "args": ["-u", "-c", code], "cwd": str(self.root)})
        self.assertTrue(same["reused"])
        self.assertEqual(same["task_id"], job["task_id"])
        other = self.call("get_task", {"task_id": job["task_id"]}, owner=OTHER)
        self.assertIn("different chat", other["message"])
        self.assertEqual(self.call("list_tasks", owner=OTHER)["tasks"], [])
        self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(count.read_text(), "one")
        changed = self.call("submit_task", {"task_key": "stable", "program": sys.executable,
                            "args": ["-c", "print('changed')"], "cwd": str(self.root)})
        self.assertIn("different arguments", changed["message"])
        replay = subprocess.run([str(EXE), "--background-worker", job["state_dir"]],
                                capture_output=True, timeout=5)
        self.assertNotEqual(replay.returncode, 0)
        self.assertEqual(count.read_text(), "one")
        self.assertEqual(len(self.deliveries()), 1)

    @unittest.skipUnless(os.name == "nt", "Windows atomic replacement sharing")
    def test_state_replacement_waits_for_temporary_reader(self):
        release = self.root / "release-task"
        job = self.submit(f"from pathlib import Path; import time; p=Path({str(release)!r}); deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\nprint('reader released')")
        self.await_state(job, lambda s: s["status"] == "running" and s.get("worker_live"))
        # CRT readers do not grant FILE_SHARE_DELETE. Hold one across completion
        # so the first atomic replacement really fails, then release it locally.
        reader = (self.root / "tasks" / job["task_id"] / "state.json").open("r")
        release.touch()
        timer = threading.Timer(.2, reader.close)
        timer.start()
        try:
            state = self.await_state(job, lambda s: s["delivery"] == "delivered")
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["result"]["exit_code"], 0)
        finally:
            timer.join()
            reader.close()

    def test_missing_host_context_and_unavailable_host_do_not_launch(self):
        missing = self.call("submit_task", {}, owner=None)
        self.assertIn("threadId", missing["message"])
        (self.root / "unloaded").touch()
        marker = self.root / "should-not-exist"
        result = self.call("submit_task", {"task_key": "blocked", "program": sys.executable,
                           "args": ["-c", f"open({str(marker)!r},'w').close()"], "cwd": str(self.root)})
        self.assertIn("not loaded", result["message"])
        self.assertFalse(marker.exists())

    def test_completion_resumes_unloaded_chat_and_delivers_once(self):
        release = self.root / "release-task"
        job = self.submit(f"from pathlib import Path; import time; p=Path({str(release)!r}); deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\nprint('saved result')")
        (self.root / "unloaded").touch()
        self.client.close()
        release.touch()
        self.client = Client()
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "completed")
        self.assertEqual([event["task_id"] for event in self.deliveries()], [job["task_id"]])
        self.assertIn("saved result", state["result"]["output_tail"]["stdout"])
        retry = self.call("retry_delivery", {"task_id": job["task_id"]})
        self.assertEqual(retry["delivery"], "delivered")
        self.assertEqual(len(self.deliveries()), 1)

    def test_resume_with_different_thread_id_prevents_delivery(self):
        release = self.root / "release-task"
        job = self.submit(f"from pathlib import Path; import time; p=Path({str(release)!r}); deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\nprint('saved result')")
        (self.root / "unloaded").touch()
        (self.root / "resume-thread-id").write_text(OTHER, encoding="utf-8")
        release.touch()
        state = self.await_state(job, lambda s: s["delivery"] == "failed")
        self.assertIn("Codex returned a different thread", state["delivery_error"])
        self.assertEqual(state["result"]["exit_code"], 0)
        self.assertEqual(self.deliveries(), [])

    def test_failed_delivery_retries_saved_result_without_rerunning(self):
        release = self.root / "release-task"
        job = self.submit(f"from pathlib import Path; import time; p=Path({str(release)!r}); deadline=time.monotonic()+10\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\nprint('saved result')")
        (self.root / "unloaded").touch()
        (self.root / "reject-resume").touch()
        release.touch()
        state = self.await_state(job, lambda s: s["delivery"] == "failed")
        self.assertIn("original thread cannot be resumed", state["delivery_error"])
        self.assertEqual(self.deliveries(), [])
        # The failure is saved before the worker drops its delivery-owner lock.
        self.assertTrue(wait_for_exit(job["worker_pid"], timeout=10))
        (self.root / "unloaded").unlink()
        result = self.call("retry_delivery", {"task_id": job["task_id"]})
        self.assertEqual(result.get("delivery"), "delivered", result)
        self.assertEqual(len(self.deliveries()), 1)

    def test_ambiguous_delivery_never_automatically_replays(self):
        (self.root / "drop_after_accept").touch()
        job = self.submit()
        self.await_state(job, lambda s: s["delivery"] == "uncertain")
        result = self.call("retry_delivery", {"task_id": job["task_id"]})
        self.assertIn("uncertain", result["message"])
        self.assertEqual(len(self.deliveries()), 1)

    def test_cancel_terminates_tree_and_delivers_cancellation(self):
        marker = self.root / "pid"
        job = self.submit(f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(120)")
        self.await_state(job, lambda s: marker.exists())
        self.assertEqual(self.call("cancel_task", {"task_id": job["task_id"]})["status"], "cancellation_requested")
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(is_alive(int(marker.read_text())))

    def test_deadline_and_output_match_preserve_real_result(self):
        deadline = self.submit("import time; time.sleep(120)", timeout_ms=150)
        self.await_state(deadline, lambda s: s["delivery"] == "delivered")
        self.assertEqual(self.call("get_task", {"task_id": deadline["task_id"]})["result"]["status"], "timeout")
        matched = self.submit("import time; print('READY'); time.sleep(120)", until=[{"name": "ready", "pattern": "READY"}])
        self.await_state(matched, lambda s: s["delivery"] == "delivered")
        result = self.call("get_task", {"task_id": matched["task_id"]})["result"]
        self.assertEqual(result["status"], "matched")
        self.assertFalse(result["process_running"])

    def test_dashboard_resource_and_task_list(self):
        self.client.send("resources/read", {"uri": "ui://terminalhook/tasks.html"}, 20)
        resource = self.client.receive()["result"]["contents"][0]
        self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("ui/initialize", resource["text"])
        self.assertNotRegex(resource["text"], r'<script[^>]+src=')
        self.assertNotRegex(resource["text"], r'<link[^>]+rel=["\x27]stylesheet')
        self.assertEqual(self.call("list_tasks")["tasks"], [])
        job = self.submit("import time; print('live output'); time.sleep(.8)")
        listed = self.call("list_tasks")["tasks"]
        self.assertEqual(listed[0]["task_id"], job["task_id"])
        self.assertIsNone(listed[0]["timeout_ms"])
        self.await_state(job, lambda s: s["delivery"] == "delivered")

    def test_background_natural_exit_records_failure_and_has_no_session_handles(self):
        job = self.submit("import sys; print('failed command'); sys.exit(7)")
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "failed")
        result = state["result"]
        self.assertEqual(result["status"], "exited")
        self.assertEqual(result["exit_code"], 7)
        for obsolete in ("process_id", "cursor", "latest_cursor", "oldest_cursor"):
            self.assertNotIn(obsolete, result)

    def test_invalid_and_removed_submission_options_never_launch(self):
        marker = self.root / "must-not-run"
        base = {"task_key": "invalid", "program": sys.executable, "cwd": str(self.root),
                "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]}
        for overrides in ({"mode": "pty"}, {"on_match": "keep_running"},
                          {"rows": 24}, {"log_dir": str(self.root)}, {"timeout_ms": 0},
                          {"timeout_ms": 31536000001}, {"streams": ["terminal"]},
                          {"until": [{"name": "bad", "pattern": "a*"}]},
                          {"until": [{"name": "bad", "pattern": "["}]},
                          {"cwd": "relative"}, {"program": ""}, {"task_key": ""}):
            result = self.call("submit_task", dict(base, **overrides))
            self.assertEqual(result["status"], "error", result)
            self.assertFalse(marker.exists(), overrides)
        self.assertEqual(list((self.root / "tasks").glob("task_*/request.json")), [])

    def test_split_utf8_ansi_and_raw_logs(self):
        code = "import os,time; os.write(1,bytes.fromhex('1b5b33326de5')); time.sleep(.1); os.write(1,bytes.fromhex('b0b1e7bbaa3a353137331b5b306d')); time.sleep(120)"
        job = self.submit(code, until=[{"name": "ready", "pattern": "就绪:(\\d+)"}])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        result = state["result"]
        self.assertEqual(result["match"], "就绪:5173")
        self.assertEqual(result["captures"], ["5173"])
        raw = (Path(result["log_dir"]) / "stdout.log").read_bytes()
        self.assertIn(b"\x1b[32m", raw)
        self.assertNotIn("\x1b", result["output_tail"]["stdout"])

    def test_stream_isolation_window_matching_and_null_stdin(self):
        job = self.submit("import sys; print('REA'); print('DY',file=sys.stderr)",
                          until=[{"name": "never", "pattern": "READY"}])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["result"]["status"], "exited")
        job = self.submit("import sys,time; print('READY',file=sys.stderr); print('REA'); print('DY'); sys.stdout.flush(); time.sleep(120)",
                          until=[{"name": "ready", "pattern": "REA\\nDY"}], match_mode="window", streams=["stdout"])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["result"]["match"], "REA\nDY")
        self.assertEqual(state["result"]["stream"], "stdout")
        job = self.submit("import sys; print('EOF' if sys.stdin.read() == '' else 'unexpected input')")
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertIn("EOF", state["result"]["output_tail"]["stdout"])

    def test_bounded_output_and_rotating_logs_without_a_match_condition(self):
        job = self.submit("import sys; sys.stdout.write('汉'*1500000)")
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "completed")
        result = state["result"]
        self.assertTrue(result["logs_rotated"])
        self.assertLessEqual(len(result["output_tail"]["stdout"].encode()), 4096)
        log = Path(result["log_dir"])
        self.assertTrue((log / "stdout.log.1").is_file())
        self.assertLessEqual((log / "stdout.log").stat().st_size, 2*1024*1024)

    def test_natural_exit_cleans_descendants_and_drains_final_output(self):
        marker = self.root / "child-pid"
        code = f"import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); Path({str(marker)!r}).write_text(str(p.pid)); print('FINAL OUTPUT',flush=True)"
        job = self.submit(code)
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["result"]["exit_code"], 0)
        self.assertIn("FINAL OUTPUT", state["result"]["output_tail"]["stdout"])
        self.assertTrue(wait_for_exit(int(marker.read_text()), timeout=5))


    def test_start_failure_and_long_matching_line_are_saved_failures(self):
        job = self.submit(program="terminalhook-nonexistent-command-b9d8", args=[])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "failed")
        if os.name == "nt":
            # The contained Windows launcher starts successfully; the missing
            # target is reported by that launcher's real exit and stderr.
            self.assertEqual(state["result"]["status"], "exited")
            self.assertNotEqual(state["result"]["exit_code"], 0)
            self.assertTrue(state["result"]["output_tail"]["stderr"].strip())
        else:
            self.assertEqual(state["result"]["status"], "error")
            self.assertIn("Failed to start", state["result"]["message"])
        job = self.submit("print('x'*70000)", until=[{"name": "never", "pattern": "NEVER"}])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "failed")
        self.assertIn("65536", state["result"]["message"])
        # Keep the command running so a match can stop it before natural exit.
        job = self.submit(
            "import sys, threading; sys.stdout.write('x'*70000); "
            "print('READY', file=sys.stderr); threading.Event().wait()",
            streams=["stderr"], until=[{"name": "ready", "pattern": "READY"}],
        )
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["result"]["status"], "matched", state["result"])

    def test_cancellation_cleans_command_descendants_before_delivery(self):
        marker = self.root / "cancel-child"
        code = f"import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(120)"
        job = self.submit(code)
        self.await_state(job, lambda s: marker.exists() and marker.read_text().isdigit())
        receipt = self.call("cancel_task", {"task_id": job["task_id"]})
        self.assertEqual(receipt["status"], "cancellation_requested", receipt)
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "cancelled", state)
        self.assertTrue(wait_for_exit(int(marker.read_text()), timeout=5))
        if sys.platform == "darwin":
            self.assertIsNone(state["result"]["os_pid"])
        else:
            self.assertTrue(wait_for_exit(state["result"]["os_pid"], timeout=5))

    @unittest.skipUnless(sys.platform == "darwin", "macOS host command backend")
    def test_cancel_reports_terminate_rejection_when_exit_is_unconfirmed(self):
        (self.root / "reject-terminate").touch()
        marker = self.root / "ready"
        job = self.submit(f"import time; from pathlib import Path; Path({str(marker)!r}).touch(); time.sleep(120)")
        self.await_state(job, lambda s: marker.exists())
        receipt = self.call("cancel_task", {"task_id": job["task_id"]})
        self.assertEqual(receipt["status"], "cancellation_requested", receipt)
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "failed", state)
        self.assertEqual(state["result"]["status"], "error")
        self.assertIsNone(state["result"]["process_running"])
        self.assertIn("simulated terminate rejection", state["result"]["message"])
        self.assertIn("command exit unconfirmed", state["result"]["message"])

    @unittest.skipUnless(sys.platform == "darwin", "macOS host command backend")
    def test_cancel_uses_exit_report_when_terminate_rejection_races_natural_exit(self):
        (self.root / "reject-terminate").touch()
        marker = self.root / "ready"
        rejected = self.root / "terminate-rejected"
        job = self.submit(
            "import time\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).touch()\n"
            f"while not Path({str(rejected)!r}).exists():\n    time.sleep(.01)\n"
        )
        self.await_state(job, lambda s: marker.exists())
        receipt = self.call("cancel_task", {"task_id": job["task_id"]})
        self.assertEqual(receipt["status"], "cancellation_requested", receipt)
        state = self.await_state(job, lambda s: s["delivery"] == "delivered")
        self.assertEqual(state["status"], "cancelled", state)
        self.assertEqual(state["result"]["status"], "cancelled")
        self.assertIs(state["result"]["process_running"], False)
        self.assertEqual(state["result"]["exit_code"], 0)

    @unittest.skipUnless(os.environ.get("TERMINALHOOK_LONG_WAIT") == "1", "set TERMINALHOOK_LONG_WAIT=1")
    def test_background_long_wait_survives_disconnect(self):
        started = time.monotonic()
        job = self.submit("import time; print('started'); time.sleep(65); print('LONG_TASK_DONE')")
        self.assertLess(time.monotonic() - started, 10)
        self.client.close()
        self.client = Client()
        state = self.await_state(job, lambda s: s["delivery"] == "delivered", seconds=85)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["result"]["exit_code"], 0)
        self.assertGreaterEqual(state["result"]["elapsed_ms"], 64000)
        self.assertIn("LONG_TASK_DONE", state["result"]["output_tail"]["stdout"])
        self.assertEqual(len(self.deliveries()), 1)

    @unittest.skipUnless(os.environ.get("TERMINALHOOK_LONG_WAIT") == "1", "set TERMINALHOOK_LONG_WAIT=1")
    def test_background_utf8_long_wait(self):
        code = "import sys,time\nfor i in range(12):\n print(f'输出{i}: 中文就绪',flush=True); print(f'错误{i}: 中文诊断',file=sys.stderr,flush=True); time.sleep(7.5)"
        job = self.submit(code)
        state = self.await_state(job, lambda s: s["delivery"] == "delivered", seconds=110)
        self.assertEqual(state["result"]["exit_code"], 0)
        self.assertGreaterEqual(state["result"]["elapsed_ms"], 89000)
        self.assertEqual(state["result"]["output_tail"]["stdout"].count("中文就绪"), 12)
        self.assertEqual(state["result"]["output_tail"]["stderr"].count("中文诊断"), 12)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("TERMINALHOOK_LONG_WAIT") == "1", "Windows release long wait")
    def test_background_powershell_utf8_long_wait(self):
        from test_mcp import UTF8_INIT, ps_literal
        script = ". " + ps_literal(UTF8_INIT) + "; 1..12 | ForEach-Object { [Console]::Out.WriteLine('中文输出'); [Console]::Error.WriteLine('中文诊断'); Start-Sleep -Milliseconds 7500 }"
        job = self.submit(program=shutil.which("pwsh") or "powershell", args=["-NoProfile", "-Command", script])
        state = self.await_state(job, lambda s: s["delivery"] == "delivered", seconds=110)
        self.assertEqual(state["result"]["exit_code"], 0)
        self.assertGreaterEqual(state["result"]["elapsed_ms"], 89000)
        self.assertEqual(state["result"]["output_tail"]["stdout"].count("中文输出"), 12)
        self.assertEqual(state["result"]["output_tail"]["stderr"].count("中文诊断"), 12)


if __name__ == "__main__":
    unittest.main()
