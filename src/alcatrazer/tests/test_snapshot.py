"""
Tests for src/snapshot.py — snapshot extraction from outer repo into workspace.

Phase 1: Default branch detection and git repo validation.
Phase 2: Snapshot extraction, .gitignore filtering, exclusions, initial commit.
Phase 3: Orchestrator (snapshot_workspace) — full flow integration.
"""

import os
import subprocess

# TODO: Remove once pyproject.toml handles src layout (Step 0.9)
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from alcatrazer import snapshot, state


def git(repo: str, *args: str) -> str:
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_repo(path: str, branch: str = "main") -> None:
    """Create a git repo with one commit on the given branch."""
    subprocess.run(
        ["git", "init", "-b", branch, path],
        capture_output=True,
        check=True,
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
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp)
            root = snapshot.require_git_repo(tmp)
            self.assertEqual(Path(root), Path(tmp).resolve())

    def test_returns_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp)
            subdir = Path(tmp) / "sub" / "deep"
            subdir.mkdir(parents=True)
            root = snapshot.require_git_repo(str(subdir))
            self.assertEqual(Path(root), Path(tmp).resolve())

    def test_raises_for_non_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(snapshot.NotAGitRepoError):
            snapshot.require_git_repo(tmp)

    def test_error_message_is_informative(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(snapshot.NotAGitRepoError) as ctx:
                snapshot.require_git_repo(tmp)
            self.assertIn("not a git repository", str(ctx.exception).lower())


# ── Unit tests: detect_default_branch ────────────────────────────────


class TestDetectDefaultBranch(unittest.TestCase):
    """Verify three-tier default branch detection."""

    def test_detects_main_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            self.assertEqual(snapshot.detect_default_branch(tmp), "main")

    def test_detects_master_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="master")
            self.assertEqual(snapshot.detect_default_branch(tmp), "master")

    def test_origin_head_takes_priority(self):
        """When origin/HEAD exists, it wins over local branch existence."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a "remote" repo with main branch
            remote = Path(tmp) / "remote"
            remote.mkdir()
            make_repo(str(remote), branch="main")

            # Clone it — this sets origin/HEAD automatically
            local = Path(tmp) / "local"
            subprocess.run(
                ["git", "clone", str(remote), str(local)],
                capture_output=True,
                check=True,
            )
            # Also create a master branch locally
            git(str(local), "branch", "master")

            # origin/HEAD points to main, so main should win
            self.assertEqual(snapshot.detect_default_branch(str(local)), "main")

    def test_origin_head_points_to_master(self):
        """origin/HEAD pointing to master is respected."""
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / "remote"
            remote.mkdir()
            make_repo(str(remote), branch="master")

            local = Path(tmp) / "local"
            subprocess.run(
                ["git", "clone", str(remote), str(local)],
                capture_output=True,
                check=True,
            )
            self.assertEqual(snapshot.detect_default_branch(str(local)), "master")

    def test_returns_none_for_empty_repo(self):
        """A freshly git-init'd repo with no commits returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["git", "init", tmp],
                capture_output=True,
                check=True,
            )
            self.assertIsNone(snapshot.detect_default_branch(tmp))

    def test_raises_when_both_exist_without_origin_head(self):
        """If both main and master exist locally with no origin/HEAD, fail."""
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            git(tmp, "branch", "master")

            with self.assertRaises(snapshot.AmbiguousBranchError):
                snapshot.detect_default_branch(tmp)

    def test_ambiguous_error_message_lists_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, branch="main")
            git(tmp, "branch", "master")

            with self.assertRaises(snapshot.AmbiguousBranchError) as ctx:
                snapshot.detect_default_branch(tmp)
            msg = str(ctx.exception)
            self.assertIn("main", msg)
            self.assertIn("master", msg)


# ── Unit tests: extract_snapshot ─────────────────────────────────────


