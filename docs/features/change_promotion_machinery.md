# Re-implementing promotion: replay agent commits onto your working branch

## Status: Design — not implemented yet. Surfaced from manual testing of Phase 1 (more_languages_support) before merging the branch.

## Origin

The triggering observation, reported during Phase 1 release-readiness manual
tests: *"promoted commits are not visible in outer git. Most probably they
go into outer git but not into working tree."*

A discriminating test against `/tmp/promo-test/repo-py` (fresh outer repo,
one initial commit `2099891 outer initial`, then one agent commit inside
Alcatraz `agent: add AGENT_FILE`) showed something **worse** than
working-tree desync:

| Check                        | Result                                                                                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Outer `HEAD` after promotion | Moved from `2099891` → `fda81a6` (the daemon advanced `main`)                                                                                                |
| Outer working tree           | Shows `deleted: AGENT_FILE.md` (HEAD's tree contains it; working tree doesn't)                                                                               |
| `git reflog show main`       | `main@{2}` outer initial → `main@{1}` fast-import → `main@{0}` fast-import. Original commit no longer ancestor of `main` (`merge-base --is-ancestor` → 1)    |
| `git fsck`                   | Clean — no broken refs, but original outer commit only kept alive by reflog                                                                                  |

Today's mirror mode does three damaging things every cycle:

1. **Rewrites outer's branch history** — the workspace's foreign root
   commit replaces the outer's existing root.
2. **Fails to update the working tree** — refs move, files don't, so
   `git status` reports phantoms.
3. **Provides no surface for the user to recover** — the daemon log
   says only "Promotion cycle complete: main"; nothing tells the user
   what just happened or how to fix it.

The user's framing is correct: alcatrazer's vision is **transparent
promotion** — outer should look like the user is coding in it, agent
work appearing instantly as the user's own commits with the working
tree updated in lockstep. The current implementation cannot deliver
that, and patching it incrementally would just paper over a tool
mismatch.

## The user flow this needs to fit

How a developer normally works on a shared codebase:

1. Checkout `main` in outer, pull latest.
2. `git checkout -b feat/X` — branches off main to work on a feature
   without conflicting with co-workers.
3. Code, commit, code, commit on `feat/X`.
4. Push `feat/X` to remote, open a PR.
5. Co-workers review, approve.
6. Merge PR to `main`. Direct push to `main` is usually blocked by
   branch protection.

Alcatrazer should slot into this flow with zero new ceremony. The
model:

1. User checks out `feat/X` in outer (or any working branch).
2. User runs `alcatrazer start`. Workspace snapshots `feat/X`'s tree,
   pins itself to `feat/X`.
3. Agents work inside the workspace. They may create their own
   internal branches (e.g. `agent-frontend`, `agent-backend`) as a
   coordination mechanism for parallel work — these are
   **agent-private** and alcatrazer never reads them. The visible
   signal of "work ready" is commits landing on the inner workspace's
   `main`.
4. Daemon replays new commits from inner-`main` onto outer's `feat/X`
   via `git am` — atomically updating the ref and the working tree.
5. User reviews the resulting `git log feat/X`, pushes for PR review.

Inner-`main` is the **single source ref** for promotion. Outer's
currently-checked-out branch (pinned at start) is the **single
target**. No multi-branch matrix, no namespace refs, no auto-creation
logic.

## What today's pipeline gets wrong

`src/alcatrazer/promote.py` lines 193–230 implement the mirror path as

```
git fast-export <refs>  |  rewrite_identity (regex sub)  |  git fast-import --force
```

`fast-export` / `fast-import` is the canonical pair for **transferring
a history graph between repos that should look identical** — mirroring,
backups, dump-and-restore, format migrations. That is a different
problem from what alcatrazer needs. The pair has three properties that
are exactly wrong here:

1. **It transfers reachability, not deltas.** `fast-export refs/heads/main`
   emits every commit reachable from main, including the workspace's
   foreign root. `fast-import` then sets `refs/heads/main` to the tip
   of the imported history. The outer's pre-existing root falls off
   not because we asked it to, but because nothing in the imported
   stream references it as a parent.
