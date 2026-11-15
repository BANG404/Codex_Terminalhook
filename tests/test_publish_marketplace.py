import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("publish_marketplace", SCRIPTS / "publish_marketplace.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
sys.path.pop(0)


class MarketplacePublicationTests(unittest.TestCase):
    def setUp(self):
        self.info = {"version": "0.4.0-alpha.2.gbbbbbbbbbbbb", "baseVersion": "0.4.0",
                     "channel": "alpha", "revision": "b" * 40, "dirty": False}
        self.files = {name: b"runtime asset" for name in publisher.PACKAGE_FILES}
        self.files["build-info.json"] = json.dumps(self.info).encode()
        self.files["plugins/terminalhook/.codex-plugin/plugin.json"] = json.dumps(
            {"name": "terminalhook", "version": self.info["version"]}).encode()
        self.files[".agents/plugins/marketplace.json"] = json.dumps({"name": "terminalhook-local", "plugins": [
            {"name": "terminalhook", "source": {"source": "local", "path": "./plugins/terminalhook"}}]}).encode()
        self.files["plugins/terminalhook/bin/terminalhook.exe"] = b"MZ verified binary"
        self.writes = []
        self.previous = None

    def api(self, path, body=None, **kwargs):
        if body is None:
            if path == "git/commits/parent":
                return {"tree": {"sha": "old-tree"}}
            if path == "git/trees/old-tree?recursive=1":
                return {"tree": [{"path": name, "sha": publisher.blob_sha(data), "type": "blob", "mode": "100644"}
                                 for name, data in self.previous.items()]}
            if path.startswith("git/blobs/"):
                return {"content": base64.b64encode(self.previous["build-info.json"]).decode()}
            self.fail("Unexpected read " + path)
        self.writes.append((path, body, kwargs))
        if path == "git/blobs":
            return {"sha": publisher.blob_sha(base64.b64decode(body["content"]))}
        return {"sha": "new-tree" if path == "git/trees" else "new-commit"}

    def publish(self, ancestry=0):
        with patch.object(publisher, "read_ref", return_value="parent" if self.previous else None), \
                patch.object(publisher, "api", side_effect=self.api), \
                patch.object(publisher.subprocess, "run", return_value=SimpleNamespace(returncode=ancestry)):
            return publisher.publish(self.info, self.files)

    def old_distribution(self):
        self.previous = dict(self.files)
        old = dict(self.info, version="0.4.0-alpha.1.gaaaaaaaaaaaa", revision="a" * 40)
        self.previous["build-info.json"] = json.dumps(old).encode()

    def archive(self, files=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            for name, data in (self.files if files is None else files).items():
                package.writestr(name, data)
        buffer.seek(0)
        return buffer

    def test_first_publish_contains_binary_and_has_no_source_history(self):
        result = self.publish()
        self.assertEqual(result["branch"], "marketplace-alpha")
        blobs = [base64.b64decode(body["content"]) for path, body, _ in self.writes if path == "git/blobs"]
        self.assertIn(b"MZ verified binary", blobs)
        commit = next(body for path, body, _ in self.writes if path == "git/commits")
        self.assertEqual(commit["parents"], [])
        self.assertEqual(self.writes[-1][1]["ref"], "refs/heads/marketplace-alpha")
        tree = next(body for path, body, _ in self.writes if path == "git/trees")
        self.assertEqual({e["path"] for e in tree["tree"]}, set(self.files))
        self.assertNotIn("base_tree", tree)

    def test_update_fast_forwards_and_preserves_racing_writer(self):
        self.old_distribution()
        self.publish()
        self.assertEqual(self.writes[-1], ("git/refs/heads/marketplace-alpha",
                                         {"sha": "new-commit", "force": False}, {"method": "PATCH"}))
        commit = next(body for path, body, _ in self.writes if path == "git/commits")
        self.assertEqual(commit["parents"], ["parent"])
        original = self.api

        def raced(path, body=None, **kwargs):
            if kwargs.get("method") == "PATCH":
                raise RuntimeError("not a fast forward")
            return original(path, body, **kwargs)

        with patch.object(publisher, "read_ref", return_value="parent"), \
                patch.object(publisher, "api", side_effect=raced), \
                patch.object(publisher.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(RuntimeError, "not a fast forward"):
                publisher.publish(self.info, self.files)

    def test_identical_retry_does_not_write(self):
        self.previous = dict(self.files)
        self.assertTrue(self.publish()["alreadyPublished"])
        self.assertEqual(self.writes, [])

    def test_rejects_rollback_before_writes(self):
        self.old_distribution()
        with self.assertRaisesRegex(ValueError, "rollback"):
            self.publish(ancestry=1)
        self.assertEqual(self.writes, [])

    def test_rejects_changed_existing_version_before_writes(self):
        self.previous = dict(self.files, README=b"unexpected extra file")
        with self.assertRaisesRegex(ValueError, "changed existing version"):
            self.publish()
        self.assertEqual(self.writes, [])

    def test_rejects_cross_channel_and_version_downgrade(self):
        for change, error in (({"channel": "beta"}, "channel mismatch"),
                              ({"baseVersion": "0.5.0"}, "version downgrade"),
                              ({"revision": self.info["revision"]}, "same source")):
            with self.subTest(change=change):
                self.old_distribution()
                previous = json.loads(self.previous["build-info.json"])
                previous.update(change)
                self.previous["build-info.json"] = json.dumps(previous).encode()
                with self.assertRaisesRegex(ValueError, error):
                    self.publish()
                self.assertEqual(self.writes, [])

    def test_package_is_exact_and_includes_matching_binary_identity(self):
        self.assertEqual(publisher.package_files(self.archive(), self.info), self.files)
        for name in ("../escape", ".git/hooks/post-checkout", ".github/workflows/execute.yml"):
            with self.assertRaisesRegex(ValueError, "distribution files"):
                publisher.package_files(self.archive(dict(self.files, **{name: b"unexpected"})), self.info)
        wrong = dict(self.files)
        wrong["build-info.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "source identity"):
            publisher.package_files(self.archive(wrong), self.info)
        del wrong["plugins/terminalhook/bin/terminalhook.exe"]
        with self.assertRaisesRegex(ValueError, "distribution files"):
            publisher.package_files(self.archive(wrong), self.info)

    def test_local_and_development_channels_are_not_publishable(self):
        self.assertNotIn("development", publisher.BRANCHES)
        self.assertEqual(publisher.BRANCHES["production"], "marketplace")

    def test_only_the_published_immutable_archive_can_advance_a_channel(self):
        for draft, changed_asset in ((True, False), (False, True), (False, False)):
            with self.subTest(draft=draft, changed_asset=changed_asset), tempfile.TemporaryDirectory() as directory:
                task_root = Path(directory)
                dist = task_root / "dist"
                dist.mkdir()
                archive = dist / "terminalhook-windows-x64.zip"
                archive.write_bytes(self.archive().getvalue())
                checksum = publisher.hashlib.sha256(archive.read_bytes()).hexdigest()
                (dist / "SHA256SUMS.txt").write_text(checksum + "  " + archive.name + "\n")
                (dist / "build-info.json").write_text(json.dumps(self.info))

                def gh(*args):
                    if args[:2] == ("release", "view"):
                        return SimpleNamespace(stdout=json.dumps({"isDraft": draft, "isPrerelease": True}))
                    self.assertEqual(args[:2], ("release", "download"))
                    destination = Path(args[args.index("--dir") + 1]) / archive.name
                    destination.write_bytes(b"changed" if changed_asset else archive.read_bytes())

                with patch.object(publisher, "ROOT", task_root), \
                        patch.object(publisher, "build_info", return_value=self.info), \
                        patch.object(publisher, "gh", side_effect=gh), \
                        patch.object(publisher, "publish", return_value={"passed": True}) as publish, \
                        patch.dict(publisher.os.environ, {"GITHUB_REPOSITORY": publisher.REPOSITORY,
                                                         "GITHUB_EVENT_NAME": "workflow_dispatch",
                                                         "RELEASE_CHANNEL": "alpha"}):
                    if draft or changed_asset:
                        with self.assertRaises(AssertionError):
                            publisher.main()
                        publish.assert_not_called()
                    else:
                        publisher.main()
                        publish.assert_called_once()
                        files = publish.call_args.args[1]
                        self.assertEqual(files["SHA256SUMS.txt"], (dist / "SHA256SUMS.txt").read_bytes())


if __name__ == "__main__":
    unittest.main()
