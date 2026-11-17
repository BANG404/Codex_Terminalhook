import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import dashboard
import package as package_build
sys.path.pop(0)


class DashboardBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "ui/dist/index.html"
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_bytes(b"<html>self-contained dashboard</html>")
        self.revision = "a" * 40
        self.manifest = self.bundle.with_name("build-info.json")
        self.manifest.write_text(json.dumps({"source_revision": self.revision,
                                            "sha256": hashlib.sha256(self.bundle.read_bytes()).hexdigest()}))

    def test_shared_bundle_requires_matching_source_and_content(self):
        with patch.object(dashboard, "git", return_value=self.revision):
            dashboard.verify_dashboard(self.root)
            self.bundle.write_bytes(b"different dashboard")
            with self.assertRaisesRegex(ValueError, "source or checksum"):
                dashboard.verify_dashboard(self.root)
        self.bundle.write_bytes(b"<html>self-contained dashboard</html>")
        with patch.object(dashboard, "git", return_value="b" * 40):
            with self.assertRaises(ValueError):
                dashboard.verify_dashboard(self.root)

    def test_shared_bundle_rejects_missing_empty_or_malformed_artifacts(self):
        with patch.object(dashboard, "git", return_value=self.revision):
            self.bundle.write_bytes(b" \n")
            with self.assertRaisesRegex(ValueError, "empty"):
                dashboard.verify_dashboard(self.root)
            self.bundle.unlink()
            with self.assertRaises(FileNotFoundError):
                dashboard.verify_dashboard(self.root)
            self.manifest.write_text("invalid JSON")
            with self.assertRaises(ValueError):
                dashboard.verify_dashboard(self.root)
            self.manifest.unlink()
            with self.assertRaises(FileNotFoundError):
                dashboard.verify_dashboard(self.root)

    def test_build_emits_verified_manifest_only_after_all_commands_succeed(self):
        self.manifest.unlink()
        with patch.object(dashboard.shutil, "which", return_value="npm"), \
                patch.object(dashboard, "git", return_value=self.revision), \
                patch.object(dashboard.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "npm")):
            with self.assertRaises(subprocess.CalledProcessError):
                dashboard.build_dashboard(self.root)
            self.assertFalse(self.manifest.exists())
        with patch.object(dashboard.shutil, "which", return_value="npm"), \
                patch.object(dashboard, "git", return_value=self.revision), \
                patch.object(dashboard.subprocess, "run"):
            dashboard.build_dashboard(self.root)
            dashboard.verify_dashboard(self.root)

    def run_package(self, prebuilt, compile_failure=None):
        arguments = ["package.py", "--prebuilt-dashboard"] if prebuilt else ["package.py"]
        with patch.object(sys, "argv", arguments), \
                patch.object(package_build, "ROOT", self.root), \
                patch.object(package_build, "build_info", return_value={"version": "0.5.0-development.1.gaaaaaaaaaaaa"}), \
                patch.object(package_build, "host_platform", return_value="linux-x64"), \
                patch.object(package_build, "package"), \
                patch.object(dashboard, "git", return_value=self.revision), \
                patch.object(package_build, "build_dashboard") as build, \
                patch.object(package_build.subprocess, "run", side_effect=compile_failure) as compile_binary:
            package_build.main()
        return build, compile_binary

    def test_prebuilt_package_compiles_without_installing_or_rebuilding_frontend(self):
        build, compile_binary = self.run_package(prebuilt=True)
        build.assert_not_called()
        self.assertEqual(compile_binary.call_count, 1)
        self.assertEqual(compile_binary.call_args.args[0], ["cargo", "build", "--release", "--locked"])

    def test_prebuilt_package_stops_before_compilation_when_artifact_is_invalid(self):
        self.bundle.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.run_package(prebuilt=True, compile_failure=AssertionError("Invalid bundle reached Cargo"))

    def test_default_package_rebuilds_frontend_even_when_bundle_exists(self):
        build, compile_binary = self.run_package(prebuilt=False)
        build.assert_called_once_with(self.root)
        compile_binary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
