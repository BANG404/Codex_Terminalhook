"""Audit a real desktop direct MCP call from its rollout and model-hook events."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def milliseconds(timestamp):
    return round(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", type=Path)
    parser.add_argument("audit", type=Path)
    parser.add_argument("--markers", type=Path, default=ROOT / "artifacts/desktop-direct-call-markers.json")
    parser.add_argument("--minimum-seconds", type=float, default=60)
    args = parser.parse_args()
    artifacts = ROOT / "artifacts"
    rows = [json.loads(line) for line in args.rollout.read_text(encoding="utf-8").splitlines()]
    events = [json.loads(line) for line in args.audit.read_text(encoding="utf-8").splitlines()]
    markers = json.loads(args.markers.read_text(encoding="utf-8"))
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    calls = [r for r in rows if r.get("type") == "response_item"
             and r.get("payload", {}).get("type") in ("function_call", "custom_tool_call")]
    assert len(calls) == 1, "Expected exactly one model tool call; wrappers/polls must not occur"
    call = calls[0]
    payload = call["payload"]
    assert payload["type"] == "function_call" and payload["namespace"] == "mcp__terminalhook" and payload["name"] == "run_until", "Call did not use the direct TerminalHook namespace"
    outputs = [r for r in rows if r.get("type") == "response_item"
               and r.get("payload", {}).get("type") == "function_call_output"
               and r["payload"].get("call_id") == payload["call_id"]]
    assert len(outputs) == 1, "Expected one final tool result"
    output = outputs[0]
    output_text = output["payload"]["output"]
    if "\nOutput:\n" in output_text:
        output_text = output_text.split("\nOutput:\n", 1)[1]
    result = json.loads(output_text)
    emitted, returned = milliseconds(call["timestamp"]), milliseconds(output["timestamp"])
    # The function_call item is emitted while the model is still generating its
    # arguments, before tool dispatch. Use the command's complete measured
    # execution interval, and preserve the wider raw call/result span separately.
    start, end = markers["start_ms"], markers["end_ms"]
    assert emitted <= start <= end <= returned, "Command markers fall outside the call/result span"
    assert (end - start) / 1000 >= args.minimum_seconds, "Command did not run long enough"
    assert len({e["session_id"] for e in events}) == 1
    assert len({e["turn_id"] for e in events}) == 1
    before = [e for e in events if e["timestamp_ms"] <= start]
    after = [e for e in events if e["timestamp_ms"] >= end]
    during = [e for e in events if start < e["timestamp_ms"] < end]
    for name in ("model_request_started", "model_request_finished"):
        assert any(e["event"] == name for e in before), "Missing real model instrumentation before the call"
        assert any(e["event"] == name for e in after), "Missing real model instrumentation after the call"
    assert not any(e["event"].startswith("model_request_") for e in during), "Model activity occurred inside the tool wait"
    # A request started before the call must also have finished before it.
    assert sum(e["event"] == "model_request_started" for e in before) == sum(e["event"] == "model_request_finished" for e in before), "A model request was still active at call start"
    assert not any(e["event"].startswith(("hook_wait_", "task_")) for e in events), "Stop/background-task workflow was involved"
    namespaces = config["features"]["code_mode"]["direct_only_tool_namespaces"]
    assert "mcp__terminalhook" in namespaces
    manifest = json.loads((ROOT / "plugins/terminalhook/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in manifest, "Default plugin must not declare lifecycle hooks"
    assert result["exit_code"] == 0 and result["process_running"] is False
    log_dir = Path(result["log_dir"])
    stdout = (log_dir / "stdout.log").read_text(encoding="utf-8")
    stderr = (log_dir / "stderr.log").read_text(encoding="utf-8")
    assert "DESKTOP_DIRECT_CALL_COMPLETED" in stdout
    assert [line for line in stdout.splitlines() if line.startswith("中文进度:")] == [f"中文进度:{i}/12" for i in range(1, 13)]
    assert stderr.splitlines() == [f"阶段:{i}" for i in range(1, 13)]
    audit_copy = artifacts / "desktop-direct-call-events.jsonl"
    shutil.copyfile(args.audit, audit_copy)
    trace_path = artifacts / "desktop-direct-call-trace.json"
    trace = {"source_rollout": str(args.rollout), "source_sha256": hashlib.sha256(args.rollout.read_bytes()).hexdigest(),
             "call": {"timestamp": call["timestamp"], **{k: payload[k] for k in ("type", "name", "namespace", "call_id", "arguments")}},
             "result": {"timestamp": output["timestamp"], "call_id": payload["call_id"], "raw_output": output["payload"]["output"]}}
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"passed": True, "session_id": events[0]["session_id"], "turn_id": events[0]["turn_id"],
               "route": "direct_function_call", "namespace": payload["namespace"], "tool": payload["name"],
               "tool_reported_wall_seconds": float(re.search(r"Wall time: ([0-9.]+) seconds", output["payload"]["output"])[1]),
               "process_elapsed_ms": result["elapsed_ms"],
               "verified_execution_seconds": (end - start) / 1000,
               "verification_interval_source": "complete command start/end markers",
               "call_item_to_result_seconds": (returned - emitted) / 1000,
               "model_requests_during_execution": 0, "model_hooks_verified_before_and_after": True,
               "model_tool_call_count": 1, "model_tool_result_count": 1,
               "code_mode_exec_call_count": 0, "model_wait_call_count": 0,
               "stop_hook_enabled": False, "stop_hook_wait_event_count": 0,
               "exit_code": result["exit_code"], "process_running": result["process_running"],
               "stdout_progress_lines": 12, "stderr_stage_lines": 12, "log_dir": str(log_dir),
               "direct_only_tool_namespaces": namespaces, "audit_copy": str(audit_copy), "trace": str(trace_path),
               "rollout_source": str(args.rollout)}
    (artifacts / "desktop-direct-call-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
