"""Run an actual Codex turn and record all MCP traffic to prove absence of tool polling.

Uses the user's existing Codex authentication, but ignores global configuration and
only enables the test MCP. Does not change global Codex settings. Requires network.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=int, default=65)
    parser.add_argument("--exe", type=Path, default=ROOT / "target/debug/terminalhook.exe")
    parser.add_argument("--plugin", action="store_true", help="Exercise the project plugin instead of standalone MCP")
    parser.add_argument("--workflow", action="store_true", help="Exercise run, cursor wait, atomic input wait, and stop")
    parser.add_argument("--mode", choices=["pipe", "pty"], default="pipe")
    args = parser.parse_args()
    if not 1 <= args.delay <= 540:
        parser.error("delay must be in 1..540")
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    label = "codex-plugin" if args.plugin else "codex-standalone"
    if args.workflow:
        label += "-workflow-" + args.mode
    traffic_path = artifacts / f"{label}-mcp.jsonl"
    events_path = artifacts / f"{label}-events.jsonl"
    input_args = {
        "program": sys.executable,
        "args": ["-u", "-c", f"import time; print('BOOTING'); time.sleep({args.delay}); print('TERMINALHOOK_READY'); time.sleep(30)"],
        "cwd": str(ROOT),
        "until": [{"name": "ready", "pattern": "TERMINALHOOK_READY"}],
        "timeout_ms": (args.delay + 15) * 1000,
        "on_match": "terminate",
    }
    prompt = "Integration test: call the terminalhook run_until MCP tool exactly once with these arguments: " + json.dumps(input_args) + ". Await it to completion. Do not use shell tools, launch another command, poll, or inspect files. After matched, reply only with the status and elapsed_ms. Tool discovery is allowed."
    if args.workflow:
        input_args["args"] = ["-u", "-c", "import sys,time; print('READY'); time.sleep(2); print('PHASE_TWO'); line=input(); print('ACK:'+line); sys.stdin.read()"]
        input_args.update(mode=args.mode, on_match="keep_running", timeout_ms=10000)
        input_args["until"] = [{"name":"ready","pattern":"READY"}]
        newline = "\r" if args.mode == "pty" else "\n"
        prompt = ("Integration test. Use only the terminalhook MCP tools; no shell, files, status polls, or repeated tool calls. "
                  "Perform exactly these four calls in order, awaiting each: "
                  "1. run_until with " + json.dumps(input_args) + ". "
                  "2. wait_output with returned process_id and cursor, until=[{name:'phase',pattern:'PHASE_TWO'}], timeout_ms=10000. "
                  "3. write_input to that process_id with data=" + json.dumps("hello" + newline) + ", wait={until:[{name:'ack',pattern:'ACK:hello'}],timeout_ms:10000}. "
                  "4. stop_process for that process_id. Reply with the four result statuses. Tool discovery is allowed.")
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("codex not found on PATH")
    command = [codex, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json", "-C", str(ROOT)]
    if args.plugin:
        # Load the actual manifest through Codex's plugin system, without registering
        # a global marketplace or replacing the plugin transport/timeout.
        command += ["-c", 'marketplaces.terminalhook-local.source_type="local"',
                    "-c", "marketplaces.terminalhook-local.source=" + json.dumps(str(ROOT)),
                    "-c", 'plugins={"terminalhook@terminalhook-local"={enabled=true,mcp_servers={terminalhook={default_tools_approval_mode="approve"}}}}']
    else:
        command += ["-c", 'plugins={"terminalhook@terminalhook-local"={enabled=false}}']
        command += ["-c", f"mcp_servers.terminalhook.command={json.dumps(sys.executable)}",
                    "-c", "mcp_servers.terminalhook.args=" + json.dumps([str(ROOT / "scripts/record_mcp.py"), str(args.exe.resolve()), str(traffic_path)]),
                    "-c", "mcp_servers.terminalhook.tool_timeout_sec=660",
                    "-c", 'mcp_servers.terminalhook.default_tools_approval_mode="approve"']
    started = time.monotonic()
    with events_path.open("wb") as events, (artifacts / f"{label}-stderr.log").open("wb") as errors:
        result = subprocess.run(command + [prompt], stdout=events, stderr=errors, timeout=args.delay + 150,
                                input=b"",
                                creationflags=0x08000000 if os.name == "nt" else 0)
    event_lines = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    completed = [e["item"] for e in event_lines if e.get("type") == "item.completed"]
    all_tools = [i for i in completed if i.get("type") == "mcp_tool_call"]
    tool_items = [i for i in all_tools if i.get("tool") == "run_until"]
    shell_items = [i for i in completed if i.get("type") == "command_execution"]
    summary = {"exit_code": result.returncode, "elapsed_seconds": time.monotonic() - started,
               "run_until_completed_items": len(tool_items), "shell_execution_items": len(shell_items)}
    if args.workflow:
        def value(item):
            result = item.get("result") or {}
            return result.get("structured_content") or result.get("structuredContent") or json.loads(next((c["text"] for c in result.get("content", []) if c.get("type") == "text"), "{}"))
        sequence = [i.get("tool") for i in all_tools]
        results = [value(i) for i in all_tools]
        summary.update(tool_sequence=sequence, results=results)
        passed = result.returncode == 0 and not shell_items and sequence == ["run_until","wait_output","write_input","stop_process"] and [r.get("status") for r in results] == ["matched","matched","matched","stopped"]
        if passed:
            passed = len({r.get("process_id") for r in results}) == 1 and results[1]["cursor"] > results[0]["cursor"] and results[2]["cursor"] > results[1]["cursor"] and results[3]["process_running"] is False
        summary["passed"] = passed
        (artifacts / f"{label}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    if not args.plugin and traffic_path.exists():
        messages = [json.loads(line) for line in traffic_path.read_text(encoding="utf-8").splitlines()]
        calls = [m for m in messages if m["direction"] == "sent" and m["message"].get("method") == "tools/call"]
        summary["tools_call_count"] = len(calls)
        if len(calls) == 1:
            ident = calls[0]["message"]["id"]
            replies = [m for m in messages if m["direction"] == "received" and m["message"].get("id") == ident]
            summary["tools_result_count"] = len(replies)
            if len(replies) == 1:
                value = replies[0]["message"]["result"]["structuredContent"]
                summary["result"] = value
                summary["intermediate_messages"] = len([m for m in messages if m["direction"] == "received" and calls[0]["at"] < m["at"] < replies[0]["at"]])
    if args.plugin and len(tool_items) == 1:
        summary["tool_item"] = tool_items[0]
    passed = result.returncode == 0 and not shell_items and len(tool_items) == 1
    if not args.plugin:
        passed = passed and summary.get("tools_call_count") == 1 and summary.get("tools_result_count") == 1 and summary.get("intermediate_messages") == 0 and summary.get("result", {}).get("status") == "matched" and summary.get("result", {}).get("elapsed_ms", 0) >= args.delay * 1000
    elif len(tool_items) == 1:
        item_result = tool_items[0].get("result") or {}
        value = item_result.get("structuredContent")
        if value is None:
            text = next((b["text"] for b in item_result.get("content", []) if b.get("type") == "text"), "{}")
            value = json.loads(text)
        summary["result"] = value
        passed = passed and value.get("status") == "matched" and value.get("elapsed_ms", 0) >= args.delay * 1000
    summary["passed"] = passed
    (artifacts / f"{label}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
