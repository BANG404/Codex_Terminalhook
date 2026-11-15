"""Publish an already released binary package to its fast-forward Git marketplace branch."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from publish_release import REPOSITORY, api, gh
from version import ROOT, build_info

BRANCHES = {"production": "marketplace", "alpha": "marketplace-alpha",
            "beta": "marketplace-beta", "rc": "marketplace-rc"}
PACKAGE_FILES = {
    ".agents/plugins/marketplace.json", "build-info.json", "LICENSE",
    "README.md", "README.zh-CN.md", "install.ps1", "Enable-DirectTools.ps1",
    "plugins/terminalhook/.codex-plugin/plugin.json", "plugins/terminalhook/.mcp.json",
    "plugins/terminalhook/bin/terminalhook.exe",
    "plugins/terminalhook/skills/terminal-wait/SKILL.md",
    "plugins/terminalhook/skills/terminal-wait/scripts/Initialize-Utf8.ps1",
}


def package_files(archive, info):
    """Only distribute the complete frozen package, never arbitrary ZIP paths or Git hooks."""
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if {entry.filename for entry in entries} != PACKAGE_FILES or len(entries) != len(PACKAGE_FILES):
            raise ValueError("Unexpected, missing, or duplicate distribution files")
        for entry in entries:
            mode = entry.external_attr >> 16
            if entry.is_dir() or (mode & 0o170000) not in (0, 0o100000):
                raise ValueError("Distribution must contain only regular files")
        files = {entry.filename: package.read(entry) for entry in entries}
    if json.loads(files["build-info.json"]) != info:
        raise ValueError("Packaged source identity differs")
    manifest = json.loads(files["plugins/terminalhook/.codex-plugin/plugin.json"])
    if manifest["name"] != "terminalhook" or manifest["version"] != info["version"] or "hooks" in manifest:
        raise ValueError("Packaged plugin identity differs")
    catalog = json.loads(files[".agents/plugins/marketplace.json"])
    if (catalog["name"] != "terminalhook-local" or len(catalog["plugins"]) != 1
            or catalog["plugins"][0]["name"] != "terminalhook"
            or catalog["plugins"][0]["source"] != {"source": "local", "path": "./plugins/terminalhook"}):
        raise ValueError("Packaged marketplace identity differs")
    if not files["plugins/terminalhook/bin/terminalhook.exe"].startswith(b"MZ"):
        raise ValueError("Missing Windows binary")
    return files


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def read_ref(branch):
    result = gh("api", f"repos/{REPOSITORY}/git/ref/heads/{branch}", check=False)
    if result.returncode:
        if "404" not in result.stderr:
            raise RuntimeError(result.stderr)
        return None
    return json.loads(result.stdout)["object"]["sha"]


def publish(info, files):
    branch = BRANCHES[info["channel"]]
    parent = read_ref(branch)
    if parent:
        commit = api("git/commits/" + parent)
        previous_tree = commit["tree"]["sha"]
        tree = api("git/trees/" + previous_tree + "?recursive=1")
        if tree.get("truncated"):
            raise ValueError("Cannot inspect a truncated marketplace tree")
        old_files = {entry["path"]: entry for entry in tree["tree"] if entry["type"] == "blob"}
        identity = old_files.get("build-info.json")
        if identity is None:
            raise ValueError("Existing branch has no distribution provenance")
        old_blob = api("git/blobs/" + identity["sha"])
        previous = json.loads(base64.b64decode(old_blob["content"]))
        expected = {name: blob_sha(data) for name, data in files.items()}
        if (set(old_files) == set(expected)
                and all(old_files[name]["sha"] == sha and old_files[name]["mode"] == "100644"
                        for name, sha in expected.items())
                and all(e["type"] in {"blob", "tree"} for e in tree["tree"])):
            return {"branch": branch, "commit": parent, "alreadyPublished": True}
        if previous["channel"] != info["channel"] or previous["version"] == info["version"]:
            raise ValueError("Refusing a channel mismatch or a changed existing version")
        if previous["revision"] == info["revision"]:
            raise ValueError("Refusing to replace the same source with a different distribution")
        ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", previous["revision"], info["revision"]],
                                  cwd=ROOT, capture_output=True)
        if ancestry.returncode:
            raise ValueError("Refusing a marketplace rollback or unrelated source history")
        if tuple(map(int, previous["baseVersion"].split("."))) > tuple(map(int, info["baseVersion"].split("."))):
            raise ValueError("Refusing a version downgrade")
    entries = []
    for name, data in sorted(files.items()):
        blob = api("git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if blob["sha"] != blob_sha(data):
            raise ValueError("Uploaded Git blob differs from verified package")
        entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    # No base_tree: obsolete files are removed instead of leaking into future packages.
    tree = api("git/trees", {"tree": entries})
    commit = api("git/commits", {
        "message": f"TerminalHook {info['version']} ({info['channel']})\n\nSource: {info['revision']}\n",
        "tree": tree["sha"], "parents": [parent] if parent else [],
    })
    if parent:
        # A concurrent writer makes this a non-fast-forward update, which GitHub rejects.
        api("git/refs/heads/" + branch, {"sha": commit["sha"], "force": False}, method="PATCH")
    else:
        api("git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    return {"branch": branch, "commit": commit["sha"], "alreadyPublished": False}


def main():
    assert os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
    assert os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    channel = os.environ["RELEASE_CHANNEL"]
    info = json.loads((ROOT / "dist/build-info.json").read_text(encoding="utf-8"))
    assert channel in BRANCHES and info == build_info(channel) and not info["dirty"]
    archive = ROOT / "dist/terminalhook-windows-x64.zip"
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums = ROOT / "dist/SHA256SUMS.txt"
    assert sums.read_text().split() == [checksum, archive.name]
    files = package_files(archive, info)
    files[sums.name] = sums.read_bytes()
    tag = "v" + info["version"]
    release = json.loads(gh("release", "view", tag, "--repo", REPOSITORY,
                            "--json", "isDraft,isPrerelease").stdout)
    assert not release["isDraft"] and release["isPrerelease"] == (channel != "production")
    # A standalone retry must prove it is publishing the immutable released ZIP too.
    with tempfile.TemporaryDirectory() as directory:
        gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", archive.name, "--dir", directory)
        assert hashlib.sha256((Path(directory) / archive.name).read_bytes()).hexdigest() == checksum
    print(json.dumps(publish(info, files), indent=2))


if __name__ == "__main__":
    main()
