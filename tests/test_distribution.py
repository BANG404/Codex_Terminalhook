import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from distribution_fixtures import archive_data, package_data

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import distribution as dist
from assemble_release import assemble
sys.path.pop(0)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.info = {"version": "0.4.0-alpha.1.gaaaaaaaaaaaa", "revision": "a" * 40}

    def test_native_platforms_and_channel_refs(self):
        hosts = [("Windows", "AMD64"), ("Linux", "x86_64"), ("Linux", "aarch64"),
                 ("Darwin", "x86_64"), ("Darwin", "arm64")]
        for target, (system, machine) in zip(dist.PLATFORMS, hosts):
            self.assertEqual(dist.host_platform(system, machine), target)
            for channel in dist.CHANNELS:
                base = dist.CHANNELS[channel]
                expected = base if target == "windows-x64" else base + "-" + target
                self.assertEqual(dist.marketplace_branch(channel, target), expected)
        with self.assertRaises(ValueError):
            dist.host_platform("Windows", "ARM64")

    def test_all_native_packages_validate_and_keep_executable_permissions(self):
        for target in dist.PLATFORMS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                files = package_data(self.info, target)
                self.assertEqual(dist.package_files(archive_data(files), self.info, target), files)
                dist.extract_package(archive_data(files), directory)
                binary = Path(directory) / "plugins/terminalhook/bin" / dist.binary_name(target)
                self.assertEqual(binary.read_bytes(), files[binary.relative_to(directory).as_posix()])
                if os.name != "nt" and not target.startswith("windows"):
                    self.assertTrue(os.access(binary, os.X_OK))
                adapter = binary.with_name(dist.desktop_host_binary_name(target))
                self.assertEqual(adapter.read_bytes(), files[adapter.relative_to(directory).as_posix()])
                if os.name != "nt" and not target.startswith("windows"):
                    self.assertTrue(os.access(adapter, os.X_OK))

    def test_bad_binary_platform_or_launch_route_is_rejected(self):
        for target in dist.PLATFORMS:
            for name, data in (("platform.json", b'{"platform":"invalid"}'),
                               ("plugins/terminalhook/.mcp.json", b'{}'),
                               ("plugins/terminalhook/bin/" + dist.binary_name(target), b"wrong binary"),
                               ("plugins/terminalhook/bin/" + dist.desktop_host_binary_name(target), b"wrong adapter")):
                files = dict(package_data(self.info, target), **{name: data})
                with self.subTest(target=target, name=name), self.assertRaises(ValueError):
                    dist.package_files(archive_data(files), self.info, target)

    def test_zip_traversal_symlinks_and_missing_execute_bits_are_rejected(self):
        import io
        for name, mode in (("../escape", 0o100644), ("/absolute", 0o100644), ("C:/escape", 0o100644), ("link", 0o120777)):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as package:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = mode << 16
                package.writestr(entry, b"unsafe")
            buffer.seek(0)
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                dist.extract_package(buffer, directory)
        files = package_data(self.info, "linux-x64")
        buffer = archive_data(files)
        with zipfile.ZipFile(buffer, "a") as package, self.assertWarns(UserWarning):
            entry = zipfile.ZipInfo("install.sh")
            entry.external_attr = 0o100644 << 16
            package.writestr(entry, b"invalid duplicate")
        buffer.seek(0)
        with self.assertRaises(ValueError):
            dist.package_files(buffer, self.info, "linux-x64")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            for name, data in files.items():
                entry = zipfile.ZipInfo(name)
                entry.external_attr = int("100644" if name == "install.sh" else dist.git_mode(name), 8) << 16
                package.writestr(entry, data)
        buffer.seek(0)
        with self.assertRaisesRegex(ValueError, "permissions differ"):
            dist.package_files(buffer, self.info, "linux-x64")

    def fixture(self, root):
        tree = "b" * 40
        for target in dist.PLATFORMS:
            source = root / "dist/platforms" / ("terminalhook-" + target)
            source.mkdir(parents=True)
            archive = source / dist.archive_name(target)
            files = package_data(self.info, target)
            archive.write_bytes(archive_data(files).getvalue())
            checksum = dist.hashlib.sha256(archive.read_bytes()).hexdigest()
            binary_hash = dist.hashlib.sha256(files["plugins/terminalhook/bin/" + dist.binary_name(target)]).hexdigest()
            report = dict(passed=True, platform=target, version=self.info["version"], source_tree=tree,
                          source_revision=self.info["revision"], archive_sha256=checksum,
                          binary_sha256=binary_hash, packaged_binary_tests="passed")
            install = dict(passed=True, platform=target, version=self.info["version"], binary_sha256=binary_hash, installed_mcp_launch=True)
            for name, data in (("build-info.json", self.info), ("package-validation.json", report), ("git-marketplace-validation.json", install)):
                (source / name).write_text(json.dumps(data), encoding="utf-8")
            (source / "SHA256SUMS.txt").write_text(checksum + "  " + archive.name + "\n")
        return tree

    def test_complete_native_evidence_assembles_a_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture(root)
            with contextlib.redirect_stdout(io.StringIO()):
                summary = assemble(root, tree)
            self.assertEqual(set(summary["platforms"]), set(dist.PLATFORMS))
            self.assertEqual(len(dist.release_assets(root / "dist", self.info)), 7)

    def test_one_missing_or_unverified_platform_blocks_release_assembly(self):
        for filename, change in (("package-validation.json", {"passed": False}),
                                 ("build-info.json", {"revision": "d" * 40}),
                                 ("git-marketplace-validation.json", {"installed_mcp_launch": False})):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                tree = self.fixture(root)
                path = root / "dist/platforms/terminalhook-macos-arm64" / filename
                content = json.loads(path.read_text())
                content.update(change)
                path.write_text(json.dumps(content))
                with self.assertRaises(ValueError):
                    assemble(root, tree)
                self.assertFalse((root / "dist/SHA256SUMS.txt").exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture(root)
            (root / "dist/platforms/terminalhook-linux-arm64/package-validation.json").unlink()
            with self.assertRaises(FileNotFoundError):
                assemble(root, tree)


if __name__ == "__main__":
    unittest.main()
