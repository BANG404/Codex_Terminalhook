import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("desktop_audit", Path(__file__).resolve().parents[1] / "scripts/audit_desktop_wait.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class AuditTests(unittest.TestCase):
    def events(self):
        def event(name, at, data=None):
            return {"event": name, "timestamp_ms": at, "session_id": "chat_a", "turn_id": "turn_a", "data": data or {}}
        return [event("model_request_started", 0), event("model_request_finished", 10),
                event("hook_wait_started", 20, {"task_ids": ["task_a"]}),
                event("task_completed", 90020, {"task_id": "task_a", "exit_code": 0}),
                event("hook_wait_finished", 90021, {"task_ids": ["task_a"]}),
                event("model_request_started", 90022), event("model_request_finished", 90023)]

    def test_accepts_observed_wait(self):
        self.assertEqual(audit_module.audit(self.events())["model_requests_during_wait"], 0)

    def test_rejects_missing_instrumentation(self):
        events = [e for e in self.events() if not e["event"].startswith("model_")]
        with self.assertRaisesRegex(ValueError, "instrumentation"):
            audit_module.audit(events)

    def test_rejects_model_wake_during_wait(self):
        events = self.events()
        events.insert(3, dict(events[0], timestamp_ms=45000))
        with self.assertRaisesRegex(ValueError, "woke"):
            audit_module.audit(events)

    def test_rejects_early_return_and_failed_task(self):
        events = self.events()
        with self.assertRaisesRegex(ValueError, "below required"):
            audit_module.audit(events, minimum_seconds=100)
        events[3]["data"]["exit_code"] = 1
        with self.assertRaisesRegex(ValueError, "exit code 0"):
            audit_module.audit(events)


if __name__ == "__main__":
    unittest.main()
