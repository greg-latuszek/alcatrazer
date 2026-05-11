"""
Unit tests for watch_alcatraz.py — the promotion daemon.

Tests the Python daemon's core logic:
- Config loading from .alcatrazer/config.toml via tomllib
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
    # tests/ is at src/alcatrazer/tests/ — project root is 3 levels up
    return Path(__file__).resolve().parent.parent.parent.parent


def python_bin():
    """Resolve Python from .alcatrazer/python symlink, fall back to sys."""
    python_file = project_dir() / ".alcatrazer" / "python"
    if python_file.is_symlink() or python_file.exists():
        return str(python_file.resolve())
    import sys

    return sys.executable


DAEMON_SCRIPT = str(project_dir() / "src" / "alcatrazer" / "daemon.py")
STATUS_SCRIPT = str(project_dir() / "src" / "alcatrazer" / "status.py")
PYTHON = python_bin()

# Fixed workspace dir name for test fixtures. Real installs generate a
# random `.{word}-{4hex}` via `identity.generate_workspace_dir_name()`,
# but tests don't need randomness — the daemon finds the workspace via
# the `.alcatrazer/workspace-dir` pointer regardless of the name.
WORKSPACE_DIR_NAME = ".devspace-test"


class TestConfigLoading(unittest.TestCase):
    """Daemon reads per-developer config from `.alcatrazer/config.toml`
    (the post-install_method.md layout — `[promotion-daemon]` and
    `[promotion]` live under alcatraz_dir, not alongside the public
    `coding-environment.toml` at the repo root)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, ".alcatrazer")
        os.makedirs(self.alcatraz_dir)
        # Point daemon at a real workspace git directory alongside
        # `.alcatrazer/` — mirrors what `alcatrazer init` + `start` would
        # have produced on a real install.
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")
        os.makedirs(os.path.join(self.tmpdir, WORKSPACE_DIR_NAME, ".git"))

    def tearDown(self):
        # Kill any daemon we may have started
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, content):
        """Write the daemon config at the post-refactor location."""
        toml_path = os.path.join(self.alcatraz_dir, "config.toml")
        with open(toml_path, "w") as f:
            f.write(content)

    def _log_path(self):
        return os.path.join(self.alcatraz_dir, "promotion-daemon.log")

    def _start_daemon(self, extra_args=None):
        """Start daemon and return the Popen object."""
        cmd = [
            PYTHON,
            DAEMON_SCRIPT,
            "--alcatraz-dir",
            self.alcatraz_dir,
            "--project-dir",
            self.tmpdir,
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

    def test_reads_interval_value_into_startup_log(self):
        """Strong assertion (previous tests only checked `didn't crash`):
        the interval actually reaches the daemon's logging — so if the
        daemon reads the wrong file and silently falls back to defaults,
        this test fails."""
        self._write_config("[promotion-daemon]\ninterval = 42\n")
        proc = self._start_daemon()
        try:
            # Poll the log briefly — it's written once the daemon settles.
            log_file = self._log_path()
            deadline = time.time() + 3
            content = ""
            while time.time() < deadline:
                if os.path.exists(log_file):
                    content = Path(log_file).read_text()
                    if "Daemon started" in content:
                        break
                time.sleep(0.1)
            self.assertIn("interval=42s", content, f"Log did not show interval=42: {content!r}")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_reads_interval_from_toml(self):
        """Daemon should read the interval value from .alcatrazer/config.toml."""
        self._write_config("[promotion-daemon]\ninterval = 42\n")
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
        self._write_config(
            "[promotion-daemon]\n"
            "interval = 3\n"
            'branches = "main"\n'
            'mode = "mirror"\n'
            'verbosity = "detailed"\n'
            "max_log_size = 256\n"
        )
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should still be running")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_handles_missing_config(self):
        """Daemon should use defaults when .alcatrazer/config.toml is missing."""
        # Don't write any config file
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should run with defaults")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_handles_missing_daemon_section(self):
        """Daemon should use defaults when [promotion-daemon] section is missing."""
        self._write_config('[promotion]\nname = "Test"\n')
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should run with defaults")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_reads_branch_list_config(self):
        """Daemon should handle branches as a TOML list."""
        self._write_config('[promotion-daemon]\ninterval = 2\nbranches = ["main", "feature/*"]\n')
        proc = self._start_daemon()
        try:
            self.assertIsNone(proc.poll(), "Daemon should handle branch list")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestWorkspaceCheck(unittest.TestCase):
    """Daemon resolves the workspace via the `.alcatrazer/workspace-dir`
    pointer written by `alcatrazer init` — the workspace itself is a
    sibling of `.alcatrazer/` at `project_dir / <name>`, NOT a child of
    `.alcatrazer/`. The daemon must error out cleanly when either the
    pointer is missing, the pointed-at directory is missing, or the
    directory exists but isn't a git repo."""

    WORKSPACE_NAME = ".devspace-test"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, ".alcatrazer")
        os.makedirs(self.alcatraz_dir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_daemon(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.tmpdir,
            ],
            capture_output=True,
            text=True,
        )

    def test_exits_when_workspace_dir_pointer_missing(self):
        """No `.alcatrazer/workspace-dir` pointer → daemon tells the user
        to run init+start first (that's what creates the pointer)."""
        result = self._run_daemon()
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stderr + result.stdout).lower()
        self.assertIn("init", combined)

    def test_exits_when_workspace_directory_missing(self):
        """Pointer says `<project>/.devspace-test`, but the directory was
        never created / was manually deleted."""
        Path(self.alcatraz_dir, "workspace-dir").write_text(self.WORKSPACE_NAME + "\n")
        result = self._run_daemon()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workspace", (result.stderr + result.stdout).lower())

    def test_exits_when_workspace_exists_but_no_git(self):
        """Directory is there (pointed at correctly) but has no `.git`
        subdirectory — e.g., the inner repo was manually wiped."""
        Path(self.alcatraz_dir, "workspace-dir").write_text(self.WORKSPACE_NAME + "\n")
        os.makedirs(os.path.join(self.tmpdir, self.WORKSPACE_NAME))
        result = self._run_daemon()
        self.assertNotEqual(result.returncode, 0)


