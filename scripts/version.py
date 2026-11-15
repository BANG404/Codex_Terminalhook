"""Derive a channel version from the exact Git source without editing source metadata."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = {"alpha", "beta", "rc", "production", "development"}


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, encoding="utf-8").strip()


def channel_version(base, channel, sequence, revision):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", base):
        raise ValueError("Cargo version must be a stable MAJOR.MINOR.PATCH baseline")
    if channel not in CHANNELS or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid channel or full source revision")
    if int(sequence) < 1:
        raise ValueError("A positive Git commit count is required")
    return base if channel == "production" else f"{base}-{channel}.{sequence}.g{revision[:12]}"


def build_info(channel="auto", cwd=ROOT, environment=None):
    environment = os.environ if environment is None else environment
    base = tomllib.loads((cwd / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
    revision = git("rev-parse", "HEAD", cwd=cwd)
    branch = git("branch", "--show-current", cwd=cwd)
    if channel == "auto":
        ref = environment.get("GITHUB_REF", "refs/heads/" + branch)
        source = ref.removeprefix("refs/heads/")
        channel = ("production" if source == "main" else source) if source in {"alpha", "beta", "rc", "main"} else "development"
        if environment.get("GITHUB_EVENT_NAME") == "pull_request":
            channel = "development"
    if channel != "development":
        expected = "main" if channel == "production" else channel
        if environment.get("GITHUB_REF", "refs/heads/" + branch) != "refs/heads/" + expected:
            raise ValueError("Release channel must match the selected source branch")
    if environment.get("GITHUB_SHA") and environment["GITHUB_SHA"] != revision:
        raise ValueError("Build source differs from GITHUB_SHA")
    dirty = bool(git("status", "--porcelain", "--untracked-files=normal", cwd=cwd))
    if dirty and (environment.get("CI") or channel != "development"):
        raise ValueError("CI and channel builds require a clean Git checkout")
    sequence = int(git("rev-list", "--count", "HEAD", cwd=cwd))
    timestamp = int(git("show", "-s", "--format=%ct", "HEAD", cwd=cwd))
    version = channel_version(base, channel, sequence, revision) + (".dirty" if dirty else "")
    return {"schemaVersion": 1, "version": version, "baseVersion": base, "channel": channel,
            "revision": revision, "sequence": sequence, "sourceDateEpoch": timestamp,
            "builtAt": datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat(), "dirty": dirty}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="auto", choices=["auto", *sorted(CHANNELS)])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.dumps(build_info(args.channel), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()
