"""Publish the CI-verified artifact from a manual channel release. Never overwrites assets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from version import ROOT, build_info

REPOSITORY = "BANG404/Codex_Terminalhook"


def gh(*args, check=True):
    result = subprocess.run(["gh", *args], text=True, encoding="utf-8", capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr)
    return result


def api(path, body=None, method="POST"):
    if body is None:
        return json.loads(gh("api", f"repos/{REPOSITORY}/{path}").stdout)
    with tempfile.TemporaryDirectory() as directory:
        payload = Path(directory) / "request.json"
        payload.write_text(json.dumps(body), encoding="utf-8")
        return json.loads(gh("api", "--method", method, f"repos/{REPOSITORY}/{path}", "--input", str(payload)).stdout)


def main():
    assert os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
    assert os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Publication requires a manual workflow"
    channel = os.environ["RELEASE_CHANNEL"]
    info = json.loads((ROOT / "dist/build-info.json").read_text(encoding="utf-8"))
    assert info == build_info(channel), "Downloaded build identity differs from the checked source"
    assert not info["dirty"] and channel != "development"
    archive = ROOT / "dist/terminalhook-windows-x64.zip"
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (ROOT / "dist/SHA256SUMS.txt").read_text().split() == [checksum, archive.name]
    tag = "v" + info["version"]
    prerelease = channel != "production"
    notes = f"TerminalHook {info['version']}\n\nChannel: {channel}\nSource: {info['revision']}\nSHA256: {checksum}\nLicense: MIT\n"
    assets = [archive, ROOT / "dist/SHA256SUMS.txt", ROOT / "dist/build-info.json"]
    ref = gh("api", f"repos/{REPOSITORY}/git/ref/tags/{tag}", check=False)
    if ref.returncode:
        assert "404" in ref.stderr, ref.stderr
        annotation = api("git/tags", {"tag": tag, "message": notes, "object": info["revision"], "type": "commit"})
        api("git/refs", {"ref": "refs/tags/" + tag, "sha": annotation["sha"]})
    else:
        value = json.loads(ref.stdout)["object"]
        assert value["type"] == "tag", "Release tags must be annotated"
        annotation = api("git/tags/" + value["sha"])
        assert annotation["object"]["type"] == "commit" and annotation["object"]["sha"] == info["revision"]
        assert annotation["message"] == notes, "Existing release provenance differs"
    existing = gh("release", "view", tag, "--repo", REPOSITORY, "--json", "isDraft,isPrerelease,body,assets", check=False)
    if existing.returncode:
        # Confirm absence through the API rather than treating an authentication failure as absence.
        missing = gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}", check=False)
        assert missing.returncode and "404" in missing.stderr, missing.stderr
        with tempfile.TemporaryDirectory() as directory:
            note_file = Path(directory) / "notes.md"
            note_file.write_text(notes, encoding="utf-8")
            options = ["--prerelease"] if prerelease else []
            gh("release", "create", tag, "--repo", REPOSITORY, "--verify-tag", "--draft", "--title", "TerminalHook " + info["version"], "--notes-file", str(note_file), *options)
        release = {"isDraft": True, "isPrerelease": prerelease, "body": notes, "assets": []}
    else:
        release = json.loads(existing.stdout)
    assert release["isPrerelease"] == prerelease and release["body"] == notes, "Existing release differs"
    existing_names = {asset["name"] for asset in release["assets"]}
    assert existing_names <= {asset.name for asset in assets}, "Unexpected existing assets"
    for asset in assets:
        if asset.name in existing_names:
            with tempfile.TemporaryDirectory() as directory:
                gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", asset.name, "--dir", directory)
                assert hashlib.sha256((Path(directory) / asset.name).read_bytes()).digest() == hashlib.sha256(asset.read_bytes()).digest(), "Immutable release asset differs"
        else:
            assert release["isDraft"], "Cannot repair assets on an already published release"
            gh("release", "upload", tag, str(asset), "--repo", REPOSITORY)
    if release["isDraft"]:
        gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest=" + ("false" if prerelease else "true"))
    print(gh("release", "view", tag, "--repo", REPOSITORY, "--json", "url", "--jq", ".url").stdout.strip())


if __name__ == "__main__":
    main()
