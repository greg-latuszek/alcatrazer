"""End-to-end integration tests for the promotion daemon's behaviour as
the outer repo changes underneath it: holding when the user leaves the
pinned branch, pausing on a working-tree conflict, and collaborating with
the user's own concurrent edits and commits.

These drive a real `DockerPrison` + real container + the real polling
daemon against a real outer git repo — no mocks at the prison layer. They
verify the user-visible promises in the "Held state and auto-resume" and
"Conflict semantics" sections of
docs/features/change_promotion_machinery.md.

Distinct purpose from test_switch_branch_flow.py (start/clear lifecycle)
and test_smoke.py (security invariants + tooling availability), so it lives
in its own file per the three-tier integration-test discipline. Fixture +
the git/files/daemon step vocabulary live once in
`_promotion_flow.PromotionFlowTest`; each scenario below subclasses it and
gets its own isolated container. Gated behind Docker; run with
`mise test-smoke`.

Empty RED placeholders. Each class frames one real-container flow; the body
is intentionally absent (self.fail) until we implement them one by one. The
prose method names exceed ruff's line-length; E501 is ignored for
integration_tests/** (see pyproject) because the test name IS the spec.
"""

import unittest

from alcatrazer.integration_tests._promotion_flow import PromotionFlowTest

# ── Baseline: a quiet outer repo ───────────────────────────────────────


class TestBaselinePromotion(PromotionFlowTest):
    """The simplest happy path — outer left untouched, an agent commit lands
    on the pinned branch. The control the held / paused / collaboration cases
    below are contrasted against."""

    def test_the_daemon_lands_an_agent_commit_when_user_makes_no_commit_in_outer_repo(self):
        """Given outer on `feat/X`, alcatrazer start (pins feat/X), and the
        outer repo left untouched (the user makes no commit and no edit); When
        an agent commits a new file inside the workspace; Then the daemon
        promotes it onto feat/X as a fast-forward — the file appears in outer's
        working tree, feat/X advances by exactly one commit authored as the
        user, and `git status` is clean. Nothing in outer competes with the
        patch.

        Baseline (not strictly a gap): test_smoke.py's full_lifecycle already
        covers this end-to-end; kept here as the control case for this file's
        scenario matrix, so the held / paused / collaboration variants read as
        deviations from a named baseline."""
        # Given: the user starts Alcatraz on feat/X, then leaves outer alone.
        self._given_alcatrazer_started_on_branch("feat/X")
        commits_before = self._outer_commit_count("feat/X")

        # When: an agent commits a new file inside the workspace.
        self._agent_commits("baseline: agent commits a feature file", "feature.txt")

        # Then: the daemon lands exactly that commit on feat/X as the user,
        # the working tree in lockstep with the advanced ref.
        self._assert_daemon_synced_to_outer("baseline: agent commits a feature file")
        self._assert_outer_has("feature.txt")
        self._assert_outer_commit_count_is("feat/X", commits_before + 1)
        self._assert_outer_tip_authored_and_committed_by(
            "feat/X", "Ghost Agent", "ghost@example.com"
        )
        self._assert_outer_working_tree_is_clean()


# ── Held state and auto-resume (off-pin / deleted / detached) ──────────


