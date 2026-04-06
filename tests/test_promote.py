"""
Tests for src/promote.py — promotion from inner (alcatraz) to outer repo.

Integration tests use real git repos (fast-export/fast-import pipeline).
Unit tests use mocking for identity resolution and stream rewriting.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Add src/ to path so we can import promote directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import promote as promote_mod

SEED_SCRIPT = str(Path(__file__).resolve().parent / "seed_alcatraz.sh")

ALCATRAZ_NAME = "Alcatraz Agent"
ALCATRAZ_EMAIL = "alcatraz@localhost"
PROMOTED_NAME = "Test User"
PROMOTED_EMAIL = "test@example.com"


def git(repo: str, *args: str) -> str:
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ── Unit tests (no git repos needed) ────────────────────────────────


class TestRewriteIdentity(unittest.TestCase):
    """Unit tests for the fast-export stream rewriting."""

    def test_rewrites_author_and_committer(self):
        stream = (
            "commit refs/heads/main\n"
            "author Old Name <old@email.com> 1234567890 +0000\n"
            "committer Old Name <old@email.com> 1234567890 +0000\n"
            "data 5\nhello\n"
        )
        result = promote_mod.rewrite_identity(stream, "New Name", "new@email.com")
        self.assertIn("author New Name <new@email.com> 1234567890 +0000", result)
        self.assertIn("committer New Name <new@email.com> 1234567890 +0000", result)

    def test_preserves_timestamps(self):
        stream = "author X <x@x> 9999999999 +0530\n"
        result = promote_mod.rewrite_identity(stream, "Y", "y@y")
        self.assertIn("9999999999 +0530", result)

    def test_handles_multiple_commits(self):
        stream = (
            "author A <a@a> 111 +0000\n"
            "committer A <a@a> 111 +0000\n"
            "author B <b@b> 222 +0000\n"
            "committer B <b@b> 222 +0000\n"
        )
        result = promote_mod.rewrite_identity(stream, "Z", "z@z")
        self.assertEqual(result.count("author Z <z@z>"), 2)
        self.assertEqual(result.count("committer Z <z@z>"), 2)

    def test_does_not_touch_data_sections(self):
        stream = (
            "author A <a@a> 111 +0000\n"
            "data 20\n"
            "author line in body\n"
        )
        result = promote_mod.rewrite_identity(stream, "Z", "z@z")
        # The "author line in body" doesn't match the pattern (no timestamp)
        self.assertIn("author line in body", result)


class TestResolveIdentity(unittest.TestCase):
    """Unit tests for the three-layer identity resolution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = os.path.join(self.tmpdir, "target")
        os.makedirs(self.target)
        subprocess.run(["git", "init", self.target], capture_output=True, check=True)
        git(self.target, "config", "commit.gpgsign", "false")
        self.toml_file = Path(self.tmpdir) / "alcatrazer.toml"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_layer1_git_config(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        name, email = promote_mod.resolve_identity(
            Path(self.target), self.toml_file, "", "",
        )
        self.assertEqual(name, "Git User")
        self.assertEqual(email, "git@user.com")

    def test_layer2_toml_overrides_git(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        self.toml_file.write_text(
            '[promotion]\nname = "TOML User"\nemail = "toml@user.com"\n'
        )
        name, email = promote_mod.resolve_identity(
            Path(self.target), self.toml_file, "", "",
        )
        self.assertEqual(name, "TOML User")
        self.assertEqual(email, "toml@user.com")

    def test_layer3_cli_overrides_all(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        self.toml_file.write_text(
            '[promotion]\nname = "TOML User"\nemail = "toml@user.com"\n'
        )
        name, email = promote_mod.resolve_identity(
            Path(self.target), self.toml_file, "CLI User", "cli@user.com",
        )
        self.assertEqual(name, "CLI User")
        self.assertEqual(email, "cli@user.com")

    def test_missing_identity_exits(self):
        # Override HOME to isolate from global git config
        import os
        fake_home = os.path.join(self.tmpdir, "fakehome")
        os.makedirs(fake_home)
        env_patch = {"HOME": fake_home, "GIT_CONFIG_GLOBAL": "/dev/null"}
        with patch.dict(os.environ, env_patch):
            with self.assertRaises(SystemExit):
                promote_mod.resolve_identity(
                    Path(self.target), self.toml_file, "", "",
                )


# ── Integration tests (real git repos) ──────────────────────────────


class PromotionTestBase(unittest.TestCase):
    """Base: creates source + target repos, seeds source."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.source = Path(self.tmpdir) / "source"
        self.target = Path(self.tmpdir) / "target"
        self.marks = Path(self.tmpdir) / "marks"

        # Create and seed source repo
        self.source.mkdir()
        subprocess.run(["git", "init", str(self.source)], capture_output=True, check=True)
        git(str(self.source), "config", "user.name", ALCATRAZ_NAME)
        git(str(self.source), "config", "user.email", ALCATRAZ_EMAIL)
        git(str(self.source), "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, str(self.source)], capture_output=True, check=True)

        # Create target repo
        self.target.mkdir()
        subprocess.run(["git", "init", str(self.target)], capture_output=True, check=True)
        git(str(self.target), "config", "user.name", PROMOTED_NAME)
        git(str(self.target), "config", "user.email", PROMOTED_EMAIL)
        git(str(self.target), "config", "commit.gpgsign", "false")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def do_promote(self):
        """Call promote() directly."""
        promote_mod.promote(self.source, self.target, self.marks,
                            PROMOTED_NAME, PROMOTED_EMAIL)

    def do_dry_run(self) -> str:
        """Call dry_run() and capture stdout."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            promote_mod.dry_run(self.source, self.marks,
                                PROMOTED_NAME, PROMOTED_EMAIL)
        return buf.getvalue()


class TestInitialPromotion(PromotionTestBase):
    """Tests after the first full promotion."""

    def setUp(self):
        super().setUp()
        self.do_promote()

    def test_same_commit_count(self):
        src = int(git(str(self.source), "rev-list", "--all", "--count"))
        tgt = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(src, tgt)

    def test_same_branches(self):
        src = sorted(git(str(self.source), "branch", "--format=%(refname:short)").splitlines())
        tgt = sorted(git(str(self.target), "branch", "--format=%(refname:short)").splitlines())
        self.assertEqual(src, tgt)

    def test_same_commit_messages(self):
        src = sorted(git(str(self.source), "log", "--all", "--topo-order", "--format=%s").splitlines())
        tgt = sorted(git(str(self.target), "log", "--all", "--topo-order", "--format=%s").splitlines())
        self.assertEqual(src, tgt)

    def test_merge_topology_preserved(self):
        def topology(repo):
            lines = git(repo, "log", "--all", "--topo-order", "--format=%s|%P").splitlines()
            result = []
            for line in lines:
                msg, _, parents = line.partition("|")
                pcount = len(parents.split()) if parents.strip() else 0
                result.append(f"{msg}|{pcount}")
            return sorted(result)

        self.assertEqual(topology(str(self.source)), topology(str(self.target)))

    def test_same_files_on_main(self):
        src = sorted(git(str(self.source), "ls-tree", "-r", "--name-only", "main").splitlines())
        tgt = sorted(git(str(self.target), "ls-tree", "-r", "--name-only", "main").splitlines())
        self.assertEqual(src, tgt)

    def test_file_contents_match(self):
        files = git(str(self.source), "ls-tree", "-r", "--name-only", "main").splitlines()
        for f in files:
            src = git(str(self.source), "show", f"main:{f}")
            tgt = git(str(self.target), "show", f"main:{f}")
            self.assertEqual(src, tgt, f"Content differs: {f}")

    def test_source_has_alcatraz_identity(self):
        authors = set(git(str(self.source), "log", "--all", "--format=%an <%ae>").splitlines())
        self.assertEqual(authors, {f"{ALCATRAZ_NAME} <{ALCATRAZ_EMAIL}>"})

    def test_target_has_promoted_author(self):
        authors = set(git(str(self.target), "log", "--all", "--format=%an <%ae>").splitlines())
        self.assertEqual(authors, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})

    def test_target_has_promoted_committer(self):
        committers = set(git(str(self.target), "log", "--all", "--format=%cn <%ce>").splitlines())
        self.assertEqual(committers, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})


