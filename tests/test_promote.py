"""
Tests for src/promote.py — promotion from inner (alcatraz) to outer repo.

Verifies:
- Same commit count, branches, messages, merge topology, file content
- Author/committer identity rewritten
- Incremental promotion (mark files)
- Dry run (up-to-date and with pending commits)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
PROMOTE_SCRIPT = str(PROJECT_DIR / "src" / "promote.py")
SEED_SCRIPT = str(PROJECT_DIR / "tests" / "seed_alcatraz.sh")

# Use the resolved Python from .alcatraz/python symlink, or fall back to current
_python_link = PROJECT_DIR / ".alcatraz" / "python"
PYTHON = str(_python_link.resolve()) if _python_link.is_symlink() else sys.executable

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


class PromotionTestBase(unittest.TestCase):
    """Base class: creates source + target repos, seeds source, runs initial promotion."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.source = os.path.join(self.tmpdir, "source")
        self.target = os.path.join(self.tmpdir, "target")
        self.marks = os.path.join(self.tmpdir, "marks")

        # Create and seed source repo
        os.makedirs(self.source)
        subprocess.run(["git", "init", self.source], capture_output=True, check=True)
        git(self.source, "config", "user.name", ALCATRAZ_NAME)
        git(self.source, "config", "user.email", ALCATRAZ_EMAIL)
        git(self.source, "config", "commit.gpgsign", "false")
        subprocess.run(
            [SEED_SCRIPT, self.source],
            capture_output=True, check=True,
        )

        # Create target repo
        os.makedirs(self.target)
        subprocess.run(["git", "init", self.target], capture_output=True, check=True)
        git(self.target, "config", "user.name", PROMOTED_NAME)
        git(self.target, "config", "user.email", PROMOTED_EMAIL)
        git(self.target, "config", "commit.gpgsign", "false")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def promote(self, dry_run=False):
        """Run promote.py with standard args. Returns CompletedProcess."""
        cmd = [
            PYTHON, PROMOTE_SCRIPT,
            "--source", self.source,
            "--target", self.target,
            "--author-name", PROMOTED_NAME,
            "--author-email", PROMOTED_EMAIL,
            "--marks-dir", self.marks,
        ]
        if dry_run:
            cmd.append("--dry-run")
        return subprocess.run(cmd, capture_output=True, text=True, check=not dry_run)


class TestInitialPromotion(PromotionTestBase):
    """Tests after the first full promotion."""

    def setUp(self):
        super().setUp()
        self.promote()

    def test_same_commit_count(self):
        source_count = int(git(self.source, "rev-list", "--all", "--count"))
        target_count = int(git(self.target, "rev-list", "--all", "--count"))
        self.assertEqual(source_count, target_count)

    def test_same_branches(self):
        source_branches = sorted(
            git(self.source, "branch", "--format=%(refname:short)").splitlines()
        )
        target_branches = sorted(
            git(self.target, "branch", "--format=%(refname:short)").splitlines()
        )
        self.assertEqual(source_branches, target_branches)

    def test_same_commit_messages(self):
        source_msgs = sorted(
            git(self.source, "log", "--all", "--topo-order", "--format=%s").splitlines()
        )
        target_msgs = sorted(
            git(self.target, "log", "--all", "--topo-order", "--format=%s").splitlines()
        )
        self.assertEqual(source_msgs, target_msgs)

    def test_merge_topology_preserved(self):
        """Parent counts per commit should match (verifies merge structure)."""

        def topology(repo):
            lines = git(repo, "log", "--all", "--topo-order", "--format=%s|%P").splitlines()
            result = []
            for line in lines:
                msg, _, parents = line.partition("|")
                pcount = len(parents.split()) if parents.strip() else 0
                result.append(f"{msg}|{pcount}")
            return sorted(result)

        self.assertEqual(topology(self.source), topology(self.target))

    def test_same_files_on_main(self):
        source_files = sorted(
            git(self.source, "ls-tree", "-r", "--name-only", "main").splitlines()
        )
        target_files = sorted(
            git(self.target, "ls-tree", "-r", "--name-only", "main").splitlines()
        )
        self.assertEqual(source_files, target_files)

    def test_file_contents_match(self):
        files = git(self.source, "ls-tree", "-r", "--name-only", "main").splitlines()
        for f in files:
            source_content = git(self.source, "show", f"main:{f}")
            target_content = git(self.target, "show", f"main:{f}")
            self.assertEqual(source_content, target_content, f"Content differs: {f}")

    def test_source_has_alcatraz_identity(self):
        authors = set(
            git(self.source, "log", "--all", "--format=%an <%ae>").splitlines()
        )
        self.assertEqual(authors, {f"{ALCATRAZ_NAME} <{ALCATRAZ_EMAIL}>"})

    def test_target_has_promoted_author(self):
        authors = set(
            git(self.target, "log", "--all", "--format=%an <%ae>").splitlines()
        )
        self.assertEqual(authors, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})

    def test_target_has_promoted_committer(self):
        committers = set(
            git(self.target, "log", "--all", "--format=%cn <%ce>").splitlines()
        )
        self.assertEqual(committers, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})


