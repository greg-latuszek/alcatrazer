"""Shared fixture + git/files/daemon vocabulary for the promotion-flow
integration tests.

`PromotionFlowTest` builds a real `DockerPrison`-backed workspace from a
seeded outer git repo (via `alcatrazer init`) and exposes the step
vocabulary every promotion scenario speaks — branch switches, agent/user
commits, start/clear, and outer/inner assertions.

Each concrete scenario subclasses this with a single self-naming test
method. Because `setUpClass` runs once per class, every subclass gets its
OWN isolated container + outer repo + daemon — full test separation — while
the fixture and helpers are written here exactly once (no repetition).

This is a support module (no `test_` prefix), so the runner does not collect
it directly; the base carries no `test_*` methods of its own. Gated behind
Docker via the class-level skip, which subclasses inherit.

Requires Docker; run with `mise test-smoke`.
"""

import contextlib
import io
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import start as start_mod
from alcatrazer import state
from alcatrazer import status as status_mod
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.git_runner import run_git_command
from alcatrazer.integration_tests.test_smoke import (
    CODING_ENV,
    _docker_available,
    _nuke_phantom_uid_files,
    _seed_project,
)


@unittest.skipUnless(_docker_available(), "Docker not available")
class PromotionFlowTest(unittest.TestCase):
    """Fixture + vocabulary base for every promotion-flow scenario.

    Holds no `test_*` methods — concrete scenarios subclass it, one self-
    naming test each, and inherit both the per-class fixture (so each gets
    its own isolated container) and the step vocabulary below. Subclasses
    arrange their own Given/When choreography from these steps; the base only
    provides the capabilities."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Ghost Agent", "ghost@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod.cmd_init(cls.project_dir)
        if rc != 0:
            raise RuntimeError(f"cmd_init failed (rc={rc})")

        cls.alcatraz_dir = cls.project_dir / ".alcatrazer"

        # Speed up daemon polling so the agent-commit phase doesn't
        # spend most of its time waiting (matches the lifecycle smoke).
        config_path = cls.alcatraz_dir / "config.toml"
        config_path.write_text(config_path.read_text().replace("interval = 5", "interval = 1"))

        cls.workspace_name = (cls.alcatraz_dir / "workspace-dir").read_text().strip()
        cls.workspace = cls.project_dir / cls.workspace_name
        cls.prison = DockerPrison(cls.project_dir)

    @classmethod
    def tearDownClass(cls):
        # Reap any leftover daemon (mid-test failure could leak one).
        pid_file = cls.alcatraz_dir / "promotion-daemon.pid"
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError, OSError):
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, os.WNOHANG)
        with contextlib.suppress(Exception):
            cls.prison.stop()
        with contextlib.suppress(Exception):
            cls.prison.remove()
        _nuke_phantom_uid_files(cls.project_dir)
        with contextlib.suppress(Exception):
            cls._tmp.cleanup()

    # ── Actors and their actions ──────────────────────────────────────

    def _user_creates_branch(self, name: str) -> None:
        run_git_command(["-C", str(self.project_dir), "checkout", "-b", name], check=True)

    def _user_returns_to_branch(self, name: str) -> None:
        run_git_command(["-C", str(self.project_dir), "checkout", name], check=True)

    def _user_deletes_branch(self, name: str) -> None:
        # git refuses to delete the currently-checked-out branch, so the caller
        # must `_user_returns_to_branch(<elsewhere>)` first.
        run_git_command(["-C", str(self.project_dir), "branch", "-D", name], check=True)

    def _user_detaches_head(self) -> None:
        """Detach HEAD at the current commit (no branch is checked out)."""
        run_git_command(["-C", str(self.project_dir), "checkout", "--detach", "HEAD"], check=True)

    def _user_creates_file_in_outer(self, filename: str, content: str) -> None:
        """Create an untracked file in the outer working tree (no git add, no
        commit) — used to stage a path-collision with an agent commit so the
        daemon's `git am` fails with 'already exists in working directory'."""
        (self.project_dir / filename).write_text(content)

    def _user_removes_file_in_outer(self, filename: str) -> None:
        """Delete a file from the outer working tree — the resolution path for
        a path-collision pause (the daemon's diff never surfaces, so removal,
        not in-tree merge, is what clears the way)."""
        (self.project_dir / filename).unlink()

    def _user_edits_tracked_file_in_outer(self, filename: str, content: str) -> None:
        """Modify an existing tracked file in the outer working tree without
        staging or committing — i.e., make the file 'dirty' from git's view.
        Same write-file mechanic as creating an untracked file, but different
        intent (the file is already part of the repo's history)."""
        (self.project_dir / filename).write_text(content)

    def _user_commits(self, message: str, filename: str) -> None:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"cd {self.project_dir} && echo {message!r} > {filename} && "
                f"git add {filename} && git commit -qm {message!r}",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"user commit failed: {result.stderr}")

    def _agent_commits(self, message: str, filename: str) -> None:
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"cd /workspace && echo {message!r} > {filename} && "
                f"git add {filename} && git commit -qm {message!r}",
            ]
        )
        self.assertEqual(result.returncode, 0, f"agent commit failed: {result.stderr}")

    def _alcatrazer_starts(self) -> None:
        rc = start_mod.cmd_start(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_start should succeed")
        print(f"workspace after start: {self._run_in_workspace('ls -la /workspace')}")

    def _alcatrazer_clears(self) -> None:
        rc = start_mod.cmd_clear(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_clear should succeed")
        # The container is gone now — inspect the host-side mount point.
        print(f"workspace dir after clear: {self._run_in_outer_repo(f'ls -la {self.workspace}')}")
        print(f"outer repo after clear: {self._run_in_outer_repo(f'ls -la {self.project_dir}')}")

    # ── Composite givens ──────────────────────────────────────────────

    def _given_alcatrazer_started_on_branch(self, branch: str) -> None:
        """Branch off, start Alcatraz, and confirm the workspace pinned there."""
        self._user_creates_branch(branch)
        self._alcatrazer_starts()
        self._assert_workspace_pinned_to(branch)

    # ── Observations ──────────────────────────────────────────────────

    def _assert_workspace_pinned_to(self, branch: str) -> None:
        self.assertEqual(
            self._pinned_branch(),
            branch,
            f"pinned_branch should track outer's checked-out branch; got {self._pinned_branch()!r}",
        )

    def _assert_workspace_repo_exists(self) -> None:
        self.assertTrue(
            (self.workspace / ".git").is_dir(), "workspace should be a git repo after start"
        )

    def _assert_workspace_is_wiped(self) -> None:
        # The mount point survives (bind-mount target), but its contents —
        # incl. .git — are gone: the wipe ran inside the container as the
        # agent UID, with no chown back to the host.
        self.assertTrue(self.workspace.is_dir(), "workspace mount-point dir must remain")
        self.assertEqual(
            list(self.workspace.iterdir()),
            [],
            "after clear, inner workspace contents must be wiped (incl. .git)",
        )

    def _assert_pin_is_dropped(self) -> None:
        self.assertFalse(
            (self.alcatraz_dir / "state.json").exists(),
            "after clear, state.json must be gone so the next start gets a fresh pin",
        )

    def _assert_daemon_synced_to_outer(self, subject: str) -> None:
        self.assertTrue(
            self._wait_until_outer_has_commit(subject),
            f"daemon must promote commit {subject!r} to the outer branch",
        )

    def _assert_outer_has(self, filename: str) -> None:
        self.assertTrue(
            (self.project_dir / filename).exists(), f"outer working tree should contain {filename}"
        )

    def _assert_outer_lacks(self, filename: str) -> None:
        self.assertFalse(
            (self.project_dir / filename).exists(),
            f"outer working tree should not contain {filename}",
        )

    def _assert_workspace_has(self, filename: str) -> None:
        self.assertTrue(
            (self.workspace / filename).exists(), f"inner workspace should contain {filename}"
        )

    def _assert_workspace_lacks(self, filename: str) -> None:
        self.assertFalse(
            (self.workspace / filename).exists(),
            f"inner workspace should not contain {filename}",
        )

    def _assert_outer_commit_count_is(self, branch: str, expected: int) -> None:
        actual = self._outer_commit_count(branch)
        self.assertEqual(
            actual,
            expected,
            f"{branch} should hold {expected} commits after promotion; found {actual}",
        )

    def _assert_outer_tip_authored_and_committed_by(
        self, branch: str, name: str, email: str
    ) -> None:
        # Both author and committer must be the user's promotion identity —
        # the inner agent's (random) identity must not leak into either field.
        author_name, author_email, committer_name, committer_email = run_git_command(
            ["-C", str(self.project_dir), "log", "-1", "--format=%an%n%ae%n%cn%n%ce", branch],
            check=True,
        ).stdout.splitlines()
        self.assertEqual(
            [author_name, author_email, committer_name, committer_email],
            [name, email, name, email],
            f"{branch} tip should be authored AND committed as {name} <{email}>",
        )

    def _assert_outer_tracked_file_is_modified(self, filename: str) -> None:
        """Assert `filename` appears as modified-but-unstaged in `git status`
        — i.e., the user's edit is still pending in the working tree, not
        accidentally staged, stashed, or reverted by promotion."""
        status = run_git_command(
            ["-C", str(self.project_dir), "status", "--porcelain", filename], check=True
        ).stdout
        # Porcelain format: `XY <space> <path>`; " M <path>" is
        # working-tree-modified, index-clean.
        self.assertTrue(
            status.startswith(" M "),
            f"{filename} should be modified-but-unstaged; git status:\n{status!r}",
        )

    def _assert_outer_working_tree_is_clean(self) -> None:
        # Tracked-file view only (`--untracked-files=no`): the init artifacts
        # (.alcatrazer/, the workspace dir, coding-environment.toml, .env.example)
        # are expected untracked entries, not part of the promotion contract.
        # The bug this guards is a phantom "deleted: <tracked file>" left when
        # the ref advances but the working tree doesn't — that surfaces here.
        status = run_git_command(
            ["-C", str(self.project_dir), "status", "--porcelain", "--untracked-files=no"],
            check=True,
        ).stdout
        self.assertEqual(status, "", f"outer working tree should be clean; git status:\n{status}")

    def _assert_daemon_holds(self, timeout: float = 10.0) -> None:
        """Wait until the daemon's log records a Held transition.

        The fixture's poll interval is 1s, so the next poll after the user
        moved off-pin will log the Held line; we poll the log file rather
        than guess at a sleep."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "Held:" in self._daemon_log_text():
                return
            time.sleep(0.2)
        self.fail(
            f"daemon should have logged a Held transition within {timeout}s; "
            f"log:\n{self._daemon_log_text()}"
        )

    def _assert_daemon_log_records_one_held_resumed_cycle(self) -> None:
        log = self._daemon_log_text()
        held = sum(1 for line in log.splitlines() if "Held:" in line)
        resumed = sum(1 for line in log.splitlines() if "Resumed:" in line)
        self.assertEqual(
            (held, resumed),
            (1, 1),
            f"daemon should log exactly one Held + one Resumed transition; "
            f"got Held={held} Resumed={resumed}. Log:\n{log}",
        )

    def _assert_daemon_pauses(self, timeout: float = 10.0) -> None:
        """Wait until the daemon's log records a Paused transition.

        Same shape as _assert_daemon_holds — poll the log rather than
        guess a sleep, so the next cycle that hits an `am` conflict and
        runs `am --abort` triggers the wait release."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "Paused:" in self._daemon_log_text():
                return
            time.sleep(0.2)
        self.fail(
            f"daemon should have logged a Paused transition within {timeout}s; "
            f"log:\n{self._daemon_log_text()}"
        )

    def _assert_status_reports_paused(self) -> None:
        output = self._alcatrazer_status_output()
        self.assertIn("paused", output, f"status should report paused; got:\n{output}")
        # The user-facing hint must point at removal/rename — the daemon's
        # diff never surfaces in outer, so committing/merging the user's
        # version is not a resolution path (see
        # project_promotion_conflict_semantics).
        self.assertIn(
            "remove it or rename",
            output,
            f"status should explain removal-as-resolution; got:\n{output}",
        )

    def _assert_daemon_log_records_one_paused_resumed_cycle(self) -> None:
        log = self._daemon_log_text()
        paused = sum(1 for line in log.splitlines() if "Paused:" in line)
        resumed = sum(1 for line in log.splitlines() if "Resumed:" in line)
        self.assertEqual(
            (paused, resumed),
            (1, 1),
            f"daemon should log exactly one Paused + one Resumed transition; "
            f"got Paused={paused} Resumed={resumed}. Log:\n{log}",
        )

    def _assert_status_reports_on_hold_with_pending(self, count: int) -> None:
        output = self._alcatrazer_status_output()
        self.assertIn("on hold", output, f"status should report on-hold; got:\n{output}")
        self.assertIn(
            f"Pending commits:  {count}",
            output,
            f"status should report {count} pending commit(s); got:\n{output}",
        )

    # ── Low-level access to the two repos and the container ───────────

    def _pinned_branch(self) -> str | None:
        return state.load_state(self.alcatraz_dir).get("pinned_branch")

    def _run_in_workspace(self, command: str) -> str:
        result = self.prison.query(["bash", "-c", command])
        self.assertEqual(result.returncode, 0, f"command in workspace failed: {result.stderr}")
        return result.stdout

    def _run_in_outer_repo(self, command: str) -> str:
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"command in outer repo failed: {result.stderr}")
        return str(result.stdout)

    def _wait_until_outer_has_commit(self, subject: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            outer_log = run_git_command(
                ["-C", str(self.project_dir), "log", "--all", "--format=%s"], check=True
            )
            if subject in outer_log.stdout.splitlines():
                return True
            time.sleep(0.5)
        return False

    def _outer_commit_count(self, branch: str) -> int:
        return int(
            run_git_command(
                ["-C", str(self.project_dir), "rev-list", "--count", branch], check=True
            ).stdout.strip()
        )

    def _daemon_log_text(self) -> str:
        log_file = self.alcatraz_dir / "promotion-daemon.log"
        return log_file.read_text() if log_file.exists() else ""

    def _alcatrazer_status_output(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status_mod.cmd_status(self.project_dir)
        return buf.getvalue()
