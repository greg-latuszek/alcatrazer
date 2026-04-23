"""Tests for `alcatrazer.daemon_lifecycle` — the CLI-side helpers that
spawn and terminate the sync daemon.

These are integration-ish tests: they spawn the real daemon subprocess
because the helpers' whole job is orchestrating `subprocess.Popen` and
signals. Mocking the subprocess would test the mock, not the wiring.
Fixture is kept minimal (git-init'd inner + outer repos, toml with
short interval) so each test takes ~1s.
"""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from alcatrazer.daemon_lifecycle import launch_sync_daemon


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


class LaunchSyncDaemonTests(unittest.TestCase):
    """launch_sync_daemon — spawn when absent, self-heal when stale,
    no-op when alive. Returns a DaemonLaunchInfo the CLI prints."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        # Workspace pointer + inner repo so daemon's resolve_workspace passes.
        workspace_name = ".devspace-launch"
        (self.alcatraz_dir / "workspace-dir").write_text(workspace_name + "\n")
        self.workspace = self.project_dir / workspace_name
        self.workspace.mkdir()
        subprocess.run(["git", "init", str(self.workspace)], capture_output=True, check=True)
        _git(self.workspace, "config", "user.name", "Alcatraz Agent")
        _git(self.workspace, "config", "user.email", "alcatraz@localhost")
        _git(self.workspace, "config", "commit.gpgsign", "false")
        # Outer repo so promote() has a target.
        subprocess.run(["git", "init", str(self.project_dir)], capture_output=True, check=True)
        _git(self.project_dir, "config", "user.name", "Test User")
        _git(self.project_dir, "config", "user.email", "test@example.com")
        _git(self.project_dir, "config", "commit.gpgsign", "false")
        (self.project_dir / "seed.txt").write_text("outer seed\n")
        _git(self.project_dir, "add", "seed.txt")
        _git(self.project_dir, "commit", "-m", "init outer")
        # Config with promotion identity (so daemon's resolve_identity works)
        # and a short interval to keep tests fast.
        (self.alcatraz_dir / "config.toml").write_text(
            '[promotion]\nname = "Test User"\nemail = "test@example.com"\n'
            "[promotion-daemon]\ninterval = 1\n"
        )
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        """Kill any leftover daemon. tearDown needs to cover the case where
        a test succeeded partway and left the daemon running."""
        pid_file = self.alcatraz_dir / "promotion-daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                # Give daemon a moment to shut down cleanly.
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
            except (ProcessLookupError, ValueError, OSError):
                pass

    def test_spawns_fresh_daemon_when_no_pid_file(self):
        info = launch_sync_daemon(self.project_dir)
        self.assertFalse(info.was_already_running)
        self.assertTrue(info.pid_file.exists())
        # Verify process is alive.
        os.kill(info.pid, 0)  # raises if dead

    def test_returns_existing_daemon_info_when_alive(self):
        """Second call on an already-running daemon must not relaunch —
        returns the live PID and flags `was_already_running=True`.
        Self-heal property needed by cmd_start's fast path."""
        first = launch_sync_daemon(self.project_dir)
        second = launch_sync_daemon(self.project_dir)
        self.assertTrue(second.was_already_running)
        self.assertEqual(first.pid, second.pid)

    def test_relaunches_when_pid_file_is_stale(self):
        """Stale PID file (process died without cleanup, e.g. SIGKILL /
        OOM). Helper must detect the stale entry, delete it, and spawn
        a fresh daemon. Without this, cmd_start's fast path would
        refuse to relaunch a legitimately-dead daemon."""
        pid_file = self.alcatraz_dir / "promotion-daemon.pid"
        pid_file.write_text("999999\n")  # Unlikely to be a real PID.
        info = launch_sync_daemon(self.project_dir)
        self.assertFalse(info.was_already_running)
        self.assertNotEqual(info.pid, 999999)

    def test_relaunches_when_pid_file_is_corrupt(self):
        """Non-integer PID content — treat as stale, clean up, relaunch."""
        pid_file = self.alcatraz_dir / "promotion-daemon.pid"
        pid_file.write_text("not-a-pid\n")
        info = launch_sync_daemon(self.project_dir)
        self.assertFalse(info.was_already_running)
        # Fresh PID, not whatever garbage was in the file.
        self.assertGreater(info.pid, 0)

    def test_returns_interval_from_config_toml(self):
        info = launch_sync_daemon(self.project_dir)
        self.assertEqual(info.interval, 1)

    def test_returns_default_interval_when_config_absent(self):
        """No config.toml at all — helper uses daemon's defaults."""
        (self.alcatraz_dir / "config.toml").unlink()
        info = launch_sync_daemon(self.project_dir)
        # Daemon's default from DEFAULTS dict.
        from alcatrazer.daemon import DEFAULTS

        self.assertEqual(info.interval, DEFAULTS["interval"])

    def test_launch_info_paths_point_into_alcatraz_dir(self):
        info = launch_sync_daemon(self.project_dir)
        self.assertEqual(info.pid_file, self.alcatraz_dir / "promotion-daemon.pid")
        self.assertEqual(info.log_file, self.alcatraz_dir / "promotion-daemon.log")
        self.assertEqual(info.config_file, self.alcatraz_dir / "config.toml")


if __name__ == "__main__":
    unittest.main()
