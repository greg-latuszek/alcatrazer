"""
Unit tests for watch_alcatraz.py — the promotion daemon.

Tests the Python daemon's core logic:
- Config loading from alcatrazer.toml via tomllib
- PID guard (create, detect running, detect stale, cleanup)
- Workspace existence check
- Signal handling (SIGTERM graceful shutdown)

Uses only stdlib (unittest, tempfile, subprocess, etc.)
"""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


def project_dir():
    return Path(__file__).resolve().parent.parent


def python_bin():
    """Resolve Python from .alcatrazer/python symlink, fall back to sys."""
    python_file = project_dir() / ".alcatrazer" / "python"
    if python_file.is_symlink() or python_file.exists():
        return str(python_file.resolve())
    import sys
    return sys.executable


DAEMON_SCRIPT = str(project_dir() / "src" / "alcatrazer" / "daemon.py")
INSPECT_SCRIPT = str(project_dir() / "src" / "inspect_promotion.py")
PYTHON = python_bin()


class TestConfigLoading(unittest.TestCase):
    """Test that the daemon reads config from alcatrazer.toml correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        os.makedirs(os.path.join(self.alcatraz_dir, "workspace", ".git"))

    def tearDown(self):
        # Kill any daemon we may have started
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(open(pid_file).read().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_toml(self, content):
        toml_path = os.path.join(self.tmpdir, "alcatrazer.toml")
        with open(toml_path, "w") as f:
            f.write(content)

    def _start_daemon(self, extra_args=None):
        """Start daemon and return the Popen object."""
        cmd = [
            PYTHON, DAEMON_SCRIPT,
            "--alcatraz-dir", self.alcatraz_dir,
            "--project-dir", self.tmpdir,
        ]
        if extra_args:
            cmd.extend(extra_args)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)  # Let it start
        return proc

    def test_reads_interval_from_toml(self):
        """Daemon should read the interval value from alcatrazer.toml."""
        self._write_toml(
            "[promotion-daemon]\n"
            "interval = 42\n"
        )
        proc = self._start_daemon()
        try:
            # Daemon is running — we can't directly inspect its internal state,
            # but we can verify it started successfully (didn't crash on config parse)
            self.assertIsNone(proc.poll(), "Daemon should still be running")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_reads_all_config_keys(self):
        """Daemon should parse all [promotion-daemon] config keys without error."""
        self._write_toml(
            '[promotion-daemon]\n'
            'interval = 3\n'
            'branches = "main"\n'
            'mode = "mirror"\n'
            'verbosity = "detailed"\n'
            'max_log_size = 256\n'
        )
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should still be running")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_handles_missing_toml(self):
        """Daemon should use defaults when alcatrazer.toml is missing."""
        # Don't write any toml file
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should run with defaults")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_handles_missing_daemon_section(self):
        """Daemon should use defaults when [promotion-daemon] section is missing."""
        self._write_toml("[promotion]\nname = \"Test\"\n")
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should run with defaults")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_reads_branch_list_config(self):
        """Daemon should handle branches as a TOML list."""
        self._write_toml(
            '[promotion-daemon]\n'
            'interval = 2\n'
            'branches = ["main", "feature/*"]\n'
        )
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should handle branch list")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestWorkspaceCheck(unittest.TestCase):
    """Test that the daemon validates workspace existence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        os.makedirs(self.alcatraz_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exits_when_workspace_missing(self):
        """Daemon should exit non-zero when workspace/.git doesn't exist."""
        result = subprocess.run(
            [PYTHON, DAEMON_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workspace", result.stderr.lower() + result.stdout.lower())

    def test_exits_when_workspace_exists_but_no_git(self):
        """Daemon should exit when workspace/ exists but has no .git."""
        os.makedirs(os.path.join(self.alcatraz_dir, "workspace"))
        result = subprocess.run(
            [PYTHON, DAEMON_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)


class TestPidGuard(unittest.TestCase):
    """Test PID file management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        os.makedirs(os.path.join(self.alcatraz_dir, "workspace", ".git"))
        # Write minimal toml
        with open(os.path.join(self.tmpdir, "alcatrazer.toml"), "w") as f:
            f.write("[promotion-daemon]\ninterval = 1\n")
        self.pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")

    def tearDown(self):
        if os.path.exists(self.pid_file):
            try:
                pid = int(open(self.pid_file).read().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start_daemon(self):
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.tmpdir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        return proc

    def test_creates_pid_file(self):
        """Daemon should write its PID to the pid file on startup."""
        proc = self._start_daemon()
        try:
            self.assertTrue(os.path.exists(self.pid_file))
            stored_pid = int(open(self.pid_file).read().strip())
            self.assertEqual(stored_pid, proc.pid)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_prevents_double_start(self):
        """Second daemon instance should refuse to start."""
        proc1 = self._start_daemon()
        try:
            result = subprocess.run(
                [PYTHON, DAEMON_SCRIPT,
                 "--alcatraz-dir", self.alcatraz_dir,
                 "--project-dir", self.tmpdir],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            output = result.stderr.lower() + result.stdout.lower()
            self.assertTrue(
                "already running" in output or "pid" in output,
                f"Expected 'already running' or 'pid' in output, got: {output}",
            )
        finally:
            proc1.send_signal(signal.SIGTERM)
            proc1.wait(timeout=5)

    def test_cleans_pid_on_sigterm(self):
        """PID file should be removed after SIGTERM."""
        proc = self._start_daemon()
        self.assertTrue(os.path.exists(self.pid_file))
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        time.sleep(0.3)
        self.assertFalse(
            os.path.exists(self.pid_file),
            "PID file should be removed after SIGTERM",
        )

    def test_overwrites_stale_pid(self):
        """Daemon should start even if a stale PID file exists."""
        # Write a PID that doesn't correspond to a running process
        with open(self.pid_file, "w") as f:
            f.write("99999\n")

        proc = self._start_daemon()
        try:
            self.assertTrue(os.path.exists(self.pid_file))
            stored_pid = int(open(self.pid_file).read().strip())
            self.assertEqual(stored_pid, proc.pid)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestSignalHandling(unittest.TestCase):
    """Test graceful shutdown on signals."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        os.makedirs(os.path.join(self.alcatraz_dir, "workspace", ".git"))
        with open(os.path.join(self.tmpdir, "alcatrazer.toml"), "w") as f:
            f.write("[promotion-daemon]\ninterval = 1\n")

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(open(pid_file).read().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sigterm_exits_cleanly(self):
        """SIGTERM should cause a clean exit (returncode 0)."""
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.tmpdir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=5)
        self.assertEqual(returncode, 0, "SIGTERM should produce exit code 0")

    def test_sigint_exits_cleanly(self):
        """SIGINT (Ctrl+C) should cause a clean exit."""
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.tmpdir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        returncode = proc.wait(timeout=5)
        # SIGINT may produce 0 or -2 (128+2) depending on implementation
        self.assertIn(returncode, [0, -2, 130],
                      f"SIGINT should produce clean exit, got {returncode}")


SEED_SCRIPT = str(project_dir() / "tests" / "seed_alcatraz.sh")

PROMOTED_NAME = "Test User"
PROMOTED_EMAIL = "test@example.com"


def git(repo: str, *args: str) -> str:
    """Run a git command, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class TestDaemonPromotion(unittest.TestCase):
    """Integration test: daemon promotes commits from workspace to outer repo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # project_dir layout: has .alcatrazer/workspace (source) and is itself a git repo (target)
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.alcatraz_dir, "workspace")

        # Create the outer (target) repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        # Need an initial commit so the repo has a HEAD
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create the inner (source) workspace repo
        os.makedirs(self.workspace)
        subprocess.run(["git", "init", self.workspace], capture_output=True, check=True)
        git(self.workspace, "config", "user.name", "Alcatraz Agent")
        git(self.workspace, "config", "user.email", "alcatraz@localhost")
        git(self.workspace, "config", "commit.gpgsign", "false")

        # Seed the workspace with commits
        subprocess.run([SEED_SCRIPT, self.workspace], capture_output=True, check=True)

        # Write alcatrazer.toml with promotion identity and fast polling
        toml_path = os.path.join(self.test_project, "alcatrazer.toml")
        Path(toml_path).write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
        )

        # Create marks dir
        os.makedirs(self.alcatraz_dir, exist_ok=True)

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start_daemon(self):
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return proc

    def test_daemon_promotes_commits(self):
        """Daemon should promote workspace commits to the outer repo."""
        proc = self._start_daemon()
        try:
            # Wait for at least one promotion cycle (interval=1s + buffer)
            time.sleep(3)

            # Verify commits appeared in the outer repo
            target_msgs = git(self.test_project, "log", "--all", "--format=%s").splitlines()
            self.assertIn("initial commit", target_msgs,
                          "Seeded commits should appear in outer repo")
            self.assertIn("merge feature/auth into main", target_msgs)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_promoted_commits_have_rewritten_identity(self):
        """Promoted commits should have the configured identity, not alcatraz."""
        proc = self._start_daemon()
        try:
            time.sleep(3)

            # Check all promoted authors (excluding the outer repo's own init commit)
            authors = set(git(self.test_project, "log", "--all", "--format=%an <%ae>").splitlines())
            # Should contain the promoted identity (from seeded commits)
            self.assertIn(f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>", authors)
            # Should NOT contain alcatraz identity
            self.assertNotIn("Alcatraz Agent <alcatraz@localhost>", authors)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_daemon_writes_log(self):
        """Daemon should write promotion activity to the log file."""
        proc = self._start_daemon()
        try:
            time.sleep(3)

            log_file = os.path.join(self.alcatraz_dir, "promotion-daemon.log")
            self.assertTrue(os.path.exists(log_file), "Log file should exist")
            log_content = Path(log_file).read_text()
            self.assertTrue(len(log_content) > 0, "Log file should not be empty")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestLogRotation(unittest.TestCase):
    """Test that the daemon rotates log files when they exceed max_log_size."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.alcatraz_dir, "workspace")

        # Create outer repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create workspace
        os.makedirs(self.workspace)
        subprocess.run(["git", "init", self.workspace], capture_output=True, check=True)
        git(self.workspace, "config", "user.name", "Alcatraz Agent")
        git(self.workspace, "config", "user.email", "alcatraz@localhost")
        git(self.workspace, "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, self.workspace], capture_output=True, check=True)

        os.makedirs(self.alcatraz_dir, exist_ok=True)

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_log_rotates_when_exceeding_max_size(self):
        """Log file should rotate when it exceeds max_log_size KB."""
        # Set max_log_size to 1 KB so rotation triggers quickly
        toml_path = os.path.join(self.test_project, "alcatrazer.toml")
        Path(toml_path).write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
            f'max_log_size = 1\n'  # 1 KB — will rotate very quickly
        )

        # Pre-fill the log with > 1 KB of data to trigger rotation on first cycle
        log_file = Path(self.alcatraz_dir) / "promotion-daemon.log"
        log_file.write_text("x" * 2048 + "\n")

        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)

            rotated_log = Path(self.alcatraz_dir) / "promotion-daemon.log.1"
            self.assertTrue(rotated_log.exists(),
                            "Rotated log file (.log.1) should exist")
            # Current log should be smaller than the rotated one
            self.assertTrue(log_file.exists(), "Current log should exist")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestBranchFiltering(unittest.TestCase):
    """Test that the daemon respects the branches config."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.alcatraz_dir, "workspace")

        # Create outer repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create workspace with seeded history (has main, feature/auth, agent/backend, agent/frontend)
        os.makedirs(self.workspace)
        subprocess.run(["git", "init", self.workspace], capture_output=True, check=True)
        git(self.workspace, "config", "user.name", "Alcatraz Agent")
        git(self.workspace, "config", "user.email", "alcatraz@localhost")
        git(self.workspace, "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, self.workspace], capture_output=True, check=True)

        os.makedirs(self.alcatraz_dir, exist_ok=True)

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_toml(self, branches_value):
        toml_path = os.path.join(self.test_project, "alcatrazer.toml")
        Path(toml_path).write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
            f'branches = {branches_value}\n'
        )

    def _start_daemon(self):
        return subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def _target_branches(self):
        """Return set of branch names in the target repo."""
        output = git(self.test_project, "branch", "--format=%(refname:short)")
        return set(output.splitlines()) if output else set()

    def test_branches_all_promotes_everything(self):
        """branches = "all" should promote all branches."""
        self._write_toml('"all"')
        proc = self._start_daemon()
        try:
            time.sleep(3)
            branches = self._target_branches()
            self.assertIn("main", branches)
            self.assertIn("feature/auth", branches)
            self.assertIn("agent/backend", branches)
            self.assertIn("agent/frontend", branches)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_branches_main_only(self):
        """branches = "main" should promote only main."""
        self._write_toml('"main"')
        proc = self._start_daemon()
        try:
            time.sleep(3)
            branches = self._target_branches()
            self.assertIn("main", branches)
            self.assertNotIn("feature/auth", branches)
            self.assertNotIn("agent/backend", branches)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_branches_glob_pattern(self):
        """branches = ["main", "agent/*"] should promote main and agent branches."""
        self._write_toml('["main", "agent/*"]')
        proc = self._start_daemon()
        try:
            time.sleep(3)
            branches = self._target_branches()
            self.assertIn("main", branches)
            self.assertIn("agent/backend", branches)
            self.assertIn("agent/frontend", branches)
            self.assertNotIn("feature/auth", branches)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class _ConflictTestBase(unittest.TestCase):
    """Shared setup for conflict tests: outer repo + workspace + initial promotion."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.alcatraz_dir, "workspace")

        # Create outer repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create workspace with seeded history
        os.makedirs(self.workspace)
        subprocess.run(["git", "init", self.workspace], capture_output=True, check=True)
        git(self.workspace, "config", "user.name", "Alcatraz Agent")
        git(self.workspace, "config", "user.email", "alcatraz@localhost")
        git(self.workspace, "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, self.workspace], capture_output=True, check=True)

        # Write toml
        Path(self.test_project, "alcatrazer.toml").write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
        )
        os.makedirs(self.alcatraz_dir, exist_ok=True)

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start_daemon(self):
        return subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

class TestConflictDetection(_ConflictTestBase):
    """Integration test: daemon handles conflicts when outer repo diverges."""

    def test_conflict_branch_created_on_divergence(self):
        """When outer repo diverges, daemon creates a conflict/resolve-* branch."""
        # First: do an initial promotion so outer repo has workspace's main
        proc = self._start_daemon()
        time.sleep(3)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Verify initial promotion worked
        target_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
        self.assertIn("initial commit", target_msgs)

        # Now: human commits directly to main in the outer repo (divergence)
        Path(self.test_project, "human_change.txt").write_text("human work\n")
        git(self.test_project, "add", "human_change.txt")
        git(self.test_project, "commit", "-m", "human commit on outer main")

        # Also add a new commit in workspace so there's something to promote
        git(self.workspace, "checkout", "main")
        Path(self.workspace, "agent_new.py").write_text("agent work\n")
        git(self.workspace, "add", "agent_new.py")
        git(self.workspace, "commit", "-m", "agent commit after divergence")

        # Restart daemon — should detect conflict on main
        proc2 = self._start_daemon()
        try:
            time.sleep(3)

            # Check that a conflict branch was created
            all_branches = git(self.test_project, "branch", "--format=%(refname:short)").splitlines()
            conflict_branches = [b for b in all_branches if b.startswith("conflict/resolve-")]
            self.assertTrue(
                len(conflict_branches) > 0,
                f"Expected conflict branch, got branches: {all_branches}",
            )

            # The conflict branch should mention 'main'
            self.assertTrue(
                any("main" in b for b in conflict_branches),
                f"Conflict branch should reference 'main', got: {conflict_branches}",
            )

            # Human's commit on main should NOT be lost
            main_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertIn("human commit on outer main", main_msgs)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)

    def test_conflict_logged(self):
        """Conflict should be logged to the daemon log."""
        # Initial promotion
        proc = self._start_daemon()
        time.sleep(3)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Create divergence
        Path(self.test_project, "human.txt").write_text("human\n")
        git(self.test_project, "add", "human.txt")
        git(self.test_project, "commit", "-m", "human divergence")

        git(self.workspace, "checkout", "main")
        Path(self.workspace, "new.py").write_text("new\n")
        git(self.workspace, "add", "new.py")
        git(self.workspace, "commit", "-m", "agent new commit")

        # Restart
        proc2 = self._start_daemon()
        try:
            time.sleep(3)
            log_content = Path(self.alcatraz_dir, "promotion-daemon.log").read_text()
            self.assertRegex(log_content, r"(?i)conflict.*main")
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)

    def test_non_conflicting_branches_still_promoted(self):
        """Branches without conflicts should still be promoted normally."""
        # Initial promotion
        proc = self._start_daemon()
        time.sleep(3)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Diverge only on main
        Path(self.test_project, "human.txt").write_text("human\n")
        git(self.test_project, "add", "human.txt")
        git(self.test_project, "commit", "-m", "human divergence")

        # Add new work on a non-main branch in workspace
        git(self.workspace, "checkout", "-b", "feature/new-work")
        Path(self.workspace, "new_work.py").write_text("new work\n")
        git(self.workspace, "add", "new_work.py")
        git(self.workspace, "commit", "-m", "new work on feature branch")

        # Configure to promote all branches
        Path(self.test_project, "alcatrazer.toml").write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
            f'branches = "all"\n'
        )

        # Restart daemon
        proc2 = self._start_daemon()
        try:
            time.sleep(3)
            branches = set(git(self.test_project, "branch", "--format=%(refname:short)").splitlines())
            # The new feature branch should be promoted despite main being conflicted
            self.assertIn("feature/new-work", branches)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)


class TestConflictResolution(_ConflictTestBase):
    """Integration test: daemon resumes after conflict branch is resolved."""

    def _create_conflict(self):
        """Run daemon for initial promotion, then create divergence on main."""
        proc = self._start_daemon()
        time.sleep(3)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Human commits to outer main
        Path(self.test_project, "human.txt").write_text("human work\n")
        git(self.test_project, "add", "human.txt")
        git(self.test_project, "commit", "-m", "human commit on outer main")

        # Agent commits to workspace main
        git(self.workspace, "checkout", "main")
        Path(self.workspace, "agent_new.py").write_text("agent work\n")
        git(self.workspace, "add", "agent_new.py")
        git(self.workspace, "commit", "-m", "agent commit after divergence")

        # Run daemon again to create conflict branch
        proc2 = self._start_daemon()
        time.sleep(3)
        proc2.send_signal(signal.SIGTERM)
        proc2.wait(timeout=5)

        # Find the conflict branch name
        all_branches = git(self.test_project, "branch", "--format=%(refname:short)").splitlines()
        conflict_branches = [b for b in all_branches if b.startswith("conflict/resolve-main")]
        assert len(conflict_branches) > 0, f"Expected conflict branch, got: {all_branches}"
        return conflict_branches[0]

    def test_resumes_after_conflict_branch_deleted(self):
        """Daemon resumes promoting a branch after its conflict branch is deleted."""
        conflict_branch = self._create_conflict()

        # User resolves: merge conflict branch into main, then delete it
        git(self.test_project, "merge", conflict_branch, "--no-ff", "-m", "resolve conflict")
        git(self.test_project, "branch", "-d", conflict_branch)

        # Add another commit in workspace to verify promotion resumes
        git(self.workspace, "checkout", "main")
        Path(self.workspace, "after_resolve.py").write_text("after resolve\n")
        git(self.workspace, "add", "after_resolve.py")
        git(self.workspace, "commit", "-m", "commit after conflict resolved")

        # Restart daemon — should resume promoting main
        proc = self._start_daemon()
        try:
            time.sleep(3)
            target_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertIn("commit after conflict resolved", target_msgs)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_resumes_after_conflict_branch_force_deleted(self):
        """Daemon resumes even if user force-deletes the conflict branch without merging."""
        conflict_branch = self._create_conflict()

        # User just deletes the conflict branch (decides to discard agent's work)
        git(self.test_project, "branch", "-D", conflict_branch)

        # Add another commit in workspace
        git(self.workspace, "checkout", "main")
        Path(self.workspace, "after_discard.py").write_text("after discard\n")
        git(self.workspace, "add", "after_discard.py")
        git(self.workspace, "commit", "-m", "commit after conflict discarded")

        # Restart daemon — should resume promoting main
        proc = self._start_daemon()
        try:
            time.sleep(3)
            target_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertIn("commit after conflict discarded", target_msgs)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestInspectPromotion(unittest.TestCase):
    """Tests for inspect_promotion.py."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        os.makedirs(self.alcatraz_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exits_when_no_log_file(self):
        """Should exit non-zero with helpful message when log doesn't exist."""
        result = subprocess.run(
            [PYTHON, INSPECT_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No log file", result.stdout)
        self.assertIn("watch_alcatraz.py", result.stdout)

    def test_starts_tailing_when_log_exists(self):
        """Should start tailing when log file exists (we kill it quickly)."""
        log_file = os.path.join(self.alcatraz_dir, "promotion-daemon.log")
        Path(log_file).write_text("2026-04-06 12:00:00 Daemon started\n")

        proc = subprocess.Popen(
            [PYTHON, INSPECT_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        stdout, _ = proc.communicate(timeout=5)
        self.assertIn("Tailing", stdout.decode())


class TestAlcatrazTreeMode(unittest.TestCase):
    """Integration test: daemon promotes into alcatraz/* namespace."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.alcatraz_dir, "workspace")

        # Create outer repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create workspace with seeded history
        os.makedirs(self.workspace)
        subprocess.run(["git", "init", self.workspace], capture_output=True, check=True)
        git(self.workspace, "config", "user.name", "Alcatraz Agent")
        git(self.workspace, "config", "user.email", "alcatraz@localhost")
        git(self.workspace, "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, self.workspace], capture_output=True, check=True)

        Path(self.test_project, "alcatrazer.toml").write_text(
            f'[promotion]\n'
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f'\n'
            f'[promotion-daemon]\n'
            f'interval = 1\n'
            f'mode = "alcatraz-tree"\n'
        )
        os.makedirs(self.alcatraz_dir, exist_ok=True)

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
            except (ProcessLookupError, ValueError):
                pass
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_promotes_into_alcatraz_namespace(self):
        """In alcatraz-tree mode, inner main becomes outer alcatraz/main."""
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)
            branches = set(git(self.test_project, "branch", "--format=%(refname:short)").splitlines())
            self.assertIn("alcatraz/main", branches)
            self.assertIn("alcatraz/feature/auth", branches)
            # Original main should still be outer repo's own main (not overwritten)
            main_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertEqual(main_msgs, ["init outer repo"])
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_no_conflicts_in_alcatraz_tree_mode(self):
        """alcatraz-tree mode should never conflict — separate namespace."""
        # Do initial promotion
        proc = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(3)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Human commits to main (would conflict in mirror mode)
        Path(self.test_project, "human.txt").write_text("human\n")
        git(self.test_project, "add", "human.txt")
        git(self.test_project, "commit", "-m", "human work on main")

        # Agent adds more work
        git(self.workspace, "checkout", "main")
        Path(self.workspace, "more.py").write_text("more\n")
        git(self.workspace, "add", "more.py")
        git(self.workspace, "commit", "-m", "agent more work")

        # Restart daemon — should promote without conflict
        proc2 = subprocess.Popen(
            [PYTHON, DAEMON_SCRIPT,
             "--alcatraz-dir", self.alcatraz_dir,
             "--project-dir", self.test_project],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)
            branches = set(git(self.test_project, "branch", "--format=%(refname:short)").splitlines())
            # No conflict branches should exist
            conflict_branches = [b for b in branches if b.startswith("conflict/")]
            self.assertEqual(conflict_branches, [])
            # Agent work should be in alcatraz/main
            alcatraz_msgs = git(self.test_project, "log", "alcatraz/main", "--format=%s").splitlines()
            self.assertIn("agent more work", alcatraz_msgs)
            # Human work should still be on main
            main_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertIn("human work on main", main_msgs)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
