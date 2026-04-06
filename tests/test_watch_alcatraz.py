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
    """Resolve Python from .alcatraz/python symlink, fall back to sys."""
    python_file = project_dir() / ".alcatraz" / "python"
    if python_file.is_symlink() or python_file.exists():
        return str(python_file.resolve())
    import sys
    return sys.executable


DAEMON_SCRIPT = str(project_dir() / "src" / "watch_alcatraz.py")
PYTHON = python_bin()


class TestConfigLoading(unittest.TestCase):
    """Test that the daemon reads config from alcatrazer.toml correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatraz")
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
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatraz")
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
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatraz")
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
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatraz")
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


if __name__ == "__main__":
    unittest.main()
