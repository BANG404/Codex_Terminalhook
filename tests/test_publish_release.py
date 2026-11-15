import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("publish_release", SCRIPTS / "publish_release.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
sys.path.pop(0)


class PublishReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "dist").mkdir()
        self.info = {"version": "0.3.1-alpha.1.gaaaaaaaaaaaa", "channel": "alpha", "revision": "a" * 40, "dirty": False}
        self.files = {"terminalhook-windows-x64.zip": b"verified CI artifact"}
        self.checksum = publisher.hashlib.sha256(self.files["terminalhook-windows-x64.zip"]).hexdigest()
        self.files["SHA256SUMS.txt"] = (self.checksum + "  terminalhook-windows-x64.zip\n").encode()
        self.files["build-info.json"] = json.dumps(self.info).encode()
        for name, data in self.files.items():
            (self.root / "dist" / name).write_bytes(data)
        self.notes = f"TerminalHook {self.info['version']}\n\nChannel: alpha\nSource: {self.info['revision']}\nSHA256: {self.checksum}\nLicense: MIT\n"
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def run_publisher(self, gh):
        with patch.object(publisher, "ROOT", self.root), patch.object(publisher, "build_info", return_value=self.info), patch.object(publisher, "gh", side_effect=gh), patch.dict(publisher.os.environ, {"GITHUB_REPOSITORY": publisher.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch", "RELEASE_CHANNEL": "alpha"}):
            publisher.main()

    def test_first_release_uploads_to_draft_before_publication(self):
        def fake_gh(*args, **kwargs):
            self.calls.append(args)
            result = SimpleNamespace(returncode=0, stdout="{}", stderr="")
            if args[0] == "api":
                if "--input" not in args:
                    result.returncode, result.stderr = 1, "HTTP 404"
                else:
                    result.stdout = json.dumps({"sha": "tag_object"})
            elif args[:2] == ("release", "view"):
                if args[-1] == ".url":
                    result.stdout = "https://example.invalid/release"
                else:
                    result.returncode = 1
            return result
        self.run_publisher(fake_gh)
        creation = next(args for args in self.calls if args[:2] == ("release", "create"))
        self.assertIn("--draft", creation)
        self.assertIn("--prerelease", creation)
        edit = next(i for i, args in enumerate(self.calls) if args[:2] == ("release", "edit"))
        uploads = [i for i, args in enumerate(self.calls) if args[:2] == ("release", "upload")]
        self.assertEqual(len(uploads), 3)
        self.assertTrue(all(i < edit for i in uploads))

    def test_refuses_changed_existing_asset(self):
        def fake_gh(*args, **kwargs):
            self.calls.append(args)
            value = {}
            if args[0] == "api":
                value = ({"object": {"type": "commit", "sha": self.info["revision"]}, "message": self.notes}
                         if "/git/tags/" in args[1] else {"object": {"type": "tag", "sha": "tag_object"}})
            elif args[:2] == ("release", "view"):
                value = {"isDraft": False, "isPrerelease": True, "body": self.notes, "assets": [{"name": name} for name in self.files]}
            elif args[:2] == ("release", "download"):
                destination = Path(args[args.index("--dir") + 1]) / args[args.index("--pattern") + 1]
                destination.write_bytes(b"tampered asset")
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
        with self.assertRaisesRegex(AssertionError, "Immutable release asset differs"):
            self.run_publisher(fake_gh)
        self.assertFalse(any(args[:2] in (("release", "upload"), ("release", "edit")) for args in self.calls))

    def test_rejects_unverified_artifact_identity(self):
        self.info["dirty"] = True
        (self.root / "dist/build-info.json").write_text(json.dumps(self.info))
        with self.assertRaises(AssertionError), patch.object(publisher, "gh") as gh:
            self.run_publisher(gh)
        gh.assert_not_called()
