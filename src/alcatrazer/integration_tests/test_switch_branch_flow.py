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
    the phases are stateful, and the line number of any failure points
    directly at which step of the user-flow broke."""

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

    # ── Helpers (subset of the lifecycle smoke's set, copy-light) ─────

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.project_dir), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def _current_outer_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _pinned_branch(self) -> str | None:
        return state.load_state(self.alcatraz_dir).get("pinned_branch")

    def _commit_in_workspace(self, msg: str, filename: str) -> None:
        r = self.prison.query(
            [
                "bash",
                "-c",
                f"cd /workspace && echo {msg!r} > {filename} && "
                f"git add {filename} && git commit -qm {msg!r}",
            ]
        )
        self.assertEqual(r.returncode, 0, f"Inner commit failed: {r.stderr}")

    def _commit_in_project_repo(self, msg: str, filename: str) -> None:
        r = subprocess.run(
            [
                "bash",
                "-c",
                f"cd {self.project_dir} && echo {msg!r} > {filename} && "
                f"git add {filename} && git commit -qm {msg!r}",
            ]
        )
        self.assertEqual(r.returncode, 0, f"Outer commit failed: {r.stderr}")

    def _cmd_in_workspace(self, cmd: str) -> str:
        r = self.prison.query(
            [
                "bash",
                "-c",
                cmd,
            ]
        )
        self.assertEqual(r.returncode, 0, f"Inner command failed: {r.stderr}")
        return r.stdout

    def _cmd_in_project_repo(self, cmd: str) -> str:
        r = subprocess.run(
            [
                "bash",
                "-c",
                cmd,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, f"Outer command failed: {r.stderr}")
        return str(r.stdout)

    def _wait_for_outer_subject(self, msg: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._git("log", "--all", "--format=%s")
            if msg in r.stdout.splitlines():
                return True
            time.sleep(0.5)
        return False

    # ── The switch-branch user-flow ───────────────────────────────────

    def test_clear_then_start_repins_to_current_branch(self):
        # Phase 1 — branch off main, then start. Workspace pins to feat/X.
        self._git("checkout", "-b", "feat/X")
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.project_dir}")
        print(f"Project has following content before first snapshot: {cmd_output}")
        rc = start_mod.cmd_start(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "first cmd_start should succeed")
        self.assertEqual(
            self._pinned_branch(),
            "feat/X",
            "after first start, state.json.pinned_branch should match outer's checked-out branch",
        )
        self.assertTrue((self.workspace / ".git").is_dir())
        self.assertFalse(
            (self.project_dir / "agent.txt").exists(),
            "outer working tree should not have file planned for agent creation",
        )
        cmd_output = self._cmd_in_workspace("ls -la /workspace")
        print(f"Alcatraz has following content after first start: {cmd_output}")

        # Phase 2 — agent makes a commit; daemon syncs it back to feat/X.
        # Realistic: clear's job is to drain pending commits before teardown,
        # so we want there to be commits to drain.
        self._commit_in_workspace("switch-flow: agent commit on feat/X", "agent.txt")
        self.assertTrue(
            self._wait_for_outer_subject("switch-flow: agent commit on feat/X"),
            "daemon must promote the agent commit to outer feat/X before clear",
        )
        self.assertTrue(
            (self.workspace / "agent.txt").exists(),
            "inner working tree should have agent created file",
        )
        self.assertTrue(
            (self.project_dir / "agent.txt").exists(),
            "outer working tree should have agent created file",
        )
        cmd_output = self._cmd_in_workspace("ls -la /workspace")
        print(f"Alcatraz has following content after first agent creation: {cmd_output}")
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.project_dir}")
        print(f"Project has following content after first sync: {cmd_output}")

        # Phase 3 — clear. The terminal teardown: drain (already drained,
        # but cmd_clear still runs final-sync), wipe inner workspace, unpin.
        rc = start_mod.cmd_clear(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_clear should succeed")
        self.assertFalse(
            (self.alcatraz_dir / "state.json").exists(),
            "after clear, state.json must be gone so the next start gets a fresh pin",
        )
        self.assertFalse(
            (self.workspace / "agent.txt").exists(),
            "inner working tree should be gone after clear",
        )
        self.assertTrue(
            (self.project_dir / "agent.txt").exists(),
            "outer working tree should STILL have agent created file",
        )
        # Workspace dir survives (bind-mount target) but is empty — the
        # wipe ran from inside the container, agent UID, no chown to host.
        self.assertTrue(self.workspace.is_dir(), "workspace mount-point dir must remain")
        self.assertEqual(
            list(self.workspace.iterdir()),
            [],
            "after clear, inner workspace contents must be wiped (incl. .git)",
        )
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.workspace}")
        print(f"Alcatraz has following content after close: {cmd_output}")
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.project_dir}")
        print(f"Project has following content after close: {cmd_output}")

        # Phase 4 — switch to a different branch and start again. The
        # snapshot should re-pin to other-branch, NOT inherit feat/X.
        self._git("checkout", "main")
        self.assertFalse(
            (self.project_dir / "agent.txt").exists(),
            "outer working tree should have no agent created file (we are back on main/)",
        )
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.project_dir}")
        print(f"Project has following content at main branch: {cmd_output}")
        self._git("checkout", "-b", "other-branch")
        self._commit_in_project_repo("switch-flow: user commit on other-branch", "user.txt")
        self.assertTrue(
            (self.project_dir / "user.txt").exists(),
            "outer working tree should have user created file",
        )
        cmd_output = self._cmd_in_project_repo(f"ls -la {self.project_dir}")
        print(f"Project has following content before second start: {cmd_output}")
        rc = start_mod.cmd_start(self.project_dir, prison=self.prison)  # snapshot time
        self.assertEqual(rc, 0, "second cmd_start should succeed")
        self.assertEqual(
            self._pinned_branch(),
            "other-branch",
            "after clear + checkout + start, state.json.pinned_branch must "
            "track the NEW current branch - "
            f"Got: {self._pinned_branch()!r}",
        )

        # The new workspace's inner git is fresh snapshot — no git history, just initial commit,
        # but it should have all the files from the project repo when snapshot was done.
        # user.txt created post-branch-switch & pre-snapshot should be there.
        self.assertTrue(
            (self.workspace / "user.txt").exists(),
            "pre-snapshot created project files must go into the fresh post-clear workspace",
        )
        # the agent.txt from the previous workspace is gone due to switching branch.
        self.assertFalse(
            (self.workspace / "agent.txt").exists(),
            "previous workspace's agent files must not survive into the fresh post-clear workspace",
        )
        cmd_output = self._cmd_in_workspace("ls -la /workspace")
        print(f"Alcatraz has following content after second start: {cmd_output}")


if __name__ == "__main__":
    unittest.main()