2. **It only updates refs.** `fast-import` writes objects and refs;
   it never touches the working tree. There is no built-in
   "update HEAD's tree on disk" step — we'd have to layer a
   `checkout-index` afterwards, with all the safety considerations
   (dirty tree? conflicting edits? in-flight rebase?) that a real
   working-tree-mutation needs.
3. **It has no abort.** A failed import leaves outer in whatever
   intermediate state the stream got to. There is no
   `git fast-import --abort`.

The foreign root exists by design: the workspace is a snapshot, not a
clone (Principle 2). `snapshot.py:146-153` does `git init`, extracts
the outer's tree via `tar`, then commits with the workspace's
identity. The workspace has its own root with no parent — agents
running `git log` must not see outer's commits, identities, or
internal references. Inner and outer share *tree content* at the
snapshot point, but cannot share *commit SHAs* (commit hashes depend
on parent + author + committer + timestamps + message — five of those
six fields necessarily differ).

The right abstraction is therefore **commit replay**, not history
transfer: take new commits from inside (commits *after* the workspace
root) and reapply them as new commits onto outer's
currently-checked-out branch, authored as the user.

## The right machinery: `git format-patch | git am`

`git am` was designed for exactly this scenario — Linus Torvalds
applying patches mailed in by kernel contributors onto his tree.
The shape maps onto alcatrazer perfectly:

| Linux kernel scenario | Alcatrazer scenario |
| --------------------- | ------------------- |
| Contributor commits in their own tree | Agent commits inside Alcatraz |
| Patch mailed via mbox | Patch piped daemon-side |
| Linus applies via `git am` onto `master` | Outer applies via `git am` onto user's working branch |
| Author kept from `From:` header | Author rewritten to user's identity (in-flight) |
| Committer = caller's `user.email` (Linus) | Committer = outer's `user.email` (the user) |
| Working tree updated as part of `am` | Working tree updated as part of `am` |
| Failed patch: `git am --abort` rolls back | Same |

### Why this fits

- **`am` is a fast-forward by construction.** It writes patch hunks
  to the working tree, stages them, creates a commit on the current
  branch with the patch's message and a re-derived author. There is
  no possible state where the ref advances but the working tree
  doesn't — they update together or not at all.
- **Patches carry no SHAs.** A patch is content + message + author;
  applying it produces a brand-new commit whose SHA depends on
  outer's current HEAD. So outer's existing history is never
  overwritten — it can only be appended to.
- **Identity rewrite is one regex.** The `From:` line in each patch
  is plain text. Rewriting it before `am` reads from stdin gives
  us authorship; `am` then uses outer's git config for the
  committer. Both end up as the user.
- **Conflict failure is legible.** `am` surfaces `Patch does not
  apply` cleanly, with `--abort` to roll back. The failure mode
  becomes "a teammate's PR didn't merge" — a thing every git user
  already understands.
- **Object isolation is preserved.** No `fetch`, no `cherry-pick`,
  no shared `objects/` via `alternates`. The inner workspace's
  history stays inside the workspace. Only patch text crosses the
  boundary.

### What gets piped

```
inner_root := <recorded once at workspace creation, persisted in
                .alcatrazer/inner-root — see "Recording state">

# Each cycle (single source ref → outer's pinned branch):
git -C $inner format-patch                                    \
    --stdout --binary --keep-subject --first-parent           \
    ${last_promoted_or_inner_root}..refs/heads/main           \
  | rewrite_from_header(name, email)                          \
  | git -C $outer am                                          \
    --committer-date-is-author-date                           \
    --keep-non-patch                                          \
    --whitespace=nowarn                                       \
    --empty=drop
```

- `${last_promoted_or_inner_root}..refs/heads/main` excludes the
  workspace root (and any already-promoted commits) from the stream,
  so outer's existing history stays intact.
- `--first-parent` walks inner-`main`'s mainline only, ignoring
  agent-private side-branch topology. See "Inner-main linearity".
- `--binary` keeps non-text content applicable.
- `--keep-subject` prevents `format-patch` from re-prefixing
  subjects with `[PATCH]`.
- `rewrite_from_header()` substitutes the `From:` line in each
  mbox entry. Operates in bytes (patches contain binary diffs).
  Same idea as today's `rewrite_identity()` (`promote.py:124`);
  different regex (mbox header instead of fast-export
  `author`/`committer` lines).
