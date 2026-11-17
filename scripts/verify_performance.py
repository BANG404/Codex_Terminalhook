"""Measure real stdio process-match latency against a bounded Linux CI budget."""
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_mcp import Client

SAMPLES = 20
P95_BUDGET_SECONDS = 3.0


def main():
    samples = []
    client = Client()
    try:
        # Each call starts a real child and matches after draining bounded output.
        for index in range(SAMPLES):
            started = time.perf_counter()
            client.run("import sys; sys.stdout.write('x' * 65536 + '\\nREADY\\n'); sys.stdout.flush()",
                       ident=index + 2, match_mode="window", window_bytes=4096,
                       timeout_ms=10000)
            result = client.result(timeout=15)
            if result["status"] != "matched" or result["rule"] != "ready":
                raise RuntimeError(f"Performance scenario failed: {result}")
            samples.append(time.perf_counter() - started)
    finally:
        client.close()
    p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
    report = {"passed": p95 <= P95_BUDGET_SECONDS, "samples_seconds": samples,
              "p95_seconds": p95, "p95_budget_seconds": P95_BUDGET_SECONDS,
              "scope": "Linux stdio, 64 KiB output, 4 KiB matching window"}
    output = ROOT / "artifacts/performance-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit("Process-match p95 exceeded the CI budget")


if __name__ == "__main__":
    main()
