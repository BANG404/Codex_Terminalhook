"""Build and package the native host with the exact channel/source identity."""
import argparse
import json
import os
import subprocess

from dashboard import build_dashboard, verify_dashboard
from distribution import host_platform
from package_plugin import main as package
from version import ROOT, build_info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="auto")
    parser.add_argument("--prebuilt-dashboard", action="store_true",
                        help="Reuse the dashboard artifact after verifying its source revision and checksum")
    args = parser.parse_args()
    info = build_info(args.channel)
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist/build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    environment = dict(os.environ, TERMINALHOOK_BUILD_VERSION=info["version"])
    if args.prebuilt_dashboard:
        verify_dashboard(ROOT)
    else:
        build_dashboard(ROOT)
    subprocess.run(["cargo", "build", "--release", "--locked"], cwd=ROOT, env=environment, check=True)
    print(f"Packaging native target: {host_platform()}", flush=True)
    package()


if __name__ == "__main__":
    main()
