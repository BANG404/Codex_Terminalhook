"""Build and package the native host with the exact channel/source identity."""
import argparse
import json
import os
import shutil
import subprocess

from distribution import host_platform
from package_plugin import main as package
from version import ROOT, build_info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="auto")
    args = parser.parse_args()
    info = build_info(args.channel)
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist/build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    environment = dict(os.environ, TERMINALHOOK_BUILD_VERSION=info["version"])
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("Node.js and npm are required to build the embedded dashboard")
    subprocess.run([npm, "ci"], cwd=ROOT, check=True)
    subprocess.run([npm, "run", "check:ui"], cwd=ROOT, check=True)
    subprocess.run([npm, "run", "build:ui"], cwd=ROOT, check=True)
    subprocess.run(["cargo", "build", "--release", "--locked"], cwd=ROOT, env=environment, check=True)
    print(f"Packaging native target: {host_platform()}", flush=True)
    package()


if __name__ == "__main__":
    main()