class TestIncrementalPromotion(PromotionTestBase):
    """Tests for second promotion run after adding new commits."""

    def setUp(self):
        super().setUp()
        self.do_promote()

        git(str(self.source), "checkout", "main")
        (self.source / "new_feature.py").write_text("new feature\n")
        git(str(self.source), "add", "new_feature.py")
        git(str(self.source), "commit", "-m", "add new feature after first promotion")

        self.do_promote()

    def test_new_commit_promoted(self):
        src = int(git(str(self.source), "rev-list", "--all", "--count"))
        tgt = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(src, tgt)

    def test_new_commit_message_present(self):
        msgs = git(str(self.target), "log", "--all", "--format=%s").splitlines()
        self.assertIn("add new feature after first promotion", msgs)

    def test_new_commit_has_promoted_identity(self):
        author = git(str(self.target), "log", "-1", "--format=%an <%ae>", "main")
        self.assertEqual(author, f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>")


class TestDryRun(PromotionTestBase):
    """Tests for dry_run()."""

    def setUp(self):
        super().setUp()
        self.do_promote()

    def test_dry_run_up_to_date(self):
        output = self.do_dry_run()
        self.assertIn("Nothing to promote", output)

    def test_dry_run_does_not_modify_target(self):
        count_before = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.do_dry_run()
        count_after = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)

    def test_dry_run_with_pending_commits(self):
        git(str(self.source), "checkout", "main")
        (self.source / "pending1.py").write_text("pending 1\n")
        git(str(self.source), "add", "pending1.py")
        git(str(self.source), "commit", "-m", "pending commit 1")

        git(str(self.source), "checkout", "-b", "dry-run-test-branch")
        (self.source / "pending2.py").write_text("pending 2\n")
        git(str(self.source), "add", "pending2.py")
        git(str(self.source), "commit", "-m", "pending commit 2 on branch")
        git(str(self.source), "checkout", "main")

        output = self.do_dry_run()
        self.assertIn("2 commit(s) would be promoted", output)
        self.assertIn(f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>", output)

    def test_dry_run_pending_does_not_modify_target(self):
        git(str(self.source), "checkout", "main")
        (self.source / "pending.py").write_text("pending\n")
        git(str(self.source), "add", "pending.py")
        git(str(self.source), "commit", "-m", "pending")

        count_before = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.do_dry_run()
        count_after = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
