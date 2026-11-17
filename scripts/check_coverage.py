"""Require 90% LLVM line coverage for the request validator and output matcher."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = ("src/spec.rs", "src/matcher.rs")
MINIMUM = 90


def validate(report, root=ROOT):
    files = {}
    for data in report["data"]:
        for item in data["files"]:
            path = Path(item["filename"]).resolve()
            if path in files:
                raise ValueError(f"Duplicate coverage entry: {path}")
            files[path] = item["summary"]["lines"]
    result = {}
    for name in CORE_FILES:
        counts = files.get((root / name).resolve())
        if counts is None:
            raise ValueError(f"Missing core coverage: {name}")
        total, covered = counts["count"], counts["covered"]
        if (type(total) is not int or type(covered) is not int
                or not 0 < total or not 0 <= covered <= total):
            raise ValueError(f"Invalid coverage counters: {name}")
        result[name] = 100 * covered / total
        if covered * 100 < total * MINIMUM:
            raise ValueError(f"{name}: {result[name]:.2f}% lines; need {MINIMUM}%")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(json.loads(args.report.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
