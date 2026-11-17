"""Derive a shared Conventional Commits target and exact channel/source version."""
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
STABLE_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
STAGE_TAG = re.compile(r"v(?P<version>" + STABLE_VERSION.pattern
                       + r")-(alpha|beta|rc)\.(?P<sequence>[1-9]\d*)\.g(?P<revision>[0-9a-f]{12})")
COMMIT_HEADER = re.compile(r"([a-z]+)(?:\([^()\r\n]+\))?(!)?: [^\s].*", re.I)


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, encoding="utf-8").strip()


def commit_bump(message):
    """Return the highest release impact: none, patch, minor, or breaking."""
    lines = message.splitlines()
    header = COMMIT_HEADER.fullmatch(lines[0]) if lines else None
    if not header:
        return 0
    if header[2] or re.search(r"^BREAKING(?: CHANGE|-CHANGE):[ \t]+\S", message, re.M):
        return 3
    return {"fix": 1, "perf": 1, "feat": 2}.get(header[1].lower(), 0)


def release_source(revision, cwd=ROOT):
    """Unwrap tree-preserving delivery/promotion merges to their source history.

    A production tag lives on main, which need not be an ancestor of alpha.
    Comparing its retained source instead keeps every stage on the same cycle.
    Prefer the last parent, where normal merge commits retain the promoted source.
    """
    while True:
        parents = git("show", "-s", "--format=%P", revision, cwd=cwd).split()
        if len(parents) < 2:
            return revision
        tree = git("rev-parse", f"{revision}^{{tree}}", cwd=cwd)
        source = next((parent for parent in reversed(parents)
                       if git("rev-parse", f"{parent}^{{tree}}", cwd=cwd) == tree), None)
        if source is None:
            return revision
        revision = source


def target_version(fallback, revision, cwd=ROOT):
    """Use the highest applicable stable tag and unreleased non-merge commits.

    Cargo metadata is the bootstrap/minimum version, not a per-release counter.
    Stage tags pin the target for that exact source, never the next cycle's base.
    """
    if not STABLE_VERSION.fullmatch(fallback):
        raise ValueError("Cargo version must be a stable MAJOR.MINOR.PATCH baseline")
    if git("rev-parse", "--is-shallow-repository", cwd=cwd) == "true":
        raise ValueError("Version calculation requires full Git history and release tags; fetch --unshallow --tags")
    source = release_source(revision, cwd)
    candidates = []
    pinned = set()
    for tag in git("tag", "--list", "v*", cwd=cwd).splitlines():
        match = STABLE_VERSION.fullmatch(tag[1:])
        if match:
            candidates.append((tuple(map(int, match.groups())), tag))
            continue
        stage = STAGE_TAG.fullmatch(tag)
        if stage:
            tagged = git("rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=cwd)
            if release_source(tagged, cwd) == source:
                if (not tagged.startswith(stage["revision"])
                        or git("rev-list", "--count", tagged, cwd=cwd) != stage["sequence"]):
                    raise ValueError(f"Stage tag source identity differs: {tag}")
                pinned.add(tuple(map(int, stage["version"].split("."))))
    floor = tuple(map(int, fallback.split(".")))
    if pinned:
        if len(pinned) != 1 or next(iter(pinned)) < floor:
            raise ValueError("Stage tags disagree on the target version or fall below the Cargo minimum")
    pinned_target = next(iter(pinned), None)
    for stable, tag in sorted(candidates, reverse=True):
        tagged = git("rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=cwd)
        previous = release_source(tagged, cwd)
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", previous, source], cwd=cwd,
                                  capture_output=True, text=True, encoding="utf-8")
        if ancestor.returncode == 1:
            continue
        if ancestor.returncode:
            raise RuntimeError(f"Cannot resolve stable release ancestry: {ancestor.stderr.strip()}")
        if pinned_target:
            if pinned_target < stable:
                raise ValueError("Pinned stage target is older than its applicable stable release")
            return ".".join(map(str, pinned_target))
        messages = git("log", "--no-merges", "--format=%B%x00", f"{previous}..{source}", cwd=cwd)
        bump = max((commit_bump(message.strip()) for message in messages.split("\0")), default=0)
        major, minor, patch = stable
        if bump == 3 and major:
            target = (major + 1, 0, 0)
        elif bump >= 2:
            target = (major, minor + 1, 0)
        elif bump == 1:
            target = (major, minor, patch + 1)
        else:
            target = stable
        return ".".join(map(str, max(floor, target)))
    return ".".join(map(str, pinned_target)) if pinned_target else fallback


def channel_version(base, channel, sequence, revision):
    if not STABLE_VERSION.fullmatch(base):
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
    base = target_version(base, revision, cwd)
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
