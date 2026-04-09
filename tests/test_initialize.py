"""
Tests for src/initialize_alcatraz.sh — initialization script guards.

Step 3.0: Verify init runs at repository root.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
INIT_SCRIPT = str(PROJECT_DIR / "src" / "initialize_alcatraz.sh")


class TestRepoRootGuard(unittest.TestCase):
    """Verify initialize_alcatraz.sh refuses to run outside repo root."""

    def test_fails_when_script_not_at_repo_root(self):
        """Script placed in a subdirectory (not repo root/src/) should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a git repo
            subprocess.run(
                ["git", "init", tmp], capture_output=True, check=True,
            )
            # Place the script in a wrong location: repo_root/deep/src/
            wrong_src = Path(tmp) / "deep" / "src"
            wrong_src.mkdir(parents=True)
            script_copy = wrong_src / "initialize_alcatraz.sh"
            script_copy.write_text(Path(INIT_SCRIPT).read_text())
            script_copy.chmod(0o755)

            result = subprocess.run(
                [str(script_copy)],
                capture_output=True, text=True,
                cwd=tmp,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("repository root", result.stderr.lower() + result.stdout.lower())

    def test_fails_when_not_in_git_repo(self):
        """Script in a non-git directory should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            src_dir.mkdir()
            script_copy = src_dir / "initialize_alcatraz.sh"
            script_copy.write_text(Path(INIT_SCRIPT).read_text())
            script_copy.chmod(0o755)

            result = subprocess.run(
                [str(script_copy)],
                capture_output=True, text=True,
                cwd=tmp,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("git repository", result.stderr.lower() + result.stdout.lower())

    def test_succeeds_at_repo_root(self):
        """Script at repo_root/src/ should pass the guard (may fail later on other steps)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a git repo with a commit
            subprocess.run(
                ["git", "init", tmp], capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "-C", tmp, "config", "user.name", "Test"],
                capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "-C", tmp, "config", "user.email", "test@test.com"],
                capture_output=True, check=True,
            )
            Path(tmp, "README.md").write_text("test")
            subprocess.run(
                ["git", "-C", tmp, "add", "."], capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "-C", tmp, "commit", "-m", "init"],
                capture_output=True, check=True,
            )

            # Place script correctly at repo_root/src/
            src_dir = Path(tmp) / "src"
            src_dir.mkdir()
            script_copy = src_dir / "initialize_alcatraz.sh"
            script_copy.write_text(Path(INIT_SCRIPT).read_text())
            script_copy.chmod(0o755)

            # Also copy resolve_python.sh since init calls it
            resolve_copy = src_dir / "resolve_python.sh"
            resolve_copy.write_text(
                (PROJECT_DIR / "src" / "resolve_python.sh").read_text()
            )
            resolve_copy.chmod(0o755)

            result = subprocess.run(
                [str(script_copy)],
                capture_output=True, text=True,
                cwd=tmp,
                timeout=30,
            )
            # Should NOT contain the repo root error
            output = result.stderr + result.stdout
            self.assertNotIn("not at the repository root", output.lower())


if __name__ == "__main__":
    unittest.main()