class TestExtractSnapshot(unittest.TestCase):
    """Verify git archive extraction into workspace."""

    def test_extracts_files_from_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertTrue(Path(workspace, "file.txt").exists())
            self.assertEqual(Path(workspace, "file.txt").read_text(), "hello")

    def test_extracts_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            # Add a nested file
            nested = Path(outer) / "src" / "lib"
            nested.mkdir(parents=True)
            (nested / "core.py").write_text("print('hi')")
            git(outer, "add", ".")
            git(outer, "commit", "-m", "add nested")

            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertEqual(
                Path(workspace, "src", "lib", "core.py").read_text(),
                "print('hi')",
            )

    def test_noop_for_none_branch(self):
        """None branch (empty repo) is a no-op — no files extracted."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            # No outer repo needed — None means greenfield
            snapshot.extract_snapshot(tmp, None, workspace)
            # Workspace should remain empty (only dirs we created)
            files = list(Path(workspace).iterdir())
            self.assertEqual(files, [])

    def test_only_tracked_files_extracted(self):
        """Untracked files in outer repo are not in the snapshot."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            # Create untracked file
            Path(outer, "untracked.txt").write_text("secret")

            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertTrue(Path(workspace, "file.txt").exists())
            self.assertFalse(Path(workspace, "untracked.txt").exists())


# ── Unit tests: filter_gitignore ─────────────────────────────────────


class TestFilterGitignore(unittest.TestCase):
    """Verify .alcatrazer/ rule is removed from .gitignore."""

    def test_removes_alcatrazer_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            gitignore = Path(tmp) / ".gitignore"
            gitignore.write_text("node_modules/\n.alcatrazer/\n*.pyc\n")
            snapshot.filter_gitignore(tmp)
            self.assertEqual(gitignore.read_text(), "node_modules/\n*.pyc\n")

    def test_removes_alcatrazer_rule_without_trailing_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            gitignore = Path(tmp) / ".gitignore"
            gitignore.write_text(".alcatrazer\nother\n")
            snapshot.filter_gitignore(tmp)
            self.assertEqual(gitignore.read_text(), "other\n")

    def test_does_not_filter_alcatrazer_substring(self):
        """Rules like .alcatrazer-something/ must NOT be filtered."""
        with tempfile.TemporaryDirectory() as tmp:
            gitignore = Path(tmp) / ".gitignore"
            gitignore.write_text(".alcatrazer-tools/\n.alcatrazer/\n")
            snapshot.filter_gitignore(tmp)
            self.assertEqual(gitignore.read_text(), ".alcatrazer-tools/\n")

    def test_removes_file_if_empty_after_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            gitignore = Path(tmp) / ".gitignore"
            gitignore.write_text(".alcatrazer/\n")
            snapshot.filter_gitignore(tmp)
            self.assertFalse(gitignore.exists())

    def test_noop_if_no_gitignore(self):
        """No .gitignore file — nothing to filter, no error."""
        with tempfile.TemporaryDirectory() as tmp:
            snapshot.filter_gitignore(tmp)  # should not raise

    def test_preserves_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            gitignore = Path(tmp) / ".gitignore"
            gitignore.write_text("# Build output\n\n.alcatrazer/\ndist/\n")
            snapshot.filter_gitignore(tmp)
            self.assertEqual(gitignore.read_text(), "# Build output\n\ndist/\n")


# ── Unit tests: exclude .alcatrazer/ and .env ─────────────────────────


