"""Verify detached execution and the host wake boundary, without model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_mcp import Client, EXE, ROOT, is_alive

OWNER = "01a1e361-731f-7020-8ba1-5fb700728b78"
OTHER = "01a1e361-731f-7020-8ba1-5fb700728b79"


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="terminalhook background ")
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "TERMINALHOOK_TASK_DIR": str(self.root / "tasks"),
            "TERMINALHOOK_PROXY_COMMAND": json.dumps([sys.executable, str(ROOT / "tests/fake_codex_proxy.py")]),
            "TERMINALHOOK_FAKE_STATE_DIR": str(self.root),
            "TERMINALHOOK_FAKE_THREAD_ID": OWNER,
        })
        self.environment.start()
        self.client = Client()
        self.jobs = []
        self.counter = 2

    def tearDown(self):
        for job in self.jobs:
            path = self.root / "tasks" / job["task_id"]
            try:
                if self.state(job)["status"] in ("queued", "running"):
                    self.call("cancel_task", {"task_id": job["task_id"]})
                self.await_state(job, lambda s: s["status"] not in ("queued", "running"))
                # The execution result is written before the delivery owner releases its lock.
                self.await_state(job, lambda s: s["delivery"] in ("delivered", "failed", "uncertain"))
            except (OSError, AssertionError):
                pass
        if self.client:
            self.client.close()
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
        return json.loads((self.root / "tasks" / job["task_id"] / "state.json").read_text())

    def await_state(self, job, predicate, seconds=12):
        # One local bounded wait, never an agent/model polling loop.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.state(job)
            if predicate(state):
                return state
            time.sleep(0.03)
        self.fail(f"task did not settle: {self.state(job)}")

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

    def test_missing_host_context_and_unavailable_host_do_not_launch(self):
        missing = self.call("submit_task", {}, owner=None)
        self.assertIn("threadId", missing["message"])
        (self.root / "offline").touch()
        marker = self.root / "should-not-exist"
        result = self.call("submit_task", {"task_key": "blocked", "program": sys.executable,
                           "args": ["-c", f"open({str(marker)!r},'w').close()"], "cwd": str(self.root)})
        self.assertIn("not loaded", result["message"])
        self.assertFalse(marker.exists())

    def test_failed_delivery_retries_saved_result_without_rerunning(self):
        job = self.submit("import time; time.sleep(.5); print('saved result')")
        (self.root / "offline").touch()
        self.await_state(job, lambda s: s["delivery"] == "failed")
        self.assertEqual(self.deliveries(), [])
        (self.root / "offline").unlink()
        result = self.call("retry_delivery", {"task_id": job["task_id"]})
        self.assertEqual(result["delivery"], "delivered")
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
        self.assertNotIn("https://", resource["text"])
        self.assertIn("textContent", resource["text"])
        self.assertEqual(self.call("list_tasks")["tasks"], [])
        job = self.submit("import time; print('live output'); time.sleep(.8)")
        listed = self.call("list_tasks")["tasks"]
        self.assertEqual(listed[0]["task_id"], job["task_id"])
        self.assertIsNone(listed[0]["timeout_ms"])
        self.await_state(job, lambda s: s["delivery"] == "delivered")


if __name__ == "__main__":
    unittest.main()
