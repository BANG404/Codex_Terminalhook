"""Build the shared dashboard bundle or verify it before embedding it in Rust."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess

from version import ROOT, git


def bundle_digest(root):
    bundle = (root / "ui/dist/index.html").read_bytes()
    if not bundle.strip():
        raise ValueError("Dashboard bundle is empty")
    return hashlib.sha256(bundle).hexdigest()


def verify_dashboard(root=ROOT):
    manifest = json.loads((root / "ui/dist/build-info.json").read_text(encoding="utf-8"))
    if manifest != {"source_revision": git("rev-parse", "HEAD", cwd=root),
                    "sha256": bundle_digest(root)}:
        raise ValueError("Dashboard bundle source or checksum differs from this checkout")


def build_dashboard(root=ROOT):
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("Node.js and npm are required to build the embedded dashboard")
    for arguments in (("ci",), ("run", "check:ui"), ("run", "build:ui")):
        subprocess.run([npm, *arguments], cwd=root, check=True)
    manifest = {"source_revision": git("rev-parse", "HEAD", cwd=root),
                "sha256": bundle_digest(root)}
    (root / "ui/dist/build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    args = parser.parse_args()
    if args.command == "build":
        build_dashboard()
    else:
        verify_dashboard()


if __name__ == "__main__":
    main()
