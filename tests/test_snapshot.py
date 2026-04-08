"""
Tests for src/snapshot.py — snapshot extraction from outer repo into workspace.

Phase 1: Default branch detection and git repo validation.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

# Add src/ to path so we can import snapshot directly
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def git(repo: str, *args: str) -> str:
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def make_repo(path: str, branch: str = "main") -> None:
    """Create a git repo with one commit on the given branch."""
    subprocess.run(
        ["git", "init", "-b", branch, path],
        capture_output=True, check=True,
    )
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@test.com")
    Path(path, "file.txt").write_text("hello")
    git(path, "add", "file.txt")
    git(path, "commit", "-m", "first commit")


# ── Unit tests: require_git_repo ────────────────────────────────────


class TestRequireGitRepo(unittest.TestCase):
    """Verify that require_git_repo validates the outer directory."""

    def test_returns_repo_root_for_valid_repo(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp)
            root = snapshot.require_git_repo(tmp)
            self.assertEqual(Path(root), Path(tmp).resolve())

    def test_returns_root_from_subdirectory(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp)
            subdir = Path(tmp) / "sub" / "deep"
            subdir.mkdir(parents=True)
            root = snapshot.require_git_repo(str(subdir))
            self.assertEqual(Path(root), Path(tmp).resolve())

    def test_raises_for_non_git_directory(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(snapshot.NotAGitRepoError):
                snapshot.require_git_repo(tmp)

    def test_error_message_is_informative(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(snapshot.NotAGitRepoError) as ctx:
                snapshot.require_git_repo(tmp)
            self.assertIn("not a git repository", str(ctx.exception).lower())


# ── Unit tests: detect_default_branch ────────────────────────────────


class TestDetectDefaultBranch(unittest.TestCase):
    """Verify three-tier default branch detection."""

    def test_detects_main_branch(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            self.assertEqual(snapshot.detect_default_branch(tmp), "main")

    def test_detects_master_branch(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="master")
            self.assertEqual(snapshot.detect_default_branch(tmp), "master")

    def test_origin_head_takes_priority(self):
        """When origin/HEAD exists, it wins over local branch existence."""
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            # Create a "remote" repo with main branch
            remote = Path(tmp) / "remote"
            remote.mkdir()
            make_repo(str(remote), branch="main")

            # Clone it — this sets origin/HEAD automatically
            local = Path(tmp) / "local"
            subprocess.run(
                ["git", "clone", str(remote), str(local)],
                capture_output=True, check=True,
            )
            # Also create a master branch locally
            git(str(local), "branch", "master")

            # origin/HEAD points to main, so main should win
            self.assertEqual(snapshot.detect_default_branch(str(local)), "main")

    def test_origin_head_points_to_master(self):
        """origin/HEAD pointing to master is respected."""
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / "remote"
            remote.mkdir()
            make_repo(str(remote), branch="master")

            local = Path(tmp) / "local"
            subprocess.run(
                ["git", "clone", str(remote), str(local)],
                capture_output=True, check=True,
            )
            self.assertEqual(snapshot.detect_default_branch(str(local)), "master")

    def test_returns_none_for_empty_repo(self):
        """A freshly git-init'd repo with no commits returns None."""
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["git", "init", tmp], capture_output=True, check=True,
            )
            self.assertIsNone(snapshot.detect_default_branch(tmp))

    def test_raises_when_both_exist_without_origin_head(self):
        """If both main and master exist locally with no origin/HEAD, fail."""
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            git(tmp, "branch", "master")

            with self.assertRaises(snapshot.AmbiguousBranchError):
                snapshot.detect_default_branch(tmp)

    def test_ambiguous_error_message_lists_branches(self):
        import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            git(tmp, "branch", "master")

            with self.assertRaises(snapshot.AmbiguousBranchError) as ctx:
                snapshot.detect_default_branch(tmp)
            msg = str(ctx.exception)
            self.assertIn("main", msg)
            self.assertIn("master", msg)


if __name__ == "__main__":
    unittest.main()