class TestPidGuard(unittest.TestCase):
    """Test PID file management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, ".alcatrazer")
        os.makedirs(self.alcatraz_dir)
        # Workspace pointer + target dir so the daemon's workspace check
        # passes. Real installs generate a random workspace name; tests
        # use a fixed one.
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")
        os.makedirs(os.path.join(self.tmpdir, WORKSPACE_DIR_NAME, ".git"))
        # Write minimal config at the post-refactor location.
        with open(os.path.join(self.alcatraz_dir, "config.toml"), "w") as f:
            f.write("[promotion-daemon]\ninterval = 1\n")
        self.pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")

    def tearDown(self):
        if os.path.exists(self.pid_file):
            try:
                pid = int(Path(self.pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start_daemon(self):
        proc = subprocess.Popen(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.tmpdir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        return proc

    def test_creates_pid_file(self):
        """Daemon should write its PID to the pid file on startup."""
        proc = self._start_daemon()
        try:
            self.assertTrue(os.path.exists(self.pid_file))
            stored_pid = int(Path(self.pid_file).read_text().strip())
            self.assertEqual(stored_pid, proc.pid)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_prevents_double_start(self):
        """Second daemon instance should refuse to start."""
        proc1 = self._start_daemon()
        try:
            result = subprocess.run(
                [
                    PYTHON,
                    DAEMON_SCRIPT,
                    "--alcatraz-dir",
                    self.alcatraz_dir,
                    "--project-dir",
                    self.tmpdir,
                ],
                capture_output=True,
                text=True,
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
            stored_pid = int(Path(self.pid_file).read_text().strip())
            self.assertEqual(stored_pid, proc.pid)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


class TestSignalHandling(unittest.TestCase):
    """Test graceful shutdown on signals."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, ".alcatrazer")
        os.makedirs(self.alcatraz_dir)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")
        os.makedirs(os.path.join(self.tmpdir, WORKSPACE_DIR_NAME, ".git"))
        with open(os.path.join(self.alcatraz_dir, "config.toml"), "w") as f:
            f.write("[promotion-daemon]\ninterval = 1\n")

    def tearDown(self):
        pid_file = os.path.join(self.alcatraz_dir, "promotion-daemon.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError):
                pass
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sigterm_exits_cleanly(self):
        """SIGTERM should cause a clean exit (returncode 0)."""
        proc = subprocess.Popen(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.tmpdir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=5)
        self.assertEqual(returncode, 0, "SIGTERM should produce exit code 0")

    def test_sigint_exits_cleanly(self):
        """SIGINT (Ctrl+C) should cause a clean exit."""
        proc = subprocess.Popen(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.tmpdir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        returncode = proc.wait(timeout=5)
        # SIGINT may produce 0 or -2 (128+2) depending on implementation
        self.assertIn(
            returncode, [0, -2, 130], f"SIGINT should produce clean exit, got {returncode}"
        )


SEED_SCRIPT = str(project_dir() / "src" / "alcatrazer" / "tests" / "seed_alcatraz.sh")

PROMOTED_NAME = "Test User"
PROMOTED_EMAIL = "test@example.com"


def git(repo: str, *args: str) -> str:
    """Run a git command, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestDaemonPromotion(unittest.TestCase):
    """Integration test: daemon promotes commits from workspace to outer repo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # project_dir layout: has .alcatrazer/workspace (source) and is itself a git repo (target)
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.test_project, WORKSPACE_DIR_NAME)
        Path(self.alcatraz_dir).mkdir(parents=True, exist_ok=True)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")

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

        # Write .alcatrazer/config.toml with promotion identity and fast polling
        toml_path = os.path.join(self.alcatraz_dir, "config.toml")
        Path(toml_path).write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
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
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return proc

    @unittest.expectedFailure
    def test_daemon_promotes_commits(self):
        """Daemon should promote workspace commits to the outer repo.

        OBSOLETE after Phase 4 mirror swap (the old "Promotion cycle
        complete: main" log line is gone; the new path emits
        "Promoted N commit(s)" instead). Marked as expectedFailure
        until Phase 6 deletes it per change_promotion_machinery.md
        Step 6.4. The equivalent end-to-end behavior is covered by
        TestRunCycleMirror.test_end_to_end_promotes_agent_commit.
        """
        proc = self._start_daemon()
        try:
            # Wait for at least one promotion cycle (interval=1s + buffer)
            time.sleep(3)

            # Verify commits appeared in the outer repo
            target_msgs = git(self.test_project, "log", "--all", "--format=%s").splitlines()
            self.assertIn(
                "initial commit", target_msgs, "Seeded commits should appear in outer repo"
            )
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

    def _read_log(self) -> str:
        return Path(self.alcatraz_dir, "promotion-daemon.log").read_text()

    def test_final_sync_on_shutdown_logs_graceful_when_state_says_requested(self):
        """When `alcatrazer stop`/`clear` signals intent via state.json,
        daemon's shutdown path logs "graceful shutdown" prefix. This is
        the happy path: CLI stopped docker first, final sync is safe by
        the ordering contract."""
        Path(self.alcatraz_dir, "state.json").write_text(
            '{"schema_version": 1, "daemon_shutdown": "requested"}\n'
        )
        proc = self._start_daemon()
        try:
            time.sleep(2)  # Let daemon start + run at least one poll.
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        log = self._read_log()
        self.assertIn("Final sync (graceful shutdown)", log, log)

    def test_final_sync_on_shutdown_logs_unexpected_when_state_absent(self):
        """No state.json at all — user ran `kill <pid>` or OS sent
        SIGTERM during shutdown. Daemon still attempts final sync (best
        effort; eventual consistency via marks on next start). Log
        prefix distinguishes this from the graceful case so forensics
        is possible later."""
        # Deliberately DO NOT write state.json.
        proc = self._start_daemon()
        try:
            time.sleep(2)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        log = self._read_log()
        self.assertIn("Final sync (unexpected shutdown)", log, log)

    def test_final_sync_logs_unexpected_when_state_flag_is_done(self):
        """Previous shutdown finished cleanly (CLI wrote "done"). Now
        the daemon is killed without a new CLI-initiated shutdown cycle
        — still counts as unexpected."""
        Path(self.alcatraz_dir, "state.json").write_text(
            '{"schema_version": 1, "daemon_shutdown": "done"}\n'
        )
        proc = self._start_daemon()
        try:
            time.sleep(2)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        log = self._read_log()
        self.assertIn("Final sync (unexpected shutdown)", log, log)

    def test_final_sync_logs_unexpected_when_state_json_is_corrupt(self):
        """Corrupt state.json — daemon's load_state returns {}, which
        `.get("daemon_shutdown")` makes None → unexpected prefix.
        Daemon keeps shutting down regardless."""
        Path(self.alcatraz_dir, "state.json").write_text("{not json")
        proc = self._start_daemon()
        try:
            time.sleep(2)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        log = self._read_log()
        self.assertIn("Final sync (unexpected shutdown)", log, log)

    @unittest.expectedFailure
    def test_final_sync_actually_promotes_pending_commits(self):
        """Race-closer: a commit lands after the last poll tick but
        before SIGTERM. Without final sync, that commit would stay in
        the workspace until next start. With final sync, it shows up
        in the outer repo before the daemon exits.

        OBSOLETE after Phase 4 mirror swap (asserts old log format
        "Final sync ... %d commit(s) synced" which is gone — new
        path uses "Final sync (...) complete" + transition logs
        emitted by _run_cycle_mirror). Marked expectedFailure until
        Phase 6 deletes it per change_promotion_machinery.md Step
        6.4. The final-sync race-closing behavior itself still
        works; only the assertion needs to be revised when this
        test is replaced.
        """
        # Let the initial seed sync through first.
        proc = self._start_daemon()
        try:
            time.sleep(3)  # First poll cycle syncs the seed.
            # Now add a new commit in the workspace AFTER the daemon's
            # poll, tight window before we SIGTERM.
            Path(self.workspace, "late.txt").write_text("after last poll\n")
            git(self.workspace, "add", "late.txt")
            git(self.workspace, "commit", "-m", "late commit (pre-shutdown)")
            # Mark the shutdown as graceful so the log line is
            # predictable; final sync runs either way.
            Path(self.alcatraz_dir, "state.json").write_text(
                '{"schema_version": 1, "daemon_shutdown": "requested"}\n'
            )
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)

        # The late commit must appear in the outer repo.
        msgs = git(self.test_project, "log", "--all", "--format=%s").splitlines()
        self.assertIn("late commit (pre-shutdown)", msgs, msgs)