class TestExclusions(unittest.TestCase):
    """Verify .alcatrazer/ and .env are excluded even if tracked."""

    def test_env_excluded_even_if_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            # Track .env
            Path(outer, ".env").write_text("SECRET_KEY=abc")
            git(outer, "add", ".env")
            git(outer, "commit", "-m", "add env")

            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertFalse(Path(workspace, ".env").exists())

    def test_alcatrazer_dir_excluded_even_if_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            # Track .alcatrazer/ contents
            alcatrazer = Path(outer) / ".alcatrazer"
            alcatrazer.mkdir()
            (alcatrazer / "uid").write_text("9999")
            git(outer, "add", ".alcatrazer/uid")
            git(outer, "commit", "-m", "add alcatrazer")

            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertFalse(Path(workspace, ".alcatrazer").exists())

    def test_regular_files_not_excluded(self):
        """Sanity check — normal files come through."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(workspace)
            make_repo(outer)
            Path(outer, ".env.example").write_text("KEY=")
            git(outer, "add", ".env.example")
            git(outer, "commit", "-m", "add example")

            snapshot.extract_snapshot(outer, "main", workspace)
            self.assertTrue(Path(workspace, ".env.example").exists())


# ── Unit tests: create_initial_commit ────────────────────────────────


class TestCreateInitialCommit(unittest.TestCase):
    """Verify the initial commit in the workspace."""

    def test_creates_commit_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")
            # Add a file to stage
            Path(workspace, "app.py").write_text("print('hello')")

            snapshot.create_initial_commit(workspace)

            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_commit_message_is_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")
            Path(workspace, "x.txt").write_text("x")

            snapshot.create_initial_commit(workspace)

            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_all_files_are_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")
            Path(workspace, "a.txt").write_text("a")
            Path(workspace, "b.txt").write_text("b")
            subdir = Path(workspace) / "sub"
            subdir.mkdir()
            (subdir / "c.txt").write_text("c")

            snapshot.create_initial_commit(workspace)

            status = git(workspace, "status", "--porcelain")
            self.assertEqual(status, "")  # clean working tree

    def test_empty_commit_for_greenfield(self):
        """No files → empty initial commit (allow-empty)."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")

            snapshot.create_initial_commit(workspace)

            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_exactly_one_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")
            Path(workspace, "file.txt").write_text("data")

            snapshot.create_initial_commit(workspace)

            count = git(workspace, "rev-list", "--count", "HEAD")
            self.assertEqual(count, "1")

    def test_commit_uses_workspace_identity(self):
        """Commit must use the identity configured in the workspace, not host."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = tmp
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Alcatraz Agent")
            git(workspace, "config", "user.email", "alcatraz@localhost")
            Path(workspace, "f.txt").write_text("x")

            snapshot.create_initial_commit(workspace)

            author = git(workspace, "log", "--format=%an <%ae>")
            self.assertEqual(author, "Alcatraz Agent <alcatraz@localhost>")


# ── Integration tests: snapshot_workspace orchestrator ────────────────


def init_workspace(workspace: str) -> None:
    """Simulate what initialize_alcatraz.sh does before calling snapshot."""
    subprocess.run(["git", "init", workspace], capture_output=True, check=True)
    git(workspace, "config", "user.name", "Alcatraz Agent")
    git(workspace, "config", "user.email", "alcatraz@localhost")
    git(workspace, "config", "commit.gpgsign", "false")


class TestSnapshotWorkspace(unittest.TestCase):
    """Integration tests for the full snapshot_workspace orchestrator."""

    def test_full_flow_with_existing_repo(self):
        """Outer repo with files → workspace has snapshot + initial commit."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            # Add .gitignore with .alcatrazer/ rule
            Path(outer, ".gitignore").write_text("node_modules/\n.alcatrazer/\n")
            git(outer, "add", ".gitignore")
            git(outer, "commit", "-m", "add gitignore")

            init_workspace(workspace)
            snapshot.snapshot_workspace(outer, workspace)

            # Files from outer are in workspace
            self.assertTrue(Path(workspace, "file.txt").exists())
            self.assertEqual(Path(workspace, "file.txt").read_text(), "hello")
            # .gitignore exists but without .alcatrazer/ rule
            gitignore = Path(workspace, ".gitignore").read_text()
            self.assertIn("node_modules/", gitignore)
            self.assertNotIn(".alcatrazer/", gitignore)
            # Exactly one commit
            count = git(workspace, "rev-list", "--count", "HEAD")
            self.assertEqual(count, "1")
            # Correct message
            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_full_flow_with_empty_repo(self):
        """Empty outer repo (no commits) → workspace has empty initial commit."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            subprocess.run(
                ["git", "init", outer],
                capture_output=True,
                check=True,
            )
            init_workspace(workspace)
            snapshot.snapshot_workspace(outer, workspace)

            # Empty commit exists
            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")
            # No files besides .git
            files = [f.name for f in Path(workspace).iterdir() if f.name != ".git"]
            self.assertEqual(files, [])

    def test_full_flow_excludes_env_and_alcatrazer(self):
        """Even if .env and .alcatrazer/ are tracked, they don't enter workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            Path(outer, ".env").write_text("SECRET=x")
            alcatrazer = Path(outer, ".alcatrazer")
            alcatrazer.mkdir()
            (alcatrazer / "uid").write_text("9999")
            git(outer, "add", ".")
            git(outer, "commit", "-m", "add secrets")

            init_workspace(workspace)
            snapshot.snapshot_workspace(outer, workspace)

            self.assertFalse(Path(workspace, ".env").exists())
            self.assertFalse(Path(workspace, ".alcatrazer").exists())
            self.assertTrue(Path(workspace, "file.txt").exists())

    def test_not_a_git_repo_raises(self):
        """Running snapshot_workspace from a non-git dir raises."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(outer)
            os.makedirs(workspace)
            with self.assertRaises(snapshot.NotAGitRepoError):
                snapshot.snapshot_workspace(outer, workspace)

    def test_no_outer_history_leaks(self):
        """Workspace must have no trace of outer repo's git history."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            # Add multiple commits to outer
            Path(outer, "second.txt").write_text("two")
            git(outer, "add", "second.txt")
            git(outer, "commit", "-m", "second commit with details")

            init_workspace(workspace)
            snapshot.snapshot_workspace(outer, workspace)

            # Only one commit in workspace
            count = git(workspace, "rev-list", "--count", "HEAD")
            self.assertEqual(count, "1")
            # Outer commit messages not visible
            log = git(workspace, "log", "--format=%s")
            self.assertNotIn("second commit", log)
            self.assertNotIn("first commit", log)