- `--committer-date-is-author-date` keeps the committer timestamp
  identical to the author timestamp, so reflogs and `git log` show
  one chronology, not two.
- `--empty=drop` silently skips patches whose changes are already
  present in outer (e.g., the user manually applied the same edit on
  `feat/X` while agents were working). Matches the "transparent
  collaboration" framing — if user and agent converged on the same
  change, just move on. Real conflicts (overlapping but different
  edits) still fail and pause as designed.

### How this removes the bugs by construction

| Today's bug | Why `am` cannot exhibit it |
| ----------- | -------------------------- |
| Outer's branch history rewritten | `am` only appends to HEAD; patches have no SHA targets |
| Working tree out of sync with HEAD | `am` writes files + index + ref atomically |
| Original outer commit replaced by inner root | `inner_root` is excluded from the format-patch range |
| `git status` shows phantom "deleted" entries | Working tree IS the patch target — status is clean afterwards |
| No abort path on failure | `git am --abort` |

## Pin-at-start contract

At `alcatrazer start`, the daemon records outer's current branch name
as the **pinned branch** in `.alcatrazer/pinned-branch`. This is a
one-time write — the pin lives for the workspace's lifetime.

"Pinned branch" is internal vocabulary (file name, function names,
this design doc). User-facing copy never says "pinned" — it says
*"the branch alcatrazer started from"*. The reasoning is that
end-users shouldn't have to learn a new term to understand a
constraint that follows naturally from how alcatrazer works:
*Alcatraz started its internal repo from your branch `feat/X`, and
can only promote commits back to that same branch.*

Invariants the daemon enforces every cycle:

1. Outer's HEAD must be a branch (not detached).
2. The branch name must equal the pinned name.
3. The branch must exist (not deleted).

If any invariant fails, the daemon **holds** — see "Held state and
auto-resume" below. It does *not* abort, retry on a different branch,
auto-recreate, or modify outer in any way.

At start time, two preconditions are checked before the workspace is
created:

- **Outer is on a branch** (not detached). If detached: refuse with
  *"alcatrazer requires outer to be on a branch — `git checkout
  <branch>` first."*
- **Outer's working tree is clean enough to snapshot.** (Existing
  behavior; unchanged.)

## Held state and auto-resume

"On hold" is **passive**, not a failure. The daemon keeps running,
polls keep firing, but when the pin invariants don't hold each cycle
short-circuits:

- `format-patch` is *not* run.
- `am` is *not* run.
- Inner work keeps accumulating on inner-`main`.

The hold auto-clears the moment outer's HEAD matches the pin again.
No CLI command, no user intervention beyond the natural `git checkout
feat/X`. On the next poll, the daemon replays *all* held commits in
one `am` invocation — `am` reads the full mbox, so 3 piled-up patches
process the same as 1.

Log discipline: emit one line on the **transition** held → resumed
(and reverse), not every poll. The daemon tracks `last_logged_status`
to suppress duplicates.

Failure modes that all map to "hold". User-facing messages avoid
internal vocabulary ("pinned", "invariant"); they explain the
constraint in plain terms:

| Outer state           | User-facing message |
| --------------------- | ------------------- |
| On the start branch   | (active — no message) |
| On a different branch | *"Promotion on hold. Alcatraz started from `feat/X` and can only promote commits back to `feat/X`. You're currently on `main` — run `git checkout feat/X` to resume."* |
| Detached HEAD         | *"Promotion on hold. Alcatraz started from `feat/X` and can only promote commits back to `feat/X`. You're in detached HEAD — run `git checkout feat/X` to resume."* |
| Start branch deleted  | *"Promotion on hold. Alcatraz started from `feat/X` and can only promote commits back to `feat/X`, but `feat/X` no longer exists. Recreate it or run `alcatrazer clear --discard-pending` to abandon the agent work."* |
| Start branch renamed  | Same as deleted (a rename is a delete + create from the daemon's view). |

The unified user contract for every hold state: **either restore
outer's state, or explicitly `alcatrazer clear --discard-pending` to
drop the agent work.** The daemon never decides to abandon work on
the user's behalf.

## User-visible surfaces

### `alcatrazer status`

Three example outputs covering the three steady states:

```
Daemon running (PID 12345)
  Started from:     feat/X  ✓ active
  Pending commits:  0
  Last promotion:   2 minutes ago