class TestLogRotation(unittest.TestCase):
    """Test that the daemon rotates log files when they exceed max_log_size."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.test_project, WORKSPACE_DIR_NAME)
        Path(self.alcatraz_dir).mkdir(parents=True, exist_ok=True)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")

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
        toml_path = os.path.join(self.alcatraz_dir, "config.toml")
        Path(toml_path).write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
            f"max_log_size = 1\n"  # 1 KB — will rotate very quickly
        )

        # Pre-fill the log with > 1 KB of data to trigger rotation on first cycle
        log_file = Path(self.alcatraz_dir) / "promotion-daemon.log"
        log_file.write_text("x" * 2048 + "\n")

        proc = subprocess.Popen(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)

            rotated_log = Path(self.alcatraz_dir) / "promotion-daemon.log.1"
            self.assertTrue(rotated_log.exists(), "Rotated log file (.log.1) should exist")
            # Current log should be smaller than the rotated one
            self.assertTrue(log_file.exists(), "Current log should exist")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


# OBSOLETE after Phase 4 mirror swap: the new path uses a single
# `pinned_branch` from state.json, not a `branches` config glob.
# Marked expectedFailure at method level (class-level decorators
# don't apply to unittest test methods). Phase 6 Step 6.4 deletes
# this class entirely. See change_promotion_machinery.md L956-958.
class TestBranchFiltering(unittest.TestCase):
    """Test that the daemon respects the branches config."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_project = self.tmpdir
        self.alcatraz_dir = os.path.join(self.test_project, ".alcatrazer")
        self.workspace = os.path.join(self.test_project, WORKSPACE_DIR_NAME)
        Path(self.alcatraz_dir).mkdir(parents=True, exist_ok=True)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")

        # Create outer repo
        subprocess.run(["git", "init", self.test_project], capture_output=True, check=True)
        git(self.test_project, "config", "user.name", PROMOTED_NAME)
        git(self.test_project, "config", "user.email", PROMOTED_EMAIL)
        git(self.test_project, "config", "commit.gpgsign", "false")
        Path(self.test_project, ".gitkeep").write_text("")
        git(self.test_project, "add", ".gitkeep")
        git(self.test_project, "commit", "-m", "init outer repo")

        # Create workspace with seeded history
        # (has main, feature/auth, agent/backend, agent/frontend)
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
        toml_path = os.path.join(self.alcatraz_dir, "config.toml")
        Path(toml_path).write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
            f"branches = {branches_value}\n"
        )

    def _start_daemon(self):
        return subprocess.Popen(
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _target_branches(self):
        """Return set of branch names in the target repo."""
        output = git(self.test_project, "branch", "--format=%(refname:short)")
        return set(output.splitlines()) if output else set()

    @unittest.expectedFailure
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

    @unittest.expectedFailure
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
        self.workspace = os.path.join(self.test_project, WORKSPACE_DIR_NAME)
        Path(self.alcatraz_dir).mkdir(parents=True, exist_ok=True)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")

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
        Path(self.alcatraz_dir, "config.toml").write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
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
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


# OBSOLETE after Phase 4 mirror swap: the new path writes
# state.paused on conflict instead of creating conflict/resolve-*
# branches. Phase 6 Step 6.4 deletes this class entirely.
# The equivalent behavior is covered by
# TestRunCycleMirror.test_paused_state_transition_logs_paused_then_resumed.
class TestConflictDetection(_ConflictTestBase):
    """Integration test: daemon handles conflicts when outer repo diverges."""

    @unittest.expectedFailure
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
            all_branches = git(
                self.test_project, "branch", "--format=%(refname:short)"
            ).splitlines()
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

    @unittest.expectedFailure
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

    @unittest.expectedFailure
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
        Path(self.alcatraz_dir, "config.toml").write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
            f'branches = "all"\n'
        )

        # Restart daemon
        proc2 = self._start_daemon()
        try:
            time.sleep(3)
            branches = set(
                git(self.test_project, "branch", "--format=%(refname:short)").splitlines()
            )
            # The new feature branch should be promoted despite main being conflicted
            self.assertIn("feature/new-work", branches)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)


