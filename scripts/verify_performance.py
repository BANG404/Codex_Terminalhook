"""Measure durable background completion latency through the loopback host fixture."""
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_background import BackgroundTests

SAMPLES = 20
P95_BUDGET_SECONDS = 3.0


def main():
    samples = []
    fixture = BackgroundTests()
    fixture.setUp()
    try:
        for index in range(SAMPLES):
            started = time.perf_counter()
            job = fixture.submit("import sys; sys.stdout.write('x' * 65536 + '\\nREADY\\n'); sys.stdout.flush()",
                                 task_key=f"performance-{index}", match_mode="window",
                                 until=[{"name": "ready", "pattern": "READY"}])
            state = fixture.await_state(job, lambda s: s["delivery"] == "delivered", seconds=15)
            if state["status"] != "completed" or state["result"]["status"] != "matched":
                raise RuntimeError(f"Performance scenario failed: {state}")
            samples.append(time.perf_counter() - started)
    finally:
        fixture.tearDown()
    p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
    report = {"passed": p95 <= P95_BUDGET_SECONDS, "samples_seconds": samples,
              "p95_seconds": p95, "p95_budget_seconds": P95_BUDGET_SECONDS,
              "scope": "Linux durable worker, 64 KiB output, 64 KiB matching window, loopback host delivery"}
    output = ROOT / "artifacts/performance-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit("Background completion p95 exceeded the CI budget")


if __name__ == "__main__":
    main()
