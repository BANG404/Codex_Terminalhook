"""Exercise semantic versions against real release and promotion Git histories."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/version.py"
SPEC = importlib.util.spec_from_file_location("release_version", SCRIPT)
version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version)


class CommitImpactTests(unittest.TestCase):
    def test_conventional_headers_and_breaking_footers(self):
        cases = {
            "fix: repair output": 1,
            "fix(worker): preserve Unicode": 1,
            "perf: reduce startup cost": 1,
            "feat(api): add background tasks": 2,
            "FEAT: add a command": 2,
            "refactor!: remove old tools": 3,
            "chore(api)!: replace protocol": 3,
            "fix: change API\n\nBREAKING CHANGE: old clients must upgrade": 3,
            "docs: describe migration\n\nBREAKING-CHANGE: remove a supported field": 3,
            "docs: document usage": 0,
            "chore: upgrade tooling": 0,
            "ci: parallelize builds": 0,
            "test: cover retries": 0,
            "refactor: extract helper": 0,
            "Merge branch 'feat/new-command'": 0,
            "not conventional\n\nBREAKING CHANGE: arbitrary text": 0,
            "feat:missing space": 0,
            "feat: ": 0,
            "fix: explain marker\n\nbreaking change: lower-case text": 1,
            "fix: explain marker\n\nBREAKING CHANGE:": 1,
            "": 0,
        }
        for message, impact in cases.items():
            with self.subTest(message=message):
                self.assertEqual(version.commit_bump(message), impact)


class GitVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="terminalhook version ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-b", "alpha")
        self.git("config", "user.name", "Version Test")
        self.git("config", "user.email", "version@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        (self.root / "Cargo.toml").write_text('[package]\nversion = "0.5.0"\n', encoding="utf-8")
        self.counter = 0
        self.initial = self.commit("chore: initialize repository")
        self.tag("v0.5.0")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True,
                                       encoding="utf-8", stderr=subprocess.PIPE).strip()

    def commit(self, message, empty=False):
        self.counter += 1
        if not empty:
            (self.root / "changes.txt").write_text(str(self.counter), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, name):
        self.git("tag", "-a", name, "-m", name)

    def info(self, channel="auto"):
        return version.build_info(channel, self.root, {})

    def test_patch_minor_and_breaking_choose_one_highest_bump(self):
        self.commit("fix: repair retries")
        self.assertEqual(self.info()["baseVersion"], "0.5.1")
        self.commit("fix: preserve stderr")
        self.assertEqual(self.info()["baseVersion"], "0.5.1")
        self.commit("feat(ui): add a panel")
        self.assertEqual(self.info()["baseVersion"], "0.6.0")
        self.commit("refactor!: replace protocol")
        self.assertEqual(self.info()["baseVersion"], "0.6.0")

    def test_breaking_one_x_increments_major_and_resets_minor_patch(self):
        self.tag("v1.4.9")
        self.commit("chore: retire interface\n\nBREAKING-CHANGE: change wire format")
        self.assertEqual(self.info()["baseVersion"], "2.0.0")

    def test_bootstrap_and_non_release_changes_keep_the_baseline(self):
        self.git("tag", "-d", "v0.5.0")
        self.commit("feat: bootstrap first release")
        self.assertEqual(self.info()["baseVersion"], "0.5.0")
        self.tag("v0.5.0")
        self.commit("docs: clarify usage")
        self.commit("chore: update tooling")
        self.assertEqual(self.info()["baseVersion"], "0.5.0")

    def test_numeric_tag_order_ignores_prereleases_and_unrelated_history(self):
        self.tag("v0.9.0")
        self.tag("v0.10.0")
        self.git("checkout", "-b", "unrelated")
        self.commit("feat: unrelated work")
        self.tag("v9.0.0")
        self.git("checkout", "alpha")
        self.commit("fix: repair supported code")
        self.tag("v8.0.0-alpha.1")
        self.tag("v01.0.0")
        self.assertEqual(self.info()["baseVersion"], "0.10.1")

    def test_merge_message_does_not_invent_a_breaking_change(self):
        self.git("checkout", "-b", "codex/fix")
        self.commit("fix: repair package")
        self.git("checkout", "alpha")
        self.git("merge", "--no-ff", "codex/fix", "-m", "feat!: misleading merge wrapper")
        self.assertEqual(self.info()["baseVersion"], "0.5.1")

    def test_promotions_share_target_and_production_retry_is_identical(self):
        for branch in ("beta", "rc", "main"):
            self.git("branch", branch)
        feature = self.commit("feat: add task panels")
        alpha = self.info()
        self.assertEqual(alpha["baseVersion"], "0.6.0")
        for source, branch, channel in (("alpha", "beta", "beta"), ("beta", "rc", "rc"),
                                         ("rc", "main", "production")):
            self.git("checkout", branch)
            self.git("merge", "--no-ff", source, "-m", f"chore(release): promote {source} to {branch}")
            info = self.info()
            self.assertEqual(info["baseVersion"], alpha["baseVersion"])
            self.assertEqual(info["channel"], channel)
            if channel != "production":
                self.assertTrue(info["version"].startswith("0.6.0-" + channel + "."))
        self.assertEqual(info["version"], "0.6.0")
        self.tag("v0.6.0")
        self.assertEqual(self.info(), info, "Publishing the stable tag must not change build provenance")
        self.git("checkout", "alpha")
        self.assertEqual(self.info(), alpha, "A main-only stable tag must not re-version the same alpha source")
        self.assertEqual(version.release_source(self.git("rev-parse", "v0.6.0^{commit}"), self.root), feature)
        self.commit("fix: repair the next cycle")
        self.assertEqual(self.info()["baseVersion"], "0.6.1")
        self.git("checkout", "rc")
        self.assertEqual(self.info()["baseVersion"], "0.6.0", "New alpha commits cannot change an older rc target")

    def test_stable_tag_on_main_is_the_baseline_before_it_reaches_alpha(self):
        self.git("tag", "-d", "v0.5.0")
        self.git("branch", "main")
        self.commit("docs: prepare initial release")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "alpha", "-m", "chore(release): release initial source")
        self.tag("v0.5.0")
        self.git("checkout", "alpha")
        self.commit("fix: repair output")
        self.assertEqual(self.info()["baseVersion"], "0.5.1")

    def test_publishing_future_source_does_not_change_an_older_candidate(self):
        self.commit("feat: first candidate")
        self.git("branch", "candidate")
        self.commit("feat: second candidate")
        self.tag("v1.0.0")
        self.git("checkout", "candidate")
        self.assertEqual(self.info("development")["baseVersion"], "0.6.0")

    def test_published_candidate_is_pinned_when_an_older_cycle_goes_stable(self):
        older = self.commit("feat: older candidate")
        self.commit("feat: next candidate")
        alpha = self.info()
        self.tag("v" + alpha["version"])
        self.git("tag", "-a", "v0.6.0", older, "-m", "Release older candidate")
        self.assertEqual(self.info(), alpha)
        self.git("checkout", "-b", "beta", self.initial)
        self.git("merge", "--no-ff", "alpha", "-m", "chore(release): promote alpha to beta")
        self.assertEqual(self.info()["baseVersion"], alpha["baseVersion"])

    def test_stage_tags_require_consistent_target_and_real_source_identity(self):
        self.commit("feat: candidate")
        alpha = self.info()
        for invalid in (f"v0.6.0-alpha.999.g{alpha['revision'][:12]}",
                        f"v0.6.0-alpha.{alpha['sequence']}.g{'a' * 12}"):
            self.tag(invalid)
            with self.assertRaisesRegex(ValueError, "source identity differs"):
                self.info()
            self.git("tag", "-d", invalid)
        self.tag("v" + alpha["version"])
        self.tag(f"v0.7.0-beta.{alpha['sequence']}.g{alpha['revision'][:12]}")
        with self.assertRaisesRegex(ValueError, "Stage tags disagree"):
            self.info()
        self.git("tag", "-d", f"v0.7.0-beta.{alpha['sequence']}.g{alpha['revision'][:12]}")
        self.git("tag", "-a", "v0.7.0", self.initial, "-m", "Newer stable baseline")
        with self.assertRaisesRegex(ValueError, "older than"):
            self.info()

    def test_shallow_clone_fails_instead_of_silently_losing_release_history(self):
        self.commit("feat: new candidate")
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "shallow"
            self.git("clone", "--depth=1", self.root.as_uri(), str(clone))
            with self.assertRaisesRegex(ValueError, "full Git history"):
                version.build_info("development", clone, {})

    def test_cargo_baseline_remains_a_floor(self):
        (self.root / "Cargo.toml").write_text('[package]\nversion = "1.0.0"\n', encoding="utf-8")
        self.commit("chore: bootstrap stable API")
        self.assertEqual(self.info()["baseVersion"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
