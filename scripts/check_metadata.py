"""Check the stable version baseline, lockfile, manifest and license agree."""
import json
from pathlib import Path
import tomllib

from distribution import PLUGIN_ICON

ROOT = Path(__file__).resolve().parents[1]


def main():
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    plugin = ROOT / "plugins/terminalhook"
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert cargo["version"] == manifest["version"]
    assert next(p["version"] for p in lock["package"] if p["name"] == "terminalhook") == cargo["version"]
    assert cargo["license"] == "MIT" and (ROOT / "LICENSE").read_text().startswith("MIT License\n")
    for field in ("logo", "composerIcon"):
        assert manifest["interface"][field] == "./" + PLUGIN_ICON, field
    assert (plugin / PLUGIN_ICON).is_file()
    for name in ("ci.yml", "release.yml"):
        assert (ROOT / ".github/workflows" / name).is_file()
    print("Version metadata, plugin icon, and MIT license verified")


if __name__ == "__main__":
    main()