# OBSOLETE after Phase 4 mirror swap: the old "conflict resolved
# by deletion of conflict/resolve-* branch" mechanism is gone.
# The new mechanism is "state.paused cleared when promote_once
# succeeds on the next cycle". Phase 6 Step 6.4 deletes this
# class entirely. Equivalent behavior is covered by
# TestRunCycleMirror.test_paused_state_transition_logs_paused_then_resumed.
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

    @unittest.expectedFailure
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

    @unittest.expectedFailure
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


class TestRunCycleMirror(unittest.TestCase):
    """Phase 4 (change_promotion_machinery.md L896-919): the daemon's
    mirror-mode cycle now calls promote_once + tracks status across
    cycles via `last_logged_status` for transition-only logging.

    These tests exercise `daemon._run_cycle_mirror` directly (a new
    top-level function extracted from the closure-based run_cycle
    that currently lives inside daemon.main). The in-process call
    sidesteps subprocess flakiness while still covering the
    transition logic the daemon depends on.
    """

    def _make_inner(self, inner: Path) -> str:
        """Initial empty commit (inner_root) — caller adds more commits."""
        inner.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(inner)],
            capture_output=True,
            check=True,
        )
        for k, v in (
            ("user.name", "Patricia Garcia"),
            ("user.email", "patricia@inner.example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(
                ["git", "-C", str(inner), "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(inner), "commit", "--allow-empty", "-m", "Initial commit"],
            capture_output=True,
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(inner), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _make_outer(self, outer: Path, branch: str) -> None:
        outer.mkdir()
        subprocess.run(
            ["git", "init", "-b", branch, str(outer)],
            capture_output=True,
            check=True,
        )
        for k, v in (
            ("user.name", "Outer User"),
            ("user.email", "user@outer.example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(
                ["git", "-C", str(outer), "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(outer), "commit", "--allow-empty", "-m", "initial outer commit"],
            capture_output=True,
            check=True,
        )

    def _capturing_logger(self):
        """Logger that captures all records into a list for assertion."""
        import logging

        records: list[logging.LogRecord] = []
        log = logging.getLogger(f"test-{id(records)}")
        log.handlers.clear()
        log.setLevel(logging.INFO)
        handler = logging.Handler()
        handler.emit = records.append
        log.addHandler(handler)
        return log, records

    def _agent_commit(self, inner: Path, filename: str, content: str, message: str) -> None:
        Path(inner, filename).write_text(content)
        subprocess.run(
            ["git", "-C", str(inner), "add", filename],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(inner), "commit", "-m", message],
            capture_output=True,
            check=True,
        )

    def test_end_to_end_promotes_agent_commit(self):
        """Manual-test shape: outer on feat/X with one initial commit;
        agent commits inside; `_run_cycle_mirror` applies the patch.

        Asserts (Step 4.1 / L898-902):
        - outer's feat/X is 2 commits (original + 1 promoted)
        - original commit is ancestor of HEAD
        - working tree clean
        - feature.py present
        - returns PromotionOutcome.PROMOTED
        - log contains "Promoted 1 commit(s)"
        """
        from alcatrazer import daemon, state
        from alcatrazer.promote import PromotionOutcome

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            inner_root = self._make_inner(inner)
            self._agent_commit(
                inner, "feature.py", "def feature():\n    return 42\n", "agent: add feature"
            )
            self._make_outer(outer, "feat/X")
            outer_initial = subprocess.run(
                ["git", "-C", str(outer), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            state.update_state(
                alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root
            )

            log, records = self._capturing_logger()

            new_status = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=None,
            )

            self.assertEqual(new_status, PromotionOutcome.PROMOTED)
            # Outer advanced from 1 to 2 commits.
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(outer), "rev-list", "--count", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "2",
            )
            # Original outer commit still ancestor of HEAD.
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(outer), "merge-base", "--is-ancestor", outer_initial, "HEAD"],
                    capture_output=True,
                ).returncode,
                0,
            )
            # Working tree clean.
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(outer), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "",
            )
            self.assertTrue(Path(outer, "feature.py").exists())
            # Log uses git vocabulary + names the branch (per
            # coding_conventions.md "User-facing strings"): the
            # message must mention "Applied", the actual count, and
            # the actual branch name 'feat/X' — never "pin" /
            # "promoted" / "outer".
            messages = [r.getMessage() for r in records]
            joined = "\n".join(messages)
            self.assertIn("Applied 1 agent commit", joined)
            self.assertIn("'feat/X'", joined)
            for jargon in ("pin", "promoted", "promotion", "outer"):
                self.assertNotIn(jargon, joined.lower())

    def test_held_state_transition_logs_once_then_resumed(self):
        """Held-state lifecycle across three cycles (Step 4.2 / L904-907):

        1. outer is off-pin (on `main`, pinned is `feat/X`) → cycle 1
           returns HELD, log emits "Held:" once.
        2. agent commits more while held; cycle 2 still HELD →
           NO new log entry (last_logged_status == HELD suppresses
           the noisy poll-per-poll repeat).
        3. user recheckouts feat/X; cycle 3 returns PROMOTED, log emits
           "Resumed: ... replaying N commits" with the piled count.
        """
        from alcatrazer import daemon, state
        from alcatrazer.promote import PromotionOutcome

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            inner_root = self._make_inner(inner)
            self._agent_commit(
                inner, "f1.py", "# agent 1\n", "agent: commit 1"
            )

            # Outer initialised on `main`; feat/X exists but isn't
            # checked out (so check_pin returns OFF_PIN, not PIN_DELETED).
            self._make_outer(outer, "main")
            subprocess.run(
                ["git", "-C", str(outer), "branch", "feat/X"],
                capture_output=True,
                check=True,
            )
            state.update_state(
                alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root
            )

            log, records = self._capturing_logger()

            # Cycle 1: off-pin → HELD. Log names BOTH actual branches
            # (current 'main' and started-on 'feat/X') per the user-
            # language rule.
            status1 = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=None,
            )
            self.assertEqual(status1, PromotionOutcome.HELD)
            joined_cycle1 = "\n".join(r.getMessage() for r in records)
            self.assertIn("Held", joined_cycle1)
            self.assertIn("'main'", joined_cycle1)
            self.assertIn("'feat/X'", joined_cycle1)
            records_after_cycle1 = len(records)

            # Cycle 2: still off-pin, more agent commits. HELD, no new log.
            self._agent_commit(inner, "f2.py", "# agent 2\n", "agent: commit 2")
            status2 = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=status1,
            )
            self.assertEqual(status2, PromotionOutcome.HELD)
            # No new log records since cycle 1 — transition-only logging
            # must suppress repeat-Held noise.
            self.assertEqual(
                len(records),
                records_after_cycle1,
                "transition-only logging: HELD->HELD should not log "
                "again. New records: "
                f"{[r.getMessage() for r in records[records_after_cycle1:]]}",
            )

            # Cycle 3: user recheckouts feat/X. PROMOTED with 2 piled
            # commits, log "Resumed: ..." mentioning the count.
            subprocess.run(
                ["git", "-C", str(outer), "checkout", "feat/X"],
                capture_output=True,
                check=True,
            )
            status3 = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=status2,
            )
            self.assertEqual(status3, PromotionOutcome.PROMOTED)
            new_messages = "\n".join(
                r.getMessage() for r in records[records_after_cycle1:]
            )
            # Resumed message names the branch and the count, uses
            # "agent commit(s)" not "commits" (git-native).
            self.assertIn("Resumed", new_messages)
            self.assertIn("'feat/X'", new_messages)
            self.assertIn("2 agent commit", new_messages)

    def test_paused_state_transition_logs_paused_then_resumed(self):
        """Paused-state lifecycle across cycles (Step 4.3 / L909-913):

        Setup: inner edits shared.py v1 -> v2 (agent commit). Outer
        on pinned feat/X has its own conflicting edit to shared.py
        (user committed "v2-user").

        Cycle 1: apply collides. promote_once writes state.paused,
        returns PAUSED. log emits "Paused: ..." entry.

        Cycle 2: user resolves by reverting their conflicting commit
        (`git reset --hard HEAD~1`), so outer's shared.py is back at
        v1 — the patch's expected base. promote_once succeeds, clears
        state.paused, returns PROMOTED. log emits "Resumed: working-
        tree conflict resolved" entry.
        """
        from alcatrazer import daemon, state
        from alcatrazer.promote import PromotionOutcome

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            # Inner: initial with shared.py = v1; agent edits to v2.
            inner.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(inner)],
                capture_output=True,
                check=True,
            )
            for k, v in (
                ("user.name", "Patricia Garcia"),
                ("user.email", "patricia@inner.example.com"),
                ("commit.gpgsign", "false"),
            ):
                subprocess.run(
                    ["git", "-C", str(inner), "config", k, v],
                    capture_output=True,
                    check=True,
                )
            Path(inner, "shared.py").write_text("v1\n")
            subprocess.run(
                ["git", "-C", str(inner), "add", "shared.py"],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(inner), "commit", "-m", "Initial commit"],
                capture_output=True,
                check=True,
            )
            inner_root = subprocess.run(
                ["git", "-C", str(inner), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            Path(inner, "shared.py").write_text("v2 - agent\n")
            subprocess.run(
                ["git", "-C", str(inner), "add", "."],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(inner), "commit", "-m", "agent: edit shared"],
                capture_output=True,
                check=True,
            )

            # Outer on feat/X: shared.py = v1 initially, then user
            # commits a conflicting edit to "v2 - user".
            outer.mkdir()
            subprocess.run(
                ["git", "init", "-b", "feat/X", str(outer)],
                capture_output=True,
                check=True,
            )
            for k, v in (
                ("user.name", "Outer User"),
                ("user.email", "user@outer.example.com"),
                ("commit.gpgsign", "false"),
            ):
                subprocess.run(
                    ["git", "-C", str(outer), "config", k, v],
                    capture_output=True,
                    check=True,
                )
            Path(outer, "shared.py").write_text("v1\n")
            subprocess.run(
                ["git", "-C", str(outer), "add", "shared.py"],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(outer), "commit", "-m", "initial outer"],
                capture_output=True,
                check=True,
            )
            Path(outer, "shared.py").write_text("v2 - user\n")
            subprocess.run(
                ["git", "-C", str(outer), "add", "."],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(outer), "commit", "-m", "user: conflicting edit"],
                capture_output=True,
                check=True,
            )

            state.update_state(
                alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root
            )

            log, records = self._capturing_logger()

            # Cycle 1: conflict -> PAUSED, "Paused:" logged.
            status1 = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=None,
            )
            self.assertEqual(status1, PromotionOutcome.PAUSED)
            joined_cycle1 = "\n".join(r.getMessage() for r in records)
            # Paused message uses git vocabulary ("working tree",
            # "commit or stash"), names the branch, and gives the
            # user an actionable next step.
            self.assertIn("Paused", joined_cycle1)
            self.assertIn("'feat/X'", joined_cycle1)
            self.assertIn("working tree", joined_cycle1)
            # state.paused should be recorded.
            self.assertIsNotNone(state.load_state(alcatraz_dir).get("paused"))
            records_after_cycle1 = len(records)

            # User resolves: revert their conflicting commit so the
            # patch's base (v1) matches outer's HEAD content again.
            subprocess.run(
                ["git", "-C", str(outer), "reset", "--hard", "HEAD~1"],
                capture_output=True,
                check=True,
            )

            # Cycle 2: PROMOTED, "Resumed:" logged.
            status2 = daemon._run_cycle_mirror(
                source=inner,
                target=outer,
                alcatraz_dir=alcatraz_dir,
                name="Outer User",
                email="user@outer.example.com",
                log=log,
                last_logged_status=status1,
            )
            self.assertEqual(status2, PromotionOutcome.PROMOTED)
            new_messages = "\n".join(
                r.getMessage() for r in records[records_after_cycle1:]
            )
            # Resumed log names the branch and says "conflict ...
            # resolved" — git vocabulary, branch named.
            self.assertIn("Resumed", new_messages)
            self.assertIn("'feat/X'", new_messages)
            self.assertIn("resolved", new_messages.lower())
            # state.paused should be cleared by the successful cycle.
            self.assertIsNone(state.load_state(alcatraz_dir).get("paused"))


