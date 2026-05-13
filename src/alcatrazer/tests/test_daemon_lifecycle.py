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

from alcatrazer import daemon_lifecycle, schema, state
from alcatrazer.daemon import DEFAULTS
from alcatrazer.daemon_lifecycle import launch_sync_daemon, shutdown_sync_daemon


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
            f"schema_version = {schema.ALCATRAZER_CONFIG.current_version}\n"
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
        self.assertEqual(info.interval, DEFAULTS["interval"])

    def test_launch_info_paths_point_into_alcatraz_dir(self):
        info = launch_sync_daemon(self.project_dir)
        self.assertEqual(info.pid_file, self.alcatraz_dir / "promotion-daemon.pid")
        self.assertEqual(info.log_file, self.alcatraz_dir / "promotion-daemon.log")
        self.assertEqual(info.config_file, self.alcatraz_dir / "config.toml")


class ParseShutdownLogTests(unittest.TestCase):
    """Pure-function parser — extract synced_count / conflict branches /
    failure flag from the daemon's log tail. Unit-tested without a real
    daemon so we can cover edge cases the integration path rarely hits."""

    def test_parses_synced_count_from_graceful_line(self):
        log = (
            "2026-04-23 09:00:00 Daemon started (PID 1, interval=1s)\n"
            "2026-04-23 09:00:05 Final sync (graceful shutdown): 3 commit(s) synced\n"
            "2026-04-23 09:00:05 Daemon stopped\n"
        )
        synced, conflicts, failed = daemon_lifecycle._parse_shutdown_log(log)
        self.assertEqual(synced, 3)
        self.assertEqual(conflicts, [])
        self.assertFalse(failed)

    def test_parses_synced_count_from_unexpected_line(self):
        """Unexpected-shutdown log line has the same shape as graceful,
        just different prefix text. Parser must accept both."""
        log = "2026-04-23 09:00:05 Final sync (unexpected shutdown): 7 commit(s) synced\n"
        synced, _, _ = daemon_lifecycle._parse_shutdown_log(log)
        self.assertEqual(synced, 7)

    def test_detects_conflict_branches(self):
        log = (
            "2026-04-23 09:00:01 CONFLICT on branch feat/a — promoted state saved "
            "to conflict/resolve-* branch. Resolve manually.\n"
            "2026-04-23 09:00:02 CONFLICT on branch feat/b — promoted state saved "
            "to conflict/resolve-* branch. Resolve manually.\n"
            "2026-04-23 09:00:05 Final sync (graceful shutdown): 1 commit(s) synced\n"
        )
        synced, conflicts, failed = daemon_lifecycle._parse_shutdown_log(log)
        self.assertEqual(synced, 1)
        self.assertEqual(set(conflicts), {"feat/a", "feat/b"})
        self.assertFalse(failed)

    def test_detects_final_sync_failure(self):
        log = (
            "2026-04-23 09:00:05 Final sync (unexpected shutdown) failed: "
            "git fast-import exited nonzero\n"
        )
        synced, _, failed = daemon_lifecycle._parse_shutdown_log(log)
        self.assertEqual(synced, 0)
        self.assertTrue(failed)

    def test_empty_log_returns_zero_no_conflicts_no_failure(self):
        synced, conflicts, failed = daemon_lifecycle._parse_shutdown_log("")
        self.assertEqual(synced, 0)
        self.assertEqual(conflicts, [])
        self.assertFalse(failed)

    def test_duplicate_conflict_lines_deduped(self):
        """Daemon may log the same conflict across multiple polls if a
        branch stays conflicted — parser collapses to distinct names."""
        log = (
            "CONFLICT on branch feat/a — ...\n"
            "CONFLICT on branch feat/a — ...\n"
            "CONFLICT on branch feat/a — ...\n"
        )
        _, conflicts, _ = daemon_lifecycle._parse_shutdown_log(log)
        self.assertEqual(conflicts, ["feat/a"])


class ShutdownSyncDaemonTests(unittest.TestCase):
    """shutdown_sync_daemon protocol: state=requested → SIGTERM → wait
    → parse log → state=done. Integration-ish because the helper's job
    is the signal/wait dance — mocking signals would test the mock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        workspace_name = ".devspace-shutdown"
        (self.alcatraz_dir / "workspace-dir").write_text(workspace_name + "\n")
        self.workspace = self.project_dir / workspace_name
        self.workspace.mkdir()
        subprocess.run(["git", "init", str(self.workspace)], capture_output=True, check=True)
        _git(self.workspace, "config", "user.name", "Alcatraz Agent")
        _git(self.workspace, "config", "user.email", "alcatraz@localhost")
        _git(self.workspace, "config", "commit.gpgsign", "false")
        subprocess.run(["git", "init", str(self.project_dir)], capture_output=True, check=True)
        _git(self.project_dir, "config", "user.name", "Test User")
        _git(self.project_dir, "config", "user.email", "test@example.com")
        _git(self.project_dir, "config", "commit.gpgsign", "false")
        (self.project_dir / "seed.txt").write_text("outer seed\n")
        _git(self.project_dir, "add", "seed.txt")
        _git(self.project_dir, "commit", "-m", "init outer")
        (self.alcatraz_dir / "config.toml").write_text(
            f"schema_version = {schema.ALCATRAZER_CONFIG.current_version}\n"
            '[promotion]\nname = "Test User"\nemail = "test@example.com"\n'
            "[promotion-daemon]\ninterval = 1\n"
        )
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        pid_file = self.alcatraz_dir / "promotion-daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            except (ProcessLookupError, ValueError, OSError):
                pass

    def test_no_op_outcome_when_no_daemon_running(self):
        """No PID file → nothing to shut down. Return cleanly."""
        result = shutdown_sync_daemon(self.project_dir)
        self.assertEqual(result.outcome, "no_daemon")
        self.assertEqual(result.synced_count, 0)

    def test_state_flipped_to_done_even_when_no_daemon(self):
        """Even with no daemon running, flip state to `done` so a
        lingering `requested` from a prior crashed cycle doesn't stay
        forever and confuse the NEXT daemon about its shutdown cause."""
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        shutdown_sync_daemon(self.project_dir)
        self.assertEqual(
            state.load_state(self.alcatraz_dir).get("daemon_shutdown"),
            "done",
        )

    def test_terminates_running_daemon_and_flips_state(self):
        """End-to-end: start a real daemon, shut it down, verify
        process is gone, PID file is gone, state.json reads `done`."""
        info = launch_sync_daemon(self.project_dir)
        pid = info.pid
        result = shutdown_sync_daemon(self.project_dir)
        self.assertEqual(result.outcome, "synced")
        # Process gone.
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        # PID file cleaned up by daemon's own shutdown path.
        self.assertFalse((self.alcatraz_dir / "promotion-daemon.pid").exists())
        # State flag closed.
        self.assertEqual(
            state.load_state(self.alcatraz_dir).get("daemon_shutdown"),
            "done",
        )

    def test_daemon_logs_graceful_prefix_because_cli_set_requested(self):
        """Verifies the CLI ↔ daemon cooperation handshake: when the
        CLI helper writes `requested`, the daemon's shutdown path sees
        it (via `state.load_state`) and logs the graceful prefix."""
        launch_sync_daemon(self.project_dir)
        shutdown_sync_daemon(self.project_dir)
        log = (self.alcatraz_dir / "promotion-daemon.log").read_text()
        self.assertIn("Final sync (graceful shutdown)", log, log)


if __name__ == "__main__":
    unittest.main()
