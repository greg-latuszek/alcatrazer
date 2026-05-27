"""End-to-end integration test for the switch-branch flow Phase 9
enables.

Drives the real `alcatrazer init` / `start` / `clear` sequence against
a real DockerPrison + real container, with a real outer git repo, to
verify the user-visible promise:

    git checkout -b feat/X
    alcatrazer start                     # workspace pinned to feat/X
    # ... agents work ...
    alcatrazer clear                     # terminal teardown: workspace
                                         # wiped, pin dropped
    git checkout -b other-branch
    alcatrazer start                     # fresh workspace pinned to
                                         # other-branch

Today (pre-Phase-9) this is broken — `clear` preserves the workspace
+ state.json, so the second `start` reuses the old pin and
`alcatrazer status` reports ⚠ on hold on `other-branch` even though
the user wanted a fresh workspace. The test fails RED on that
behaviour; Phase 9.4 GREEN's `cmd_clear` extension makes it pass.

This test is an integration test in the strictest sense — no mocks at
the prison layer, no host-side simulation of the wipe. The container
actually runs, `find /workspace -mindepth 1 -delete` actually runs
inside as the agent UID, and the bind-mount actually propagates the
empty state to the host. That's the only way to verify the wipe-
from-inside contract (Principle 2: agent never observes host UID).

Lives in `integration_tests/` next to `test_smoke.py` but is NOT a
smoke test (smoke is for security invariants + tooling availability
checks per the three-tier discipline in install_method.md). Workflow
integration tests are a separate purpose; both are gated behind
Docker availability and opt out of the default `alcatrazer test` run
via the integration-tests location.

Requires Docker; run with `mise test-smoke`.
"""

import contextlib
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
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.integration_tests.test_smoke import (
    CODING_ENV,
    _docker_available,
    _nuke_phantom_uid_files,
    _seed_project,
)


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestSwitchBranchFlow(unittest.TestCase):
    """Phase 9 Step 9.3 — alcatrazer clear + start re-pins to the
    user's currently-checked-out branch.

    Single test method by design (mirrors TestAlcatrazSmokeLifecycle):
    the flow is stateful, and each step is a named action or
    observation, so a failure's call site names which step of the
    user-flow broke."""

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

    # ── The switch-branch user-flow, read as prose ────────────────────

    def test_alcatrazer_repins_the_workspace_to_the_current_branch_when_restarted_after_clear(self):
        # The user branches off and starts: the workspace pins to feat/X.
        self._user_creates_branch("feat/X")
        self._alcatrazer_starts()
        self._assert_workspace_pinned_to("feat/X")
        self._assert_workspace_repo_exists()
        self._assert_outer_lacks("agent.txt")

        # The agent commits inside; the daemon syncs it back to feat/X.
        self._agent_commits("switch-flow: agent commit on feat/X", "agent.txt")
        self._assert_daemon_synced_to_outer("switch-flow: agent commit on feat/X")
        self._assert_workspace_has("agent.txt")
        self._assert_outer_has("agent.txt")

        # The user clears: the inner workspace is wiped and the pin
        # dropped, but the already-synced commit stays in the outer repo.
        self._alcatrazer_clears()
        self._assert_pin_is_dropped()
        self._assert_workspace_is_wiped()
        self._assert_outer_has("agent.txt")

        # The user switches to a new branch and starts again: the fresh
        # workspace re-pins to other-branch and mirrors ITS tree, not
        # feat/X's.
        self._user_returns_to_branch("main")
        self._assert_outer_lacks("agent.txt")
        self._user_creates_branch("other-branch")
        self._user_commits("switch-flow: user commit on other-branch", "user.txt")
        self._assert_outer_has("user.txt")
        self._alcatrazer_starts()
        self._assert_workspace_pinned_to("other-branch")
        self._assert_workspace_has("user.txt")
        self._assert_workspace_lacks("agent.txt")

    # ── Actors and their actions ──────────────────────────────────────

    def _user_creates_branch(self, name: str) -> None:
        self._git("checkout", "-b", name)

    def _user_returns_to_branch(self, name: str) -> None:
        self._git("checkout", name)

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

    # ── Low-level access to the two repos and the container ───────────

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.project_dir), *args],
            capture_output=True,
            text=True,
            check=True,
        )

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
            if subject in self._git("log", "--all", "--format=%s").stdout.splitlines():
                return True
            time.sleep(0.5)
        return False


if __name__ == "__main__":
    unittest.main()
