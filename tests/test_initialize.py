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
INIT_SCRIPT = str(PROJECT_DIR / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")


class TestRepoRootGuard(unittest.TestCase):
    """Verify initialize_alcatraz.sh refuses to run outside repo root."""

    def test_fails_when_script_not_at_repo_root(self):
        """Script placed in a subdirectory (not repo root) should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a git repo
            subprocess.run(
                ["git", "init", tmp], capture_output=True, check=True,
            )
            # Place the script at wrong depth: repo_root/deep/src/alcatrazer/scripts/
            # Script derives PROJECT_DIR as 3 levels up → repo_root/deep/ (not repo root)
            wrong_scripts = Path(tmp) / "deep" / "src" / "alcatrazer" / "scripts"
            wrong_scripts.mkdir(parents=True)
            script_copy = wrong_scripts / "initialize_alcatraz.sh"
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

            # Place scripts at repo_root/src/alcatrazer/scripts/
            scripts_dir = Path(tmp) / "src" / "alcatrazer" / "scripts"
            scripts_dir.mkdir(parents=True)
            for f in ["initialize_alcatraz.sh", "resolve_python.sh"]:
                src_file = PROJECT_DIR / "src" / "alcatrazer" / "scripts" / f
                dest = scripts_dir / f
                dest.write_text(src_file.read_text())
                dest.chmod(0o755)
            # Copy alcatrazer package
            pkg_dir = Path(tmp) / "src" / "alcatrazer"
            for f in (PROJECT_DIR / "src" / "alcatrazer").iterdir():
                if f.is_file() and f.suffix == ".py":
                    (pkg_dir / f.name).write_text(f.read_text())

            result = subprocess.run(
                [str(scripts_dir / "initialize_alcatraz.sh")],
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
        # Copy src/alcatrazer/ package (scripts + Python modules)
        pkg_dir = Path(tmp) / "src" / "alcatrazer"
        scripts_dir = pkg_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        for f in ["initialize_alcatraz.sh", "resolve_python.sh"]:
            src_file = PROJECT_DIR / "src" / "alcatrazer" / "scripts" / f
            dest = scripts_dir / f
            dest.write_text(src_file.read_text())
            dest.chmod(0o755)
        for f in (PROJECT_DIR / "src" / "alcatrazer").iterdir():
            if f.is_file() and f.suffix == ".py":
                (pkg_dir / f.name).write_text(f.read_text())

    def _resolve_workspace(self, tmp):
        """Read workspace path from .alcatrazer/workspace-dir."""
        ws_file = Path(tmp) / ".alcatrazer" / "workspace-dir"
        if ws_file.exists():
            name = ws_file.read_text().strip()
            return Path(tmp) / name
        return None

    def test_workspace_git_identity_is_not_alcatraz(self):
        """After init, workspace git config must NOT contain 'Alcatraz Agent'."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            # Pre-select workspace dir to avoid interactive prompt
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".testws-0001\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            workspace = self._resolve_workspace(tmp)
            if workspace and workspace.exists():
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
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".testws-0002\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
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
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".testws-0003\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            identity_file = Path(tmp) / ".alcatrazer" / "agent-identity"
            workspace = self._resolve_workspace(tmp)
            if identity_file.exists() and workspace and workspace.exists():
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


class TestWorkspaceSeparation(unittest.TestCase):
    """Verify workspace lives in a separate directory from .alcatrazer/."""

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
        # Copy src/alcatrazer/ package (scripts + Python modules)
        pkg_dir = Path(tmp) / "src" / "alcatrazer"
        scripts_dir = pkg_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        for f in ["initialize_alcatraz.sh", "resolve_python.sh"]:
            src_file = PROJECT_DIR / "src" / "alcatrazer" / "scripts" / f
            dest = scripts_dir / f
            dest.write_text(src_file.read_text())
            dest.chmod(0o755)
        for f in (PROJECT_DIR / "src" / "alcatrazer").iterdir():
            if f.is_file() and f.suffix == ".py":
                (pkg_dir / f.name).write_text(f.read_text())

    def test_workspace_not_inside_alcatrazer(self):
        """Workspace must NOT be inside .alcatrazer/ directory."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            # Pre-select workspace dir to avoid interactive prompt
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".devspace-test\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            # Workspace should be at repo_root/.devspace-test, NOT .alcatrazer/workspace
            self.assertTrue(
                Path(tmp, ".devspace-test", ".git").exists(),
                "Workspace git not found at the selected directory",
            )
            self.assertFalse(
                Path(tmp, ".alcatrazer", "workspace").exists(),
                "Workspace should NOT exist inside .alcatrazer/",
            )

    def test_workspace_dir_stored_in_alcatrazer(self):
        """The workspace-dir file should record the selected directory name."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".sandbox-abcd\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            stored = (alcatrazer_dir / "workspace-dir").read_text().strip()
            self.assertEqual(stored, ".sandbox-abcd")

    def test_workspace_dir_added_to_gitignore(self):
        """The selected workspace dir should be added to .gitignore."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".codework-ef01\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            gitignore = Path(tmp, ".gitignore")
            self.assertTrue(gitignore.exists())
            content = gitignore.read_text()
            self.assertIn(".codework-ef01", content)

    def test_workspace_has_snapshot_from_outer(self):
        """Workspace in the separate dir should have files from outer repo."""
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_repo_with_init_script(tmp)
            alcatrazer_dir = Path(tmp) / ".alcatrazer"
            alcatrazer_dir.mkdir(exist_ok=True)
            (alcatrazer_dir / "workspace-dir").write_text(".devbox-1234\n")

            subprocess.run(
                [str(Path(tmp) / "src" / "alcatrazer" / "scripts" / "initialize_alcatraz.sh")],
                capture_output=True, text=True,
                cwd=tmp, timeout=60,
            )
            # The outer repo has README.md — it should be in the workspace
            workspace = Path(tmp, ".devbox-1234")
            self.assertTrue(
                Path(workspace, "README.md").exists(),
                "Outer repo files not snapshotted into workspace",
            )


if __name__ == "__main__":
    unittest.main()