# ── Unit tests: count_unpromoted_commits ─────────────────────────────


class TestCountUnpromotedCommits(unittest.TestCase):
    """Verify detection of unpromoted commits for --reset warning."""

    def _make_workspace_with_commits(self, workspace: str, n: int) -> None:
        """Create a workspace with n commits beyond initial."""
        subprocess.run(
            ["git", "init", workspace],
            capture_output=True,
            check=True,
        )
        git(workspace, "config", "user.name", "Alcatraz Agent")
        git(workspace, "config", "user.email", "alcatraz@localhost")
        git(workspace, "commit", "--allow-empty", "-m", "Initial commit")
        for i in range(n):
            Path(workspace, f"file{i}.txt").write_text(f"content {i}")
            git(workspace, "add", ".")
            git(workspace, "commit", "-m", f"commit {i}")

    def test_all_unpromoted_when_no_marks(self):
        """No marks file = never promoted = all commits are unpromoted."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "workspace")
            marks_dir = str(Path(tmp) / "marks")
            os.makedirs(marks_dir)
            self._make_workspace_with_commits(workspace, 3)

            count = snapshot.count_unpromoted_commits(workspace, marks_dir)
            # 1 initial + 3 work commits = 4 total unpromoted
            self.assertEqual(count, 4)

    def test_workspace_with_no_commits(self):
        """Empty workspace (git init, no commits) returns 0."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "workspace")
            marks_dir = str(Path(tmp) / "marks")
            os.makedirs(marks_dir)
            subprocess.run(
                ["git", "init", workspace],
                capture_output=True,
                check=True,
            )

            count = snapshot.count_unpromoted_commits(workspace, marks_dir)
            self.assertEqual(count, 0)

    def test_workspace_does_not_exist(self):
        """Non-existent workspace returns 0 (nothing to warn about)."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "nonexistent")
            marks_dir = str(Path(tmp) / "marks")
            os.makedirs(marks_dir)

            count = snapshot.count_unpromoted_commits(workspace, marks_dir)
            self.assertEqual(count, 0)


# ── End-to-end: snapshot.py CLI entry point ──────────────────────────

# tests/ is at src/alcatrazer/tests/ — project root is 3 levels up
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
SNAPSHOT_SCRIPT = str(PROJECT_DIR / "src" / "alcatrazer" / "snapshot.py")


class TestSnapshotCLI(unittest.TestCase):
    """Test snapshot.py as a CLI tool (called by initialize_alcatraz.sh)."""

    def test_cli_snapshots_existing_repo(self):
        """CLI with outer-repo and workspace args produces a valid snapshot."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            Path(outer, "app.py").write_text("print('hello')")
            git(outer, "add", ".")
            git(outer, "commit", "-m", "add app")

            # Prepare workspace with git init + identity (as init script does)
            init_workspace(workspace)

            result = subprocess.run(
                [sys.executable, SNAPSHOT_SCRIPT, outer, workspace],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(workspace, "file.txt").exists())
            self.assertTrue(Path(workspace, "app.py").exists())
            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_cli_snapshots_empty_repo(self):
        """CLI with empty outer repo creates workspace with empty commit."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            subprocess.run(
                ["git", "init", outer],
                capture_output=True,
                check=True,
            )
            init_workspace(workspace)

            result = subprocess.run(
                [sys.executable, SNAPSHOT_SCRIPT, outer, workspace],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            msg = git(workspace, "log", "--format=%s")
            self.assertEqual(msg, "Initial commit")

    def test_cli_fails_for_non_git_dir(self):
        """CLI exits non-zero when outer dir is not a git repo."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            os.makedirs(outer)
            os.makedirs(workspace)

            result = subprocess.run(
                [sys.executable, SNAPSHOT_SCRIPT, outer, workspace],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_cli_wrong_args(self):
        """CLI exits non-zero with wrong number of arguments."""
        result = subprocess.run(
            [sys.executable, SNAPSHOT_SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", result.stderr)

    def test_cli_gitignore_filtered(self):
        """CLI filters .alcatrazer/ from .gitignore in workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            Path(outer, ".gitignore").write_text("node_modules/\n.alcatrazer/\n*.pyc\n")
            git(outer, "add", ".gitignore")
            git(outer, "commit", "-m", "add gitignore")

            init_workspace(workspace)
            subprocess.run(
                [sys.executable, SNAPSHOT_SCRIPT, outer, workspace],
                capture_output=True,
                text=True,
                check=True,
            )

            gitignore = Path(workspace, ".gitignore").read_text()
            self.assertNotIn(".alcatrazer/", gitignore)
            self.assertIn("node_modules/", gitignore)
            self.assertIn("*.pyc", gitignore)

    def test_cli_env_excluded(self):
        """CLI excludes .env even if tracked in outer repo."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            make_repo(outer)
            Path(outer, ".env").write_text("SECRET=abc")
            git(outer, "add", ".env")
            git(outer, "commit", "-m", "add env")

            init_workspace(workspace)
            subprocess.run(
                [sys.executable, SNAPSHOT_SCRIPT, outer, workspace],
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertFalse(Path(workspace, ".env").exists())
            # But other files come through
            self.assertTrue(Path(workspace, "file.txt").exists())


# ── End-to-end: reset with unpromoted work ───────────────────────────


class TestResetUnpromotedWarning(unittest.TestCase):
    """Test the --reset flow's unpromoted work detection.

    These test the Python detection layer end-to-end, not the bash
    interactive prompt (which requires Docker and is covered by smoke tests).
    """

    def test_reset_detects_unpromoted_after_agent_work(self):
        """Full scenario: snapshot → agents commit → detect unpromoted."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            marks_dir = str(Path(tmp) / "marks")
            os.makedirs(marks_dir)

            # Outer repo with files
            make_repo(outer)

            # Initialize workspace with snapshot
            init_workspace(workspace)
            snapshot.snapshot_workspace(outer, workspace)

            # Simulate agent work inside workspace
            Path(workspace, "agent_work.py").write_text("# agent code")
            git(workspace, "add", ".")
            git(workspace, "commit", "-m", "agent: add feature")
            Path(workspace, "agent_work2.py").write_text("# more code")
            git(workspace, "add", ".")
            git(workspace, "commit", "-m", "agent: add another feature")

            # All 3 commits (initial + 2 agent) are unpromoted
            count = snapshot.count_unpromoted_commits(workspace, marks_dir)
            self.assertEqual(count, 3)


# ── current_branch + non-default-branch warning ──────────────────────


class TestCurrentBranch(unittest.TestCase):
    """Unit tests for the current_branch() helper."""

    def test_returns_branch_name_when_on_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = str(Path(tmp) / "repo")
            make_repo(repo, branch="main")
            self.assertEqual(snapshot.current_branch(repo), "main")

    def test_returns_feature_branch_when_checked_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = str(Path(tmp) / "repo")
            make_repo(repo, branch="main")
            subprocess.run(
                ["git", "-C", repo, "checkout", "-b", "abc"],
                capture_output=True,
                check=True,
            )
            self.assertEqual(snapshot.current_branch(repo), "abc")

    def test_returns_none_for_detached_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = str(Path(tmp) / "repo")
            make_repo(repo, branch="main")
            sha = git(repo, "rev-parse", "HEAD")
            subprocess.run(
                ["git", "-C", repo, "checkout", "--detach", sha],
                capture_output=True,
                check=True,
            )
            self.assertIsNone(snapshot.current_branch(repo))

    def test_returns_none_for_empty_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = str(Path(tmp) / "repo")
            subprocess.run(
                ["git", "init", repo],
                capture_output=True,
                check=True,
            )
            self.assertIsNone(snapshot.current_branch(repo))


class TestSnapshotRecordsState(unittest.TestCase):
    """Phase 1 (change_promotion_machinery.md §"Recording state",
    L411-434): `snapshot_workspace` records workspace state in
    `<alcatraz_dir>/state.json` after the initial commit.

    Step 1.1 covers `inner_root` (SHA of the workspace's `Initial
    commit`). It's the format-patch range boundary used by every
    later promotion cycle, so the snapshot orchestrator is the
    canonical writer (written once, never updated).
    """

    def test_records_inner_root_as_initial_commit_sha(self):
        """`state.json.inner_root` equals `git rev-parse HEAD` of the
        workspace, taken right after `create_initial_commit` runs.

        Spec: change_promotion_machinery.md L774-777 (Step 1.1).
        """
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            make_repo(outer)
            init_workspace(workspace)

            snapshot.snapshot_workspace(outer, workspace, str(alcatraz_dir))

            expected_sha = git(workspace, "rev-parse", "HEAD")
            recorded = state.load_state(alcatraz_dir).get("inner_root")
            self.assertEqual(recorded, expected_sha)

    def test_records_pinned_branch_as_outer_current_branch(self):
        """`state.json.pinned_branch` equals the name of outer's
        currently-checked-out branch at snapshot time. Written once,
        never updated — promotion is bound to this branch for the life
        of the workspace.

        Spec: change_promotion_machinery.md L783-788 (Step 1.3).
        """
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            make_repo(outer, branch="main")
            # Move outer to a feature branch so the recorded pin is
            # observably different from the default branch.
            subprocess.run(
                ["git", "-C", outer, "checkout", "-b", "feat/X"],
                capture_output=True,
                check=True,
            )
            init_workspace(workspace)

            snapshot.snapshot_workspace(outer, workspace, str(alcatraz_dir))

            recorded = state.load_state(alcatraz_dir).get("pinned_branch")
            self.assertEqual(recorded, "feat/X")


class TestSnapshotUsesCurrentBranchTree(unittest.TestCase):
    """Phase 1 (change_promotion_machinery.md L790-795): the workspace
    is built from outer's current branch tree, not the default branch.
    Replaces the prior "main branch only" rule which is incompatible
    with the starting-branch promotion contract.
    """

    def test_workspace_mirrors_current_branch_not_default(self):
        """Outer on `feat/X` with a file absent from `main` — the
        workspace must contain that file. Until Step 1.6, snapshot
        extracts the default-branch tree, so the file is missing.

        Spec: change_promotion_machinery.md L790-792 (Step 1.5).
        """
        with tempfile.TemporaryDirectory() as tmp:
            outer = str(Path(tmp) / "outer")
            workspace = str(Path(tmp) / "workspace")
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            make_repo(outer, branch="main")
            # Create feat/X with a file that is NOT on main.
            subprocess.run(
                ["git", "-C", outer, "checkout", "-b", "feat/X"],
                capture_output=True,
                check=True,
            )
            Path(outer, "feat_only.txt").write_text("feature work")
            git(outer, "add", "feat_only.txt")
            git(outer, "commit", "-m", "feat: feature-only file")
            init_workspace(workspace)

            snapshot.snapshot_workspace(outer, workspace, str(alcatraz_dir))

            self.assertTrue(
                Path(workspace, "feat_only.txt").exists(),
                "workspace should contain feat/X's tree, not main's",
            )
            self.assertEqual(
                Path(workspace, "feat_only.txt").read_text(),
                "feature work",
            )


if __name__ == "__main__":
    unittest.main()