class TestHeldOffPinAutoResume(PromotionFlowTest):
    """The daemon holds while the user is off the pinned branch and replays
    everything that piled up the moment they return."""

    def test_the_daemon_holds_promotion_then_replays_all_piled_commits_in_one_batch_when_the_user_leaves_and_returns_to_the_pinned_branch(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start (pins feat/X); When the
        user `git checkout main`, agents make several commits inside while the
        daemon holds (no promotion, pending count grows), then the user
        `git checkout feat/X`; Then the next poll replays ALL piled commits in
        one `git am`, feat/X advances by all of them, and the log shows exactly
        one Held → one Resumed transition.

        Fold-in: while held, assert `alcatrazer status` reports "on hold" with
        the correct pending count (the held status surface, checked mid-flow
        rather than in a separate container).

        Coverage gap: only the `promote_once` unit `resumes_after_recheckout`
        test and daemon log-parsing exist; never exercised against a real
        polling daemon + real branch switch."""
        # Given: the user starts Alcatraz on feat/X (daemon running, on-pin).
        self._given_alcatrazer_started_on_branch("feat/X")
        commits_before = self._outer_commit_count("feat/X")

        # When: the user switches to main; the next poll holds promotion.
        self._user_returns_to_branch("main")
        self._assert_daemon_holds()

        # ... and several agent commits pile up inside while held.
        piled_subjects = [
            "hold flow: agent commit 1 of 3",
            "hold flow: agent commit 2 of 3",
            "hold flow: agent commit 3 of 3",
        ]
        for i, subject in enumerate(piled_subjects, start=1):
            self._agent_commits(subject, f"piled-{i}.txt")

        # While held: none reach outer; status reports on-hold + pending count.
        self._assert_outer_commit_count_is("feat/X", commits_before)
        self._assert_status_reports_on_hold_with_pending(len(piled_subjects))

        # When the user returns to feat/X, the next poll replays ALL piled
        # commits in one batch (arrival of the LAST in outer implies all
        # earlier ones landed too, since `git am` applies them in order).
        self._user_returns_to_branch("feat/X")
        self._assert_daemon_synced_to_outer(piled_subjects[-1])

        # Then: feat/X advanced by exactly N commits, every piled file is
        # present, and the log shows exactly one Held → one Resumed
        # transition (no thrashing under sustained hold).
        self._assert_outer_commit_count_is("feat/X", commits_before + len(piled_subjects))
        for i in range(1, len(piled_subjects) + 1):
            self._assert_outer_has(f"piled-{i}.txt")
        self._assert_daemon_log_records_one_held_resumed_cycle()


class TestHeldOnDeletedPin(PromotionFlowTest):
    """The daemon holds when the pinned branch is deleted and resumes once
    the user recreates it (a rename folds in — delete + create)."""

    def test_the_daemon_holds_promotion_when_the_pinned_branch_is_deleted_and_resumes_once_the_user_recreates_it(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, agent commits pending;
        When the user deletes feat/X (`git checkout main && git branch -D
        feat/X`) so the daemon holds (PIN_DELETED), then recreates feat/X and
        checks it out; Then the daemon resumes and promotes the pending commits
        onto the recreated feat/X.

        Coverage gap: only the `check_pin` unit `returns_PIN_DELETED` test
        exists; the recreate-and-resume loop is unproven end-to-end."""
        # Given: the user starts Alcatraz on feat/X (daemon running, on-pin).
        self._given_alcatrazer_started_on_branch("feat/X")
        commits_before = self._outer_commit_count("feat/X")

        # When: the user steps off feat/X and deletes it; the daemon holds.
        # (git refuses to delete the currently-checked-out branch, hence the
        # intermediate `git checkout main`.)
        self._user_returns_to_branch("main")
        self._user_deletes_branch("feat/X")
        self._assert_daemon_holds()

        # ... and several agent commits pile up inside while held.
        piled_subjects = [
            "deleted-pin: agent commit 1 of 3",
            "deleted-pin: agent commit 2 of 3",
            "deleted-pin: agent commit 3 of 3",
        ]
        for i, subject in enumerate(piled_subjects, start=1):
            self._agent_commits(subject, f"piled-{i}.txt")

        # When the user recreates feat/X (off main) and checks it out, the
        # next poll replays ALL piled commits in one batch onto the new feat/X.
        # The recreated branch points at main's tip — same content as the
        # original feat/X tip (the seed), so `git am` applies cleanly.
        self._user_creates_branch("feat/X")
        self._assert_daemon_synced_to_outer(piled_subjects[-1])

        # Then: the recreated feat/X advanced by exactly N commits, every
        # piled file is present, and the log shows exactly one Held → one
        # Resumed transition (no thrashing across the off-pin / deleted /
        # recreated state changes; the daemon collapses HELD → HELD
        # transitions by design).
        self._assert_outer_commit_count_is("feat/X", commits_before + len(piled_subjects))
        for i in range(1, len(piled_subjects) + 1):
            self._assert_outer_has(f"piled-{i}.txt")
        self._assert_daemon_log_records_one_held_resumed_cycle()


class TestHeldOnDetachedHead(PromotionFlowTest):
    """The daemon holds on a detached HEAD and resumes once a branch is
    checked out again. (Optional — close to the off-pin hold; include only
    to exercise the DETACHED check_pin state live.)"""

    def test_the_daemon_holds_promotion_when_the_user_detaches_head_and_resumes_once_a_branch_is_checked_out(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, agent commits; When the
        user `git checkout <sha>` (detached) so the daemon holds (DETACHED),
        then `git checkout feat/X`; Then the daemon resumes and promotes.

        Coverage gap: only the `check_pin` unit `returns_DETACHED` test exists.
        Lower priority — nearly redundant with the off-pin hold flow."""
        # Given: the user starts Alcatraz on feat/X (daemon running, on-pin).
        self._given_alcatrazer_started_on_branch("feat/X")
        commits_before = self._outer_commit_count("feat/X")

        # When: the user detaches HEAD; the daemon holds (DETACHED).
        self._user_detaches_head()
        self._assert_daemon_holds()

        # ... and several agent commits pile up inside while held.
        piled_subjects = [
            "detached: agent commit 1 of 3",
            "detached: agent commit 2 of 3",
            "detached: agent commit 3 of 3",
        ]
        for i, subject in enumerate(piled_subjects, start=1):
            self._agent_commits(subject, f"piled-{i}.txt")

        # When the user reattaches HEAD by checking out feat/X, the next
        # poll replays ALL piled commits in one batch.
        self._user_returns_to_branch("feat/X")
        self._assert_daemon_synced_to_outer(piled_subjects[-1])

        # Then: feat/X advanced by exactly N commits, every piled file is
        # present, and the log shows exactly one Held → one Resumed
        # transition.
        self._assert_outer_commit_count_is("feat/X", commits_before + len(piled_subjects))
        for i in range(1, len(piled_subjects) + 1):
            self._assert_outer_has(f"piled-{i}.txt")
        self._assert_daemon_log_records_one_held_resumed_cycle()


# ── Conflict semantics and transparent collaboration ───────────────────


class TestPausedFileCollisionAutoResume(PromotionFlowTest):
    """The daemon pauses when an agent-added file collides with a same-named
    file the user created in outer, and auto-resumes once the user removes
    their file. The daemon `--abort`s on conflict, so its diff never surfaces
    in outer — meaning removal (not in-tree merge resolution) is the only fix."""

    def test_the_daemon_pauses_when_an_agent_added_file_collides_with_a_same_named_outer_file_then_resumes_once_the_user_removes_it(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, an agent commit that ADDS
        a new file `F` inside the workspace, and the user separately creating a
        file named `F` with DIFFERENT content in the outer repo; When the
        daemon tries to promote, `git am` cannot apply the add (path `F`
        already exists) → it runs `git am --abort` and pauses, leaving the
        commit pending; Then — because the abort makes the attempted change
        invisible and there are NO in-tree conflict markers to resolve — the
        user removes their `F` (this test deletes it; renaming it aside,
        stashing, or reverting to the snapshot-time content are equally valid
        and out of scope), after which the next cycle applies the agent's `F`
        and resumes. The log shows Paused → Resumed.

        Fold-in: while paused, assert `alcatrazer status` reports "paused" with
        the working-tree-conflict message.

        Coverage gap: only the `promote_once` unit `paused_on_conflict` test
        and daemon log-parsing exist; the real `am` abort + remove-to-resolve +
        auto-resume loop is unproven end-to-end. NB: resolution is file removal,
        NOT committing/merging the user's version — the daemon's diff never
        surfaces in outer (see project_promotion_conflict_semantics)."""
        self.fail("not yet implemented — see docstring")


class TestNonOverlappingEditsCoexist(PromotionFlowTest):
    """The "surprising-but-fine" case: an agent commit lands while the user
    has unrelated uncommitted edits, and both survive."""

    def test_the_daemon_lands_an_agent_commit_beside_the_users_uncommitted_edits_when_the_two_touch_different_files(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start, an agent commit to file
        `A`, and an outer uncommitted edit to a DIFFERENT file `B`; When the
        daemon promotes; Then `git am` SUCCEEDS — the agent commit lands on
        feat/X while the user's uncommitted edit to `B` stays in the working
        tree untouched (`git status` shows the new commit applied and `B` still
        dirty). This is the "transparent collaboration" the design promises.

        Coverage gap: Conflict-semantics case #1 (non-overlapping dirty tree)
        is untested anywhere."""
        self.fail("not yet implemented — see docstring")


class TestAgentCommitsStackOnUserCommits(PromotionFlowTest):
    """Outer moving ahead with the user's own commits is the expected case —
    agent patches stack on top as further fast-forwards."""

    def test_the_daemon_stacks_agent_commits_on_top_of_the_users_own_commits_when_the_user_commits_on_the_pinned_branch_between_syncs(
        self,
    ):
        """Given outer on `feat/X`, alcatrazer start (snapshot taken); When the
        user makes their OWN commit(s) on feat/X after the snapshot (outer
        moves ahead) and an agent also commits inside; Then the daemon applies
        the agent patches as fast-forwards ON TOP of the user's commits —
        feat/X reads O1 → user-commit(s) → agent-commit(s), with both
        preserved.

        Coverage gap: the doc calls this "the expected case, not an edge case",
        but it is unproven end-to-end; the `apply_patch_stream` preserve-history
        unit test never involves user commits made after the snapshot."""
        self.fail("not yet implemented — see docstring")


if __name__ == "__main__":
    unittest.main()
