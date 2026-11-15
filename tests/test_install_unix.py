import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("unix_installer", SCRIPTS / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
sys.path.pop(0)


class UnixInstallerTests(unittest.TestCase):
    def fixture(self, root, target="linux-arm64"):
        (root / "plugins/terminalhook/bin").mkdir(parents=True)
        (root / "plugins/terminalhook/bin/terminalhook").write_bytes(b"native fixture")
        (root / "platform.json").write_text(json.dumps({"platform": target}))

    def test_wrong_local_platform_fails_before_registration_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "macos-arm64")
            with patch.object(installer, "host_platform", return_value="linux-arm64"), patch.object(installer, "cli") as cli:
                with self.assertRaisesRegex(ValueError, "platform differs"):
                    installer.install(source=root)
                cli.assert_not_called()

    def test_git_install_selects_native_channel_and_preserves_plugin_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            calls = []
            def cli(*args):
                calls.append(args)
                if args[:3] == ("plugin", "marketplace", "list"):
                    return {"marketplaces": []}
                if args[:3] == ("plugin", "marketplace", "add"):
                    return {"marketplaceName": "terminalhook-local", "installedRoot": str(root)}
                return {}
            with patch.object(installer, "host_platform", return_value="linux-arm64"), \
                    patch.object(installer, "cli", side_effect=cli), patch.object(installer, "configure") as config:
                installer.install(channel="rc", git_source="https://example.invalid/repo.git")
            self.assertIn(("plugin", "marketplace", "add", "https://example.invalid/repo.git", "--ref", "marketplace-rc-linux-arm64", "--json"), calls)
            self.assertIn(("plugin", "add", "terminalhook@terminalhook-local", "--json"), calls)
            config.assert_called_once_with()

    def test_missing_native_binary_restores_exact_git_ref_and_sparse_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def cli(*args):
                calls.append(args)
                if args[:3] == ("plugin", "marketplace", "list"):
                    return {"marketplaces": [{"name": "terminalhook-local", "marketplaceSource": {"sourceType": "git"}}]}
                if args[:3] == ("plugin", "marketplace", "add"):
                    return {"marketplaceName": "terminalhook-local", "installedRoot": str(root)}
                return {}
            registration = {"source_type": "git", "source": "https://example.invalid/old.git",
                            "ref": "marketplace-beta-linux-arm64", "sparse_paths": ["plugins", ".agents"]}
            with patch.object(installer, "host_platform", return_value="linux-arm64"), \
                    patch.object(installer, "cli", side_effect=cli), patch.object(installer, "configure", return_value=registration):
                with self.assertRaisesRegex(ValueError, "compiled native binary"):
                    installer.install(channel="rc")
            self.assertEqual(calls[-1], ("plugin", "marketplace", "add", registration["source"], "--ref", registration["ref"],
                                         "--sparse", "plugins", "--sparse", ".agents", "--json"))
            self.assertNotIn(("plugin", "add", "terminalhook@terminalhook-local", "--json"), calls)

    def test_nonempty_local_catalog_metadata_uses_local_registration_for_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def cli(*args):
                calls.append(args)
                if args[:3] == ("plugin", "marketplace", "list"):
                    return {"marketplaces": [{"name": "terminalhook-local", "root": "catalog root",
                                              "marketplaceSource": {"sourceType": "local"}}]}
                if args[:3] == ("plugin", "marketplace", "add"):
                    return {"marketplaceName": "terminalhook-local", "installedRoot": str(root)}
                return {}
            registration = {"source": str(root / "previous local source")}
            with patch.object(installer, "host_platform", return_value="linux-arm64"), \
                    patch.object(installer, "cli", side_effect=cli), patch.object(installer, "configure", return_value=registration):
                with self.assertRaisesRegex(ValueError, "compiled native binary"):
                    installer.install(channel="rc")
            self.assertEqual(calls[-1], ("plugin", "marketplace", "add", registration["source"], "--json"))


if __name__ == "__main__":
    unittest.main()
