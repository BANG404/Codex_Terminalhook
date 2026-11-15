import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version = load("version")
promotion = load("check_promotion")


class ReleasePolicyTests(unittest.TestCase):
    def test_channel_versions(self):
        for channel in ("alpha", "beta", "rc", "development"):
            self.assertEqual(version.channel_version("0.3.1", channel, 12, "a" * 40), f"0.3.1-{channel}.12.gaaaaaaaaaaaa")
        self.assertEqual(version.channel_version("0.3.1", "production", 12, "a" * 40), "0.3.1")

    def test_rejects_invalid_identity(self):
        for base, channel, sequence, revision in (("0.3.1-alpha", "alpha", 1, "a" * 40), ("0.3.1", "oops", 1, "a" * 40), ("0.3.1", "rc", 0, "a" * 40), ("0.3.1", "rc", 1, "short")):
            with self.assertRaises(ValueError):
                version.channel_version(base, channel, sequence, revision)

    def test_promotion_order_and_forks(self):
        promotion.validate("alpha", "feature/example", False)
        for base, head in (("beta", "alpha"), ("rc", "beta"), ("main", "rc")):
            promotion.validate(base, head, True)
            with self.assertRaises(ValueError):
                promotion.validate(base, "feature/skip", True)
            with self.assertRaises(ValueError):
                promotion.validate(base, head, False)

    def test_clean_source_and_ci_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
            git("init", "-b", "alpha")
            git("config", "user.name", "Release Test")
            git("config", "user.email", "test@example.invalid")
            (cwd / "Cargo.toml").write_text('[package]\nversion="0.3.1"\n')
            git("add", "Cargo.toml")
            git("commit", "-m", "test fixture")
            info = version.build_info(cwd=cwd, environment={})
            self.assertEqual(info["channel"], "alpha")
            self.assertIn(git("rev-parse", "HEAD")[:12], info["version"])
            with self.assertRaises(ValueError):
                version.build_info("production", cwd, {})
            with self.assertRaises(ValueError):
                version.build_info(cwd=cwd, environment={"GITHUB_SHA": "b" * 40})
            (cwd / "Cargo.toml").write_text('[package]\nversion="0.3.2"\n')
            with self.assertRaises(ValueError):
                version.build_info(cwd=cwd, environment={"CI": "true"})
            self.assertTrue(version.build_info("development", cwd, {})["version"].endswith(".dirty"))