class TestIncrementalPromotion(PromotionTestBase):
    """Tests for second promotion run after adding new commits."""

    def setUp(self):
        super().setUp()
        self.promote()

        # Add a new commit to source after initial promotion
        git(self.source, "checkout", "main")
        Path(self.source, "new_feature.py").write_text("new feature\n")
        git(self.source, "add", "new_feature.py")
        git(self.source, "commit", "-m", "add new feature after first promotion")

        # Run promotion again
        self.promote()

    def test_new_commit_promoted(self):
        source_count = int(git(self.source, "rev-list", "--all", "--count"))
        target_count = int(git(self.target, "rev-list", "--all", "--count"))
        self.assertEqual(source_count, target_count)

    def test_new_commit_message_present(self):
        messages = git(self.target, "log", "--all", "--format=%s").splitlines()
        self.assertIn("add new feature after first promotion", messages)

    def test_new_commit_has_promoted_identity(self):
        author = git(self.target, "log", "-1", "--format=%an <%ae>", "main")
        self.assertEqual(author, f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>")


class TestDryRun(PromotionTestBase):
    """Tests for --dry-run flag."""

    def setUp(self):
        super().setUp()
        self.promote()

    def test_dry_run_up_to_date(self):
        result = self.promote(dry_run=True)
        self.assertIn("Nothing to promote", result.stdout)

    def test_dry_run_does_not_modify_target(self):
        count_before = int(git(self.target, "rev-list", "--all", "--count"))
        self.promote(dry_run=True)
        count_after = int(git(self.target, "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)

    def test_dry_run_with_pending_commits(self):
        # Add two commits on different branches
        git(self.source, "checkout", "main")
        Path(self.source, "pending1.py").write_text("pending 1\n")
        git(self.source, "add", "pending1.py")
        git(self.source, "commit", "-m", "pending commit 1")

        git(self.source, "checkout", "-b", "dry-run-test-branch")
        Path(self.source, "pending2.py").write_text("pending 2\n")
        git(self.source, "add", "pending2.py")
        git(self.source, "commit", "-m", "pending commit 2 on branch")
        git(self.source, "checkout", "main")

        result = self.promote(dry_run=True)
        self.assertIn("2 commit(s) would be promoted", result.stdout)
        self.assertIn(f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>", result.stdout)

    def test_dry_run_pending_does_not_modify_target(self):
        git(self.source, "checkout", "main")
        Path(self.source, "pending.py").write_text("pending\n")
        git(self.source, "add", "pending.py")
        git(self.source, "commit", "-m", "pending")

        count_before = int(git(self.target, "rev-list", "--all", "--count"))
        self.promote(dry_run=True)
        count_after = int(git(self.target, "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
