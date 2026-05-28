"""End-to-end integration tests for the start/clear lifecycle flows.

The headline promise (TestSwitchBranchFlow) is the switch-branch loop:

    git checkout -b feat/X
    alcatrazer start                     # workspace pinned to feat/X
    # ... agents work ...
    alcatrazer clear                     # terminal teardown: workspace
                                         # wiped, pin dropped
    git checkout -b other-branch
    alcatrazer start                     # fresh workspace pinned to
                                         # other-branch

These are integration tests in the strictest sense — no mocks at the prison
layer, no host-side simulation of the wipe. The container actually runs,
`find /workspace -mindepth 1 -delete` actually runs inside as the agent UID,
and the bind-mount actually propagates the empty state to the host. That's
the only way to verify the wipe-from-inside contract (Principle 2: agent
never observes host UID).

Fixture + the git/files/daemon step vocabulary live once in
`_promotion_flow.PromotionFlowTest`; each scenario below subclasses it and
gets its own isolated container (setUpClass runs once per class). Lives in
`integration_tests/` next to `test_smoke.py` but is NOT a smoke test (smoke
covers security invariants + tooling availability); both are gated behind
Docker and opt out of the default `alcatrazer test` run via location.

Requires Docker; run with `mise test-smoke`.
"""

import unittest

from alcatrazer.integration_tests._promotion_flow import PromotionFlowTest


class TestSwitchBranchFlow(PromotionFlowTest):
    """Phase 9 Step 9.3 — alcatrazer clear + start re-pins to the
    user's currently-checked-out branch.

    Single test method by design (mirrors TestAlcatrazSmokeLifecycle):
    the flow is stateful, and each step is a named action or
    observation, so a failure's call site names which step of the
    user-flow broke."""

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


# ══════════════════════════════════════════════════════════════════════
# Empty RED placeholders for the start/clear lifecycle flows the feature
# doc promises but only mocks cover today. Each class subclasses
# PromotionFlowTest (own isolated container) and frames one real-container
# user-flow; the body is intentionally absent (self.fail) until we
# implement them one by one. See docs/features/change_promotion_machinery.md.
#
# The prose method names exceed ruff's line-length; E501 is ignored for
# integration_tests/** (see pyproject) because the test name IS the spec.
# ══════════════════════════════════════════════════════════════════════


class TestBranchLifecycleAcrossMerge(PromotionFlowTest):
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
        # Given: the user starts Alcatraz on feat/X, the agent commits a
        # file, the daemon promotes it onto feat/X.
        self._given_alcatrazer_started_on_branch("feat/X")
        agent_subject = "merge-loop: agent adds feature.py on feat/X"
        self._agent_commits(agent_subject, "feature.py")
        self._assert_daemon_synced_to_outer(agent_subject)
        self._assert_outer_has("feature.py")

        # When: the user clears (terminal teardown — workspace wiped, pin
        # dropped, but the promoted commit stays on outer's feat/X).
        self._alcatrazer_clears()
        self._assert_pin_is_dropped()
        self._assert_workspace_is_wiped()
        self._assert_outer_has("feature.py")

        # When: the user merges feat/X into main (the PR-merge step), so
        # main now carries the agent's work as part of its history.
        self._user_returns_to_branch("main")
        self._user_merges_branch_into_current("feat/X")
        self._assert_outer_has("feature.py")

        # When: the user cuts a new branch off main and starts again.
        self._user_creates_branch("feat/Y")
        self._alcatrazer_starts()

        # Then: the fresh workspace pins to feat/Y AND its snapshot
        # contains the previously-promoted agent file (inherited via
        # main) — promoted work survives the PR-merge and re-seeds the
        # next workspace as its baseline. This is the complement of
        # TestSwitchBranchFlow, which asserts the inverse case (branch
        # cut BEFORE the merge → agent work absent from the new
        # workspace).
        self._assert_workspace_pinned_to("feat/Y")
        self._assert_workspace_has("feature.py")


class TestFinalSyncDrainOnClear(PromotionFlowTest):
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
        # Given: feat/X branched off AND the daemon configured to poll very
        # rarely, so agent commits made just before `clear` stay PENDING —
        # the daemon doesn't get a chance to promote them on the regular
        # poll loop, so the only way they reach outer is via the final-
        # sync step inside `clear`'s shutdown_sync_daemon call.
        self._user_creates_branch("feat/X")
        self._slow_daemon_poll_to(seconds=300)
        self._alcatrazer_starts()
        commits_before = self._outer_commit_count("feat/X")

        # When: the agent makes several commits inside the workspace.
        pending_subjects = [
            "final-sync drain: pending commit 1 of 3",
            "final-sync drain: pending commit 2 of 3",
            "final-sync drain: pending commit 3 of 3",
        ]
        for i, subject in enumerate(pending_subjects, start=1):
            self._agent_commits(subject, f"pending-{i}.txt")

        # When: clear runs on feat/X with all three commits pending.
        self._alcatrazer_clears()

        # Then: the clear's stop → shutdown_sync_daemon step drained ALL
        # pending commits via the daemon's final-sync handler. The
        # daemon's `Final sync (graceful shutdown): N commit(s) synced`
        # log line records the drain count — locks the claim that it was
        # the final sync (not a stray regular poll) that did the work
        # (which would surface as final-sync count = 0). Workspace
        # wiped, pin dropped, outer's feat/X retains the agent's work —
        # no commits lost.
        self._assert_daemon_final_sync_drained(len(pending_subjects))
        self._assert_pin_is_dropped()
        self._assert_workspace_is_wiped()
        self._assert_outer_commit_count_is("feat/X", commits_before + len(pending_subjects))
        for i in range(1, len(pending_subjects) + 1):
            self._assert_outer_has(f"pending-{i}.txt")


class TestStopRestartPreservesPin(PromotionFlowTest):
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


class TestRestartKeepsPinWhenBranchSwitchedWhileStopped(PromotionFlowTest):
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


class TestClearBlockedOffPin(PromotionFlowTest):
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


class TestClearBlockedWhilePaused(PromotionFlowTest):
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


class TestClearDiscardsPending(PromotionFlowTest):
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


class TestStartRefusesDetachedHead(PromotionFlowTest):
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