```

```
Daemon running (PID 12345)
  Started from:     feat/X  ⚠ on hold
                    Alcatraz can only promote commits back to `feat/X`.
                    You're currently on `main` — run `git checkout feat/X`
                    to resume.
  Pending commits:  3
  Last promotion:   23 minutes ago
```

```
Daemon running (PID 12345)
  Started from:     feat/X  ⚠ paused
                    Your working tree on `feat/X` overlaps with an agent
                    commit. Commit or stash your changes and the daemon
                    will resume.
  Pending commits:  1
  Last promotion:   never
```

Pending commit count is `git -C inner rev-list --count last_promoted..main` — cheap, exact, and the visible reassurance that held work is still safe.

### `alcatrazer clear`

The command's contract is "stop the daemon, run a final promotion
sync, tear down the workspace." With pin-at-start, four cases:

- Outer matches pin, no pending commits → proceed silently.
- Outer matches pin, has pending commits → final sync drains them
  onto the pinned branch, proceed.
- Outer is off-pin / detached / pin missing, no pending commits →
  proceed silently.
- Outer is off the start branch / detached / start branch missing,
  **has pending commits** → **block** with:

  ```
  alcatrazer: cannot clear — 3 agent commits are waiting to be promoted
  to `feat/X`, but you're currently on `main`.

  Alcatraz started from `feat/X` and can only promote commits back to
  that branch.

    To keep the agent work:    git checkout feat/X && alcatrazer clear
    To discard pending work:   alcatrazer clear --discard-pending
  ```

Default-deny is the right call — losing N hours of agent work because
the user `git checkout main`'d to inspect something and ran clear from
muscle memory is a real failure mode. The flag makes destructive
intent explicit.

### Daemon log

Adds these per-event entries (transitions only, never per-poll).
Diagnostic logs are still readable to humans, so they avoid the
"pinned" jargon too:

- `Held: outer on <current>, alcatraz started from <start>`
- `Resumed: outer back on <start>, replaying N commits`
- `Promoted N commit(s) to <start>`
- `Paused: working-tree conflict on <start>`
- `Resumed: working-tree conflict resolved`

## Conflict semantics

Two distinct paused states, both clean:

1. **Outer working tree has uncommitted edits, no overlap with
   patch.** `am` succeeds; user's edits remain in the working tree
   alongside the new commit. This is the surprising-but-fine case —
   the user sees an agent commit appear under their cursor. That is
   exactly the "transparent collaboration" the design promises.
2. **Outer working tree has uncommitted edits that overlap with
   patch.** `am` fails. Daemon catches non-zero exit, runs
   `git -C outer am --abort`, marks the workspace paused, logs
   *"Paused: working-tree conflict on `feat/X`. Commit or stash and
   the daemon resumes."* Auto-resumes on the next cycle when the
   overlap is gone.

Outer having commits the workspace doesn't know about (the user
committing on `feat/X` between snapshot and promotion) is **the
expected case**, not an edge case. With `am`, patches simply land on
top of the user's commits as further fast-forwards — `am` doesn't
care that HEAD moved, it just patches against current HEAD. User
commits are preserved, agent commits land as additional fast-forwards
above them.

## Inner-main linearity

`format-patch --first-parent` follows inner-`main`'s mainline only.
If agents create internal branches and merge them in:

- **Fast-forward merge:** trivially linear, every commit appears in
  the stream.
- **Squash merge:** appears as a single commit on inner-`main`, gets
  one patch.
- **Real merge commit:** `--first-parent` follows the mainline
  parent; the side-branch commits don't appear individually. Outer
  sees the merge commit's tree as a single patch.

**Agents are not informed of this.** A core principle is that agents
inside the workspace must believe they're in a vanilla repo — they
don't know outer exists, don't know about promotion, don't know
about this constraint. So we don't add a CLAUDE.md instruction
saying "keep inner-`main` linear". `--first-parent` accepts whatever
topology agents produce on inner-`main` and surfaces it to outer as
a sequence of patches. If a future-merge produces an undesirable
patch (e.g. a giant merge-of-many-files), that's a workspace-level
conversation about how agents coordinate, not a promotion concern.

## Recording state

All new fields land in the existing `.alcatrazer/state.json`. The
`state` module (`src/alcatrazer/state.py`) is already documented as
the seed of the infocenter layer, with additive-merge semantics and
atomic writes — exactly what we need. No new files; one source of
truth for `alcatrazer status` to read.

Fields added:

- `inner_root` — SHA of the workspace's `Initial commit`.
  Format-patch range boundary. Written once by `snapshot.py` at
  workspace creation; never updated.
- `pinned_branch` — outer's HEAD branch name at `alcatrazer start`.
  Written once by `snapshot.py` (since the snapshot itself is what
  defines what the workspace was built from); never updated.
- `last_promoted` — SHA of the last successfully promoted inner-`main`
  commit. Updated by the daemon after each successful `am`. Replaces
  the per-branch `promoted-tips.json` (which goes away).
- `paused` — `{ "reason": "<message>" } | null`. Updated by the
  daemon when entering/leaving the working-tree-conflict state.
  Replaces `paused-branches.json` (which goes away).

`state.SCHEMA_VERSION` bumps `1 → 2` in the same change. See
"Breaking-change posture" below.

## Breaking-change posture

v0.0.5 changes both the `coding-environment.toml` schema (removes
`[promotion-daemon].mode` and `[promotion-daemon].branches`) and the
`.alcatrazer/state.json` layout. A clean automatic migration would
require: detecting old layouts, asking permission, possibly draining
an old daemon — too much code for a feature that has zero external
users today (pre-1.0.0).

Decision: **no automatic migration.** Instead:

- Bump `coding-environment.toml`'s `schema_version` field to `2`.
- Bump `state.SCHEMA_VERSION` to `2`.
- `alcatrazer init` / `start` / `status` detect a `schema_version`
  mismatch (or absent field, or legacy `[promotion-daemon].mode`
  key) and refuse with a transparent message:

  ```
  alcatrazer: this directory was set up by an older version (schema 1).
  v0.0.5 reworks promotion and is not backwards compatible.

  To upgrade:
    1. If a daemon is running: alcatrazer stop      (using v0.0.4)
    2. rm -rf .alcatrazer/ coding-environment.toml
    3. alcatrazer init                              (using v0.0.5)
    4. alcatrazer start

  See CHANGELOG for what changed and why.
  ```

- CHANGELOG entry calls out the breaking change explicitly and
  re-states the four steps.

Pre-1.0.0, this is the right tradeoff: explicit user action over
silent migration code that nobody benefits from.

## What this proposal replaces

- `promote.promote()` (mirror code path, `promote.py:193-232`).
- `promote.promote_with_conflict_handling()` (`promote.py:334+`) —
  replaced by a new `promote_once()` with the same daemon entrypoint
  but a single source/target shape.
- `resolve_branches()` and the per-branch fnmatch logic — single
  source ref, no resolution needed.
- The marks-file pair `promote-export-marks` /
  `promote-import-marks` — no longer used. (Mirror mode never reads
  them in the new path; alcatraz-tree mode is being retired entirely.)
- `find_conflict_branches()`, `check_resolved_conflicts()`,
  `detect_diverged_branches()`, `_promote_single_branch()`,
  `rewrite_refs()` — all unused after the rewrite.
- `promoted-tips.json` and `paused-branches.json` files entirely —
  their contents fold into `state.json` as `last_promoted` and
  `paused` fields. Cleaner home, single read for `alcatrazer status`.
- **`alcatraz-tree` mode entirely.** The new model makes mirror mode
  actually work AND naturally provides what alcatraz-tree was trying
  to: every agent commit is visible in `git log feat/X` with the
  user's identity, and gets reviewed via the normal PR flow when the
  user pushes. The parallel-namespace ref no longer earns its
  complexity. Removes: the `[promotion-daemon].mode` config option,
  the `namespace=` parameter on `promote()`, `rewrite_refs()`, the
  `alcatraz-tree` branch in `daemon.run_cycle()`, and the
  `conflict/resolve-*` branch creation flow.
- The `[promotion-daemon].branches` config option — single source is
  always inner-`main`.

## What this proposal leaves alone

- **Snapshot, identity, and isolation primitives.** Workspace creation
  still produces a flat snapshot with a fresh root, agents still see a
  vanilla repo with the random human identity. Only difference: the
  snapshot extracts from outer's *currently checked-out branch*'s tree,
  not hardcoded `main`.
- **Daemon polling, lifecycle, PID file, shutdown semantics.** Same
  poll loop. Only the inner promote-call swaps and the held-state
  branch is added.
- **Identity priority chain** (`promote.py:96-115`) — git config →
  `[promotion]` toml → CLI flags.

## Concrete code touches

- `src/alcatrazer/snapshot.py`
  - `create_initial_commit()` returns the new commit SHA.
  - `snapshot_workspace()` reads outer's *current branch name* (not
    hardcoded `main`), extracts that branch's tree, then calls
    `state.update_state(alcatraz_dir, inner_root=<sha>,
    pinned_branch=<name>)`. Both fields land in the same `state.json`
    write.
  - Detached-HEAD precondition check lives in `start.py` (it's about
    outer's runtime state, not the snapshot mechanics), but
    `snapshot.py` is the writer of the resulting state fields.

- `src/alcatrazer/promote.py`
  - New `rewrite_from_header(stream: bytes, name: str, email: str) -> bytes`.
    Substitutes the `From:` line in each mbox entry. Bytes-only.
  - New `format_patch_stream(source: Path, since_sha: str) -> bytes`.
    Wraps `git format-patch --stdout --binary --keep-subject
    --first-parent <since>..refs/heads/main`. Empty bytes when
    nothing to promote.
  - New `apply_patch_stream(target: Path, stream: bytes, name: str, email: str) -> None`.
    Runs `git -C target am --committer-date-is-author-date
    --keep-non-patch --whitespace=nowarn --empty=drop` with stream on
    stdin and `GIT_AUTHOR_*` / `GIT_COMMITTER_*` set in env. Raises
    a typed `PromotionConflictError` on non-zero exit, after running
    `git am --abort`.
  - New `check_pin(target: Path, pinned_branch: str) -> PinStatus`.
    Returns one of `OK`, `OFF_PIN`, `DETACHED`, `PIN_DELETED`. Used
    by daemon to decide held vs. active.
  - New `promote_once(source, target, alcatraz_dir, name, email) -> PromotionResult`.
    Single-cycle entry. Reads `inner_root`, `pinned_branch`,
    `last_promoted` from `state.load_state(alcatraz_dir)`. Writes
    updated `last_promoted` and `paused` fields back via
    `state.update_state()`. Returns `{status, commit_count,
    new_promoted_tip}`.
  - Deletes: `promote()`, `promote_with_conflict_handling()`,
    `resolve_branches()`, `rewrite_refs()`, `find_conflict_branches()`,
    `check_resolved_conflicts()`, `detect_diverged_branches()`,
    `_promote_single_branch()`, `load_promoted_tips()`,
    `save_promoted_tips()`, `load_paused_branches()`,
    `save_paused_branches()`. Marks-file handling, separate state
    files, and namespace logic all go.

- `src/alcatrazer/daemon.py`
  - `run_cycle()` calls `check_pin()` then `promote_once()` (which
    pulls its inputs from `state.json`). Logs held/resumed/paused/
    promoted on state transition only.
  - Drops the `if mode == "mirror" / elif mode == "alcatraz-tree"`
    branching; one path.
  - Final-sync on shutdown obeys the same pin invariants. If outer
    is off-pin during graceful shutdown, the final sync is held just
    like a regular cycle — and the resulting pending commits surface
    to `alcatrazer clear` (see `start.py`).

- `src/alcatrazer/start.py`
  - `cmd_start`: refuse to start if outer is detached. Post-success
    message clarifies promotion semantics: *"Alcatraz started from
    `feat/X`. Agent commits will land on `feat/X` as your own (working
    tree updates automatically). Switching branches puts promotion on
    hold until you return to `feat/X`."*
  - `cmd_status`: surfaces the three states above (active / held /
    paused) with pending-commit count and the recovery hint.
  - `cmd_clear`: implements the four-case logic above, including
    `--discard-pending` flag.

- `src/alcatrazer/config.py` (or wherever the schema lives)
  - Removes `[promotion-daemon].mode` and `[promotion-daemon].branches`
    from the schema definition.
  - Bumps `coding-environment.toml`'s `schema_version` to `2`.
  - `init` / `start` / `status` refuse on `schema_version < 2` (or
    absent, or with legacy keys present) with the upgrade message
    described in "Breaking-change posture".

- `src/alcatrazer/state.py`
  - Bumps `SCHEMA_VERSION` to `2`.
  - No API changes. The new fields (`inner_root`, `pinned_branch`,
    `last_promoted`, `paused`) merge in via the existing
    `update_state()` semantics.
  - `load_state()` callers (in `start.py`, `daemon.py`,
    `cmd_status`, `cmd_clear`) refuse on `schema_version < 2` the
    same way `coding-environment.toml` does.

- Tests
  - `tests/test_promote.py`
    - `test_format_patch_stream_excludes_inner_root` — workspace with
      `inner_root` + N agent commits → stream has exactly N patches.
    - `test_rewrite_from_header_substitutes_author_in_mbox` — feeds
      a real mbox with a Patricia author, expects Alice in output.
      Operates on bytes, preserves binary hunks.
    - `test_apply_patch_stream_advances_branch_and_updates_worktree`
      — outer with one commit, apply patches for an "agent commit"
      adding a file → outer's branch has 2 commits, working tree
      contains the file, `git status` clean.
    - `test_apply_patch_stream_preserves_outer_history` — outer
      with one commit `O1`, apply N patches → branch is
      `O1 → A1 → … → AN`, `O1` still ancestor of HEAD.
      **Direct regression test for the manual-test bug.**
    - `test_apply_patch_stream_identity_is_user_for_author_and_committer`
      — both fields on each new commit equal the configured user.
      Inner identity (Patricia) appears nowhere in outer.
    - `test_apply_patch_stream_aborts_cleanly_on_conflict` — pre-seed
      outer with a conflicting edit → raises
      `PromotionConflictError`, outer's HEAD and tree byte-identical
      to pre-call state.
    - `test_check_pin_ok`, `test_check_pin_off_pin`,
      `test_check_pin_detached`, `test_check_pin_deleted`.
    - `test_promote_once_holds_when_off_pin` — no `am` invocation,
      no error, returns `held` status.
    - `test_promote_once_resumes_after_recheckout` — held state
      accumulates, recheckout pinned → next call promotes all piled
      commits in one `am`.
    - `test_promote_once_first_parent_flattens_inner_merges` —
      inner has a merge commit on `main`; stream contains one patch
      for it, side-branch commits absent.
  - `tests/test_snapshot.py`
    - `test_snapshot_records_inner_root` — `.alcatrazer/inner-root`
      equals `git rev-parse HEAD` of the workspace's Initial commit.
    - `test_snapshot_records_pinned_branch` — `.alcatrazer/pinned-branch`
      equals outer's current branch name.
    - `test_snapshot_uses_outer_current_branch_tree` — outer on
      `feat/X` with files differing from `main`; workspace tree
      matches `feat/X`'s tree, not `main`'s.
    - `test_snapshot_refuses_detached_outer` — outer in detached
      HEAD → start fails with explanatory message.
  - `tests/test_daemon.py`
    - End-to-end: outer on `feat/X`, agent commits, daemon promotes
      onto `feat/X`, working tree clean, original outer commit still
      ancestor. **Closes the manual-test bug under automation.**
    - Held-state: outer switches to `main` mid-cycle, daemon holds,
      switches back, daemon replays piled commits.
    - Conflict: pre-seed overlap, daemon pauses, user resolves,
      daemon resumes.
  - `tests/test_clear.py`
    - `test_clear_blocks_with_pending_off_pin`
    - `test_clear_discards_with_flag`
    - `test_clear_proceeds_on_pin_with_pending` — final sync drains
      onto pinned branch.
  - **Deleted entirely:** all existing tests for alcatraz-tree mode,
    marks-file behavior, multi-branch promotion, `conflict/resolve-*`
    branches. The mode and its tests retire together.

- Documentation
  - `docs/design_principles.md` — Promotion section's "Daemon Watches
    from Outside" rewritten: *"The daemon replays new agent commits
    onto your working branch via `git format-patch | git am`.
    Pin-at-start ties promotion to the branch you started on;
    switching branches puts promotion on hold until you return."*
  - `README.md` — "Promotion" subsection rewritten for the new flow:
    branch off main, alcatrazer start, agents commit, your branch
    grows, push for PR.
  - `CHANGELOG.md` — call out the breaking change: *"v0.0.5
    reworks promotion. **Not backwards compatible.** Stop any
    running v0.0.4 daemon (`alcatrazer stop` with v0.0.4 installed),
    delete `.alcatrazer/` and `coding-environment.toml`, then re-run
    `alcatrazer init` and `alcatrazer start` with v0.0.5. See [link
    to design doc] for what changed and why."*

## What this proposal does NOT do

- **Does not change the snapshot model.** Workspace remains a flat
  snapshot with a fresh root and a random human identity. All of
  Principle 2 stands.
- **Does not change the daemon's polling or lifecycle.** Same poll
  loop, same PID file, same shutdown semantics. Only the inner
  promote-call swaps and the held-state branch is added.
- **Does not preserve `alcatraz-tree` mode.** The new mirror mode
  subsumes its use case; dual-mode adds maintenance burden for
  negative value.
- **Does not promote any inner branches other than `main`.** Inner
  side-branches are agent-private. (A configurable `ready_ref` could
  be added later if there's reason to make this not-`main`. Out of
  scope.)
- **Does not preserve inner side-branch topology in outer.**
  `--first-parent` flattens. If a future feature needs the full
  topology, that's a separate design.
- **Does not auto-stash outer's uncommitted changes on conflict.**
  Pause + surface; don't silently mutate the user's working tree.
- **Does not change the `[promotion]` identity config schema.**
  Identity still resolved through the existing priority chain.
- **Does not introduce a new CLI subcommand.** Behavior surfaces
  through `alcatrazer status` (extended) and `alcatrazer clear`
  (extended with `--discard-pending`).
- **Does not provide automatic migration from v0.0.4.** Schema
  bump to v2 + refusal-with-helpful-message is the upgrade path
  (see "Breaking-change posture"). Pre-1.0.0, this is the right
  tradeoff: explicit user action over silent migration code that
  serves zero current users.

## Recommended order

This is one logically-coherent change; no value in splitting
RED/GREEN/BLUE across multiple PRs.

1. **RED:** the manual-test regression as an automated test —
   outer on `feat/X`, one agent commit, assert outer's `feat/X`
   advances, original commit still ancestor, working tree clean,
   identity is the user. Will fail on current `main`.
2. **RED+:** unit tests for `rewrite_from_header`,
   `format_patch_stream`, `apply_patch_stream`, `check_pin`,
   `promote_once` covering identity, range, conflict-abort, and
   held-state behaviors.
3. **RED++:** integration tests for held-state auto-resume,
   deleted-pin handling, `clear --discard-pending`.
4. **GREEN:** implement the new functions in `promote.py`. Wire
   `daemon.py`'s single path. Record `inner-root` and
   `pinned-branch` from `snapshot.py`. Delete alcatraz-tree code,
   marks-file handling, multi-branch logic.
5. **GREEN+:** extend `cmd_start`, `cmd_status`, `cmd_clear` for the
   new surfaces and `--discard-pending` flag.
6. **BLUE:** `README.md` + `design_principles.md` rewrites; CHANGELOG
   entry calling out the upgrade path.

Estimated total: ~6–8 hours including tests and docs (more than the
prior estimate because alcatraz-tree retirement is bundled in).

## Open questions

None blocking. All design questions identified during iteration are
settled:

- `git am --empty` behavior → `--empty=drop`.
- State storage → fold into existing `state.json` via the `state`
  module; no new files; bump `SCHEMA_VERSION` to 2.
- Detached-HEAD precondition placement → `start.py` runs the check;
  `snapshot.py` writes the resulting state fields.
- Migration policy → no automatic migration; bump
  `coding-environment.toml` schema to 2, refuse with helpful upgrade
  message; explicit pre-1.0.0 breaking change in CHANGELOG.
- CLAUDE.md merge-policy instruction → no instruction; agents must
  not be aware of Alcatraz's machinery (see "Inner-main linearity").
- Multi-agent races on inner-`main` → out of scope; workspace-level
  agent-orchestration concern, not a promotion concern.