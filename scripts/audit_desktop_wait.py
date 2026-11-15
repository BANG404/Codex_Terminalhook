"""Verify a desktop Stop-hook wait from real model lifecycle events. Never launches Codex."""
import argparse
import json
from pathlib import Path


def audit(events, minimum_seconds=60):
    starts = [i for i, event in enumerate(events) if event["event"] == "hook_wait_started"]
    if not starts:
        raise ValueError("No Stop-hook wait was recorded")
    start = starts[-1]
    first = events[start]
    end = next((i for i in range(start + 1, len(events))
                if events[i]["event"] == "hook_wait_finished"
                and events[i]["session_id"] == first["session_id"]
                and events[i]["turn_id"] == first["turn_id"]
                and events[i]["data"]["task_ids"] == first["data"]["task_ids"]), None)
    if end is None:
        raise ValueError("Stop-hook wait has not finished")
    session = first["session_id"]
    turn = first["turn_id"]
    related = lambda event: event["session_id"] == session and event["turn_id"] == turn
    before = [e for e in events[:start] if related(e)]
    during = [e for e in events[start + 1:end] if related(e)]
    after = [e for e in events[end + 1:] if related(e)]
    if not any(e["event"] == "model_request_started" for e in before):
        raise ValueError("Missing real model request before waiting; instrumentation is unverified")
    if not any(e["event"] == "model_request_finished" for e in before):
        raise ValueError("Missing model completion before waiting")
    if not any(e["event"] == "model_request_started" for e in after):
        raise ValueError("Missing model continuation after waiting")
    if not any(e["event"] == "model_request_finished" for e in after):
        raise ValueError("Model continuation has not completed")
    requests = [e for e in during if e["event"] == "model_request_started"]
    duration = (events[end]["timestamp_ms"] - first["timestamp_ms"]) / 1000
    if duration < minimum_seconds:
        raise ValueError(f"Wait lasted {duration:.3f}s, below required {minimum_seconds}s")
    if requests:
        raise ValueError(f"Model woke {len(requests)} times during the Stop-hook wait")
    completed = {e["data"]["task_id"]: e for e in events[:end] if e["event"] == "task_completed" and related(e)}
    for task_id in first["data"]["task_ids"]:
        if task_id not in completed or completed[task_id]["data"].get("exit_code") != 0:
            raise ValueError(f"Task {task_id} did not complete with exit code 0")
    return {"passed": True, "session_id": session, "turn_id": turn,
            "wait_elapsed_seconds": duration, "model_requests_during_wait": 0,
            "model_hooks_verified_before_and_after": True, "task_ids": first["data"]["task_ids"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_path", type=Path)
    parser.add_argument("--minimum-seconds", type=float, default=60)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/desktop-stop-hook-summary.json")
    args = parser.parse_args()
    events = [json.loads(line) for line in args.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        summary = audit(events, args.minimum_seconds)
    except ValueError as error:
        summary = {"passed": False, "reason": str(error)}
    summary["audit_path"] = str(args.audit_path.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