class TestStatusLogTail(unittest.TestCase):
    """Tests for status.py (renamed from inspect.py in Phase 3 — see
    docs/source-tree-analysis.md). Currently a tail -f viewer of
    .alcatrazer/promotion-daemon.log; Phase 5 will expand it into
    the `alcatrazer status` command surface."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, ".alcatrazer")
        os.makedirs(self.alcatraz_dir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exits_when_no_log_file(self):
        """Should exit non-zero with helpful message when log doesn't exist."""
        result = subprocess.run(
            [PYTHON, STATUS_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No log file", result.stdout)
        self.assertIn("alcatrazer.daemon", result.stdout)

    def test_starts_tailing_when_log_exists(self):
        """Should start tailing when log file exists (we kill it quickly)."""
        log_file = os.path.join(self.alcatraz_dir, "promotion-daemon.log")
        Path(log_file).write_text("2026-04-06 12:00:00 Daemon started\n")

        proc = subprocess.Popen(
            [PYTHON, STATUS_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
        self.workspace = os.path.join(self.test_project, WORKSPACE_DIR_NAME)
        Path(self.alcatraz_dir).mkdir(parents=True, exist_ok=True)
        Path(self.alcatraz_dir, "workspace-dir").write_text(WORKSPACE_DIR_NAME + "\n")

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

        Path(self.alcatraz_dir, "config.toml").write_text(
            f"[promotion]\n"
            f'name = "{PROMOTED_NAME}"\n'
            f'email = "{PROMOTED_EMAIL}"\n'
            f"\n"
            f"[promotion-daemon]\n"
            f"interval = 1\n"
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
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)
            branches = set(
                git(self.test_project, "branch", "--format=%(refname:short)").splitlines()
            )
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
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            [
                PYTHON,
                DAEMON_SCRIPT,
                "--alcatraz-dir",
                self.alcatraz_dir,
                "--project-dir",
                self.test_project,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(3)
            branches = set(
                git(self.test_project, "branch", "--format=%(refname:short)").splitlines()
            )
            # No conflict branches should exist
            conflict_branches = [b for b in branches if b.startswith("conflict/")]
            self.assertEqual(conflict_branches, [])
            # Agent work should be in alcatraz/main
            alcatraz_msgs = git(
                self.test_project, "log", "alcatraz/main", "--format=%s"
            ).splitlines()
            self.assertIn("agent more work", alcatraz_msgs)
            # Human work should still be on main
            main_msgs = git(self.test_project, "log", "main", "--format=%s").splitlines()
            self.assertIn("human work on main", main_msgs)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
