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


class TestIdentityInInit(unittest.TestCase):
    """Verify init generates random identity instead of hardcoded Alcatraz Agent."""

    def _setup_repo_with_init_script(self, tmp):
        """Create a git repo with the init script at the correct location."""
        subprocess.run(["git", "init", tmp], capture_output=True, check=True)
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
        # Copy src/ directory
        src_dir = Path(tmp) / "src"
        src_dir.mkdir()
        for f in ["initialize_alcatraz.sh", "resolve_python.sh", "snapshot.py"]:
            src_file = PROJECT_DIR / "src" / f
            dest = src_dir / f
            dest.write_text(src_file.read_text())
            dest.chmod(0o755)
        # Copy alcatrazer package
        pkg_dir = src_dir / "alcatrazer"
        pkg_dir.mkdir()
        for f in (PROJECT_DIR / "src" / "alcatrazer").iterdir():
            if f.is_file() and f.suffix == ".py":
                (pkg_dir / f.name).write_text(f.read_text())

    def test_workspace_git_identity_is_not_alcatraz(self):
        """After init, workspace git config must NOT contain 'Alcatraz Agent'."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            result = subprocess.run(
                [str(Path(tmp) / "src" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            workspace = Path(tmp) / ".alcatrazer" / "workspace"
            if workspace.exists():
                name = subprocess.run(
                    ["git", "-C", str(workspace), "config", "user.name"],
                    capture_output=True, text=True,
                ).stdout.strip()
                self.assertNotEqual(name, "Alcatraz Agent")
                self.assertNotIn("alcatraz", name.lower())

    def test_agent_identity_file_created(self):
        """Init should create .alcatrazer/agent-identity file."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            subprocess.run(
                [str(Path(tmp) / "src" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            identity_file = Path(tmp) / ".alcatrazer" / "agent-identity"
            self.assertTrue(identity_file.exists(), "agent-identity file not created")
            lines = identity_file.read_text().strip().split("\n")
            self.assertEqual(len(lines), 2, f"Expected 2 lines, got: {lines}")

    def test_workspace_identity_matches_stored_identity(self):
        """Workspace git config should use the identity from agent-identity file."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            subprocess.run(
                [str(Path(tmp) / "src" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            identity_file = Path(tmp) / ".alcatrazer" / "agent-identity"
            workspace = Path(tmp) / ".alcatrazer" / "workspace"
            if identity_file.exists() and workspace.exists():
                lines = identity_file.read_text().strip().split("\n")
                expected_name, expected_email = lines[0], lines[1]
                actual_name = subprocess.run(
                    ["git", "-C", str(workspace), "config", "user.name"],
                    capture_output=True, text=True,
                ).stdout.strip()
                actual_email = subprocess.run(
                    ["git", "-C", str(workspace), "config", "user.email"],
                    capture_output=True, text=True,
                ).stdout.strip()
                self.assertEqual(actual_name, expected_name)
                self.assertEqual(actual_email, expected_email)


if __name__ == "__main__":
    unittest.main()
