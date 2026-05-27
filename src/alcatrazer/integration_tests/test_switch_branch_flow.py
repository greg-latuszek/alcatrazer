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


# ══════════════════════════════════════════════════════════════════════
# Phase-9 follow-up — empty RED placeholders for the start/clear lifecycle
# flows the feature doc promises but only mocks cover today. Each class
# frames one real-container user-flow; the body is intentionally absent
# (self.fail) until we implement them one by one. See
# docs/features/change_promotion_machinery.md.
#
# The prose method names below currently exceed ruff's line-length (E501);
# left as-is to land the names first — lint handling (noqa / per-file
# limit / shorter names) decided separately.
# ══════════════════════════════════════════════════════════════════════


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestBranchLifecycleAcrossMerge(unittest.TestCase):
    """The realistic feature loop: branch off main, agents commit, promote,
    clear, merge the branch into main, then start fresh on the next branch.

    Complements TestSwitchBranchFlow, which asserts the *inverse* — a branch
    cut from main *before* the merge does NOT carry the agent work."""

    def test_alcatrazer_carries_the_merged_agent_work_into_the_fresh_workspace_when_started_on_a_branch_cut_from_main_after_the_pinned_branch_merged(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start (pins feat/X), an agent
        commit promoted onto feat/X, then `clear`; When feat/X merges into
        `main` and the user cuts `feat/Y` off main and starts again; Then the
        fresh workspace pins to feat/Y AND contains the promoted agent file
        (inherited via main) — proving promoted work survives the PR-merge and
        re-seeds the next workspace as its baseline.

        Coverage gap: no end-to-end proof of the promote → merge → re-snapshot
        round-trip. The existing switch test checks the inverse case only."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestFinalSyncDrainOnClear(unittest.TestCase):
    """`clear` on the pinned branch with commits still pending must drain
    them in the final sync before teardown — not lose them."""

    def test_alcatrazer_drains_unpromoted_agent_commits_in_a_final_sync_before_teardown_when_cleared_on_the_pinned_branch(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, and agent commits that
        are still PENDING (not yet promoted — e.g. clear is invoked before the
        daemon's next poll); When `clear` runs on feat/X; Then the stop →
        final-sync step drains the pending commits onto feat/X BEFORE the wipe,
        so outer ends with the agent work; the workspace is wiped and the pin
        dropped.

        Coverage gap: the existing switch test WAITS for the daemon to promote,
        then clears (nothing pending at clear time), so the final-sync drain
        path is exercised only by the mocked cmd_clear `on_pin_with_pending`
        test. Implementer note: getting commits to still be pending at clear
        time is a real race — may require a slow poll interval or stopping the
        daemon first."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestStopRestartPreservesPin(unittest.TestCase):
    """`stop` + `start` is a freeze-restart that keeps the SAME pin and
    workspace — the explicit contrast to `clear` + `start` (new pin).
    This is the same-branch case; the branch-switch-while-stopped variant
    is TestRestartKeepsPinWhenBranchSwitchedWhileStopped below."""

    def test_alcatrazer_keeps_the_pin_and_the_workspace_intact_when_stopped_and_restarted_instead_of_cleared(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start (pins feat/X), an agent
        commit promoted, and the user STAYS on feat/X throughout; When `stop`
        then `start` (NOT clear); Then the pin is still feat/X, the inner
        workspace is NOT wiped (no re-snapshot), the inner history/files
        survive the restart, and the daemon resumes ACTIVE (on-pin).

        Coverage gap: the doc explicitly distinguishes stop/start (freeze-
        restart, same pin) from clear/start (fresh, new pin); only the
        clear/start half is proven end-to-end today."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestRestartKeepsPinWhenBranchSwitchedWhileStopped(unittest.TestCase):
    """The dangerous-looking case made safe: switching the outer branch while
    stopped must NOT silently re-pin on restart — `start` keeps the original
    pin and the daemon simply holds until the user returns. Only `clear` +
    `start` re-pins to the current branch."""

    def test_alcatrazer_keeps_the_original_pin_and_holds_promotion_when_restarted_after_the_user_switched_branches_while_stopped(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start (pins feat/X), then `stop`
        (container frozen; workspace + state.json incl. pinned_branch preserved,
        NOT wiped); When the user `git checkout main` while stopped and then
        `start` again; Then `start` takes the subsequent-run (resume) path — it
        does NOT re-snapshot or re-pin — so the pin stays `feat/X` and the
        workspace still mirrors feat/X's tree; because outer is now off-pin the
        daemon HOLDS rather than applying feat/X's agent work onto `main`.
        `git checkout feat/X` resumes promotion.

        Verified against start.py: the detached-head guard does not fire (main
        is a branch), routing enters _subsequent_run (workspace .git present +
        image current), and subsequent-run never rewrites pinned_branch
        (snapshot.py writes it once at workspace creation). The post-start
        message still names `feat/X`.

        Fold-in / planned scenario-aware message (see the TODO under start.py's
        post-start block): when implemented, `start` should DETECT at the top of
        cmd_start that outer's current branch ('main') differs from the pinned
        branch ('feat/X') and print a scenario-aware notice instead of the
        generic "syncing pauses until you return" line — telling the user (a)
        agents are starting in HOLD mode so nothing will be promoted because
        they switched branch while Alcatraz was frozen after `stop`, and (b) to
        instead collaborate on 'main' the re-pin path is `git checkout feat/X &&
        alcatrazer clear && git checkout main && alcatrazer start`. This test
        should assert that notice (and that `alcatrazer status` reads on-hold
        while outer is on main). Until that lands, the generic message ships and
        the behavioural assertions below (pin unchanged + held) are what hold.

        Coverage gap: no test (mocked or otherwise) covers restart-after-
        branch-switch-while-stopped — the case where a naive implementation
        might silently re-pin to `main` and replay feat/X-based patches onto
        it. This is the guard that stop/start ≠ clear/start even across a
        branch switch."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestClearBlockedOffPin(unittest.TestCase):
    """`clear` must refuse — and preserve the workspace — when agent work is
    pending but the user has wandered off the pinned branch."""

    def test_alcatrazer_refuses_to_clear_and_preserves_the_workspace_when_pending_agent_commits_exist_but_the_user_is_off_the_pinned_branch(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, agent commits that pile
        up as PENDING after the user `git checkout main` (daemon holds); When
        `clear` runs while on main with pending commits; Then clear returns
        nonzero, the block message names the pending count and `feat/X`, and
        the workspace + pin are preserved so the work stays recoverable.

        Coverage gap: only the mocked cmd_clear `blocks_when_off_pin_with_
        pending_commits` test exists; this proves the guard end-to-end against
        a real held daemon and real pending commits."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestClearBlockedWhilePaused(unittest.TestCase):
    """`clear` must refuse when promotion is paused by a working-tree
    conflict, even though the user IS on the pinned branch."""

    def test_alcatrazer_refuses_to_clear_while_promotion_is_paused_by_a_working_tree_conflict_even_on_the_pinned_branch(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, an uncommitted outer edit
        that OVERLAPS a file the agent also commits, so the daemon's `git am`
        fails → aborts → pauses with a pending commit; When `clear` runs while
        still on feat/X; Then clear returns nonzero, the message explains the
        paused conflict, and the workspace is preserved.

        Coverage gap: only the mocked cmd_clear `blocks_when_paused_even_
        though_on_pinned_branch` test exists; this drives a real `am` conflict,
        real pause, and clear refusal."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestClearDiscardsPending(unittest.TestCase):
    """`clear --discard-pending` is the explicit escape hatch: tear down even
    though pending agent work would otherwise block."""

    def test_alcatrazer_tears_down_the_workspace_and_discards_pending_agent_commits_when_cleared_with_discard_pending_off_the_pinned_branch(
        self,
    ):
        """Given the same off-pin-with-pending state as TestClearBlockedOffPin;
        When `clear` runs with discard_pending=True; Then clear PROCEEDS — the
        workspace is wiped, the pin dropped, and the pending agent work is
        intentionally discarded (outer feat/X unchanged).

        Coverage gap: the override counterpart that proves the off-pin block is
        escapable; only mocked (`discard_pending_flag_proceeds_through_
        teardown`) today. Drive via cmd_start.cmd_clear(..., discard_pending=
        True)."""
        self.fail("not yet implemented — see docstring")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestStartRefusesDetachedHead(unittest.TestCase):
    """`start` must refuse on a detached HEAD before building anything — the
    pin-at-start contract requires outer to be on a branch."""

    def test_alcatrazer_refuses_to_start_and_creates_no_workspace_when_the_outer_repo_has_a_detached_head(
        self,
    ):
        """Given a fresh init'd project whose outer repo is in detached HEAD
        (`git checkout <sha>`); When `start` runs; Then it returns nonzero with
        the explanatory "requires outer to be on a branch — git checkout
        <branch> first" message, and NO container/workspace is created and no
        pin is written.

        Coverage gap: only the mocked start `refuses_with_explanatory_message_
        on_detached_head` test exists; this proves the precondition halts the
        real flow before any build/snapshot."""
        self.fail("not yet implemented — see docstring")


if __name__ == "__main__":
    unittest.main()
