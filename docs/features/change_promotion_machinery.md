# Re-implementing promotion: replay agent commits onto your working branch

## Status: Complete — shipped in v0.1.1 (2026-05-14). All nine phases landed. Implementation Notes at the bottom record the in-flight discoveries that diverged from the original design (Phase 7 retarget, Phase 9 chown-back-via-side-container).

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
Messages use git's vocabulary and name the actual branches — per
`docs/coding_conventions.md` "User-facing strings speak the user's
language", they avoid project jargon (`pin`, `pinned`, `promotion`,
`outer`, `inner`):

- `Held: your repository is on branch '<current>' but Alcatrazer was
  started on '<start>'. Switch back to '<start>' to resume.`
- `Held: your repository has a detached HEAD. Check out branch
  '<start>' to resume.` *(DETACHED variant)*
- `Held: branch '<start>' no longer exists in your repository.
  Recreate it (e.g. `` `git branch <start>` ``) to resume.`
  *(PIN_DELETED variant)*
- `Resumed: back on branch '<start>'. Applying N agent commit(s).`
- `Resumed: conflict on branch '<start>' resolved.`
- `Applied N agent commit(s) to branch '<start>'.`
- `Paused: your working tree on branch '<start>' overlaps with an
  agent commit. Commit or stash your changes and Alcatrazer will
  resume.`

The earlier draft of this section used `outer`, `pinned`, `Promoted`,
and `alcatraz started from` — internal jargon the end user doesn't
speak. Revised in Phase 4 BLUE to match the rule and improve clarity.

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
- `last_promotion_time` — ISO 8601 UTC timestamp of the last
  successful `am` (written in the same state update as
  `last_promoted`). Backs the *"Last promotion: 23 minutes ago"* line
  in `alcatrazer status`. Persists across hold periods — important so
  the user sees the *real* last-promoted time, not the time of the
  most recent skipped (held) cycle. Stays unset until the first
  successful promotion (status renders that as `never`).
- `paused` — `{ "reason": "<message>" } | null`. Updated by the
  daemon when entering/leaving the working-tree-conflict state.
  Replaces `paused-branches.json` (which goes away).

`state.SCHEMA_VERSION` bumps `1 → 2` in the same change. See
"Breaking-change posture" below.

## Breaking-change posture

v0.1.1 changes the layouts of two of Alcatrazer's three declared
schemas (single source of truth: `src/alcatrazer/schemas.json`,
governed by `docs/coding_conventions.md` "Schema changes must land in
schemas.json + CHANGELOG before release"):

- **`.alcatrazer/state.json`** — adds `inner_root`, `pinned_branch`,
  `last_promoted`, `last_promotion_time`, `paused` fields (folding in
  what v0.1.0 split across the side files `promoted-tips.json` and
  `paused-branches.json`). Bumps `schema_version: 1 → 2`.
- **`.alcatrazer/config.toml`** — removes `[promotion-daemon].mode` and
  `[promotion-daemon].branches`; adds a `schema_version` field (v0.1.0
  didn't have one). The unversioned v0.1.0 shape is recorded as v=1;
  v0.1.1 stamps `schema_version = 2`.
- **`coding-environment.toml`** — unchanged. Existing files keep
  `schema_version = 1` and are accepted by v0.1.1 verbatim.

A clean automatic migration would require: detecting old layouts,
asking permission, possibly draining an old daemon — too much code
for a feature that has zero external users today (pre-1.0.0).

Decision: **no automatic migration.** Instead:

- Append v=2 revision entries to `state` and `alcatrazer_config` in
  `schemas.json` (this is the bump — `state.SCHEMA_VERSION` and the
  new `start.ALCATRAZER_CONFIG_SCHEMA_VERSION` derive from these).
- `alcatrazer init` / `start` / `status` / `clear` invoke a single
  `state.require_compatible_workspace(alcatraz_dir)` gate that refuses
  on **any** of these signals (multi-signal because v0.1.0's
  `state.json` was lazily created — `init`+`start` alone never wrote
  it, so the schema-version check alone misses those workspaces):

  - `state.json` exists with `schema_version < 2`.
  - `.alcatrazer/config.toml` exists but lacks `schema_version`, OR
    has it `< 2`, OR still contains `[promotion-daemon].mode` /
    `[promotion-daemon].branches`.
  - Any legacy artifact present: `paused-branches.json`,
    `promoted-tips.json`, `promote-export-marks`,
    `promote-import-marks`.

  Refusal renders this transparent message:

  ```
  alcatrazer: this directory was set up by an older version (schema 1).
  v0.1.1 reworks promotion and is not backwards compatible.

  To upgrade from pre-v0.1.1:
    1. If a daemon is running: alcatrazer stop      (using your previous version)
    2. sudo rm -rf `cat .alcatrazer/workspace-dir`  (inner git repo for agents coding)
    3. rm -rf .alcatrazer/                          (your coding-environment.toml is preserved)
    4. alcatrazer init                              (using v0.1.1)
    5. alcatrazer start

  Step 2 needs `sudo` because pre-v0.1.1's `alcatrazer clear` left
  the container-owned inner workspace files on the host filesystem.
  From v0.1.1 onwards, `alcatrazer clear` wipes the inner workspace
  itself — this manual step is a one-time upgrade procedure, not a
  general fresh-start workflow.

  See CHANGELOG for what changed and why.
  ```

- CHANGELOG entry calls out the breaking change explicitly and
  re-states the upgrade steps. The `sudo` on step 2 is needed because
  pre-v0.1.1's `clear` didn't wipe the inner workspace — the
  container-owned files (agent UID inside, phantom UID on host)
  survived to be cleaned up manually by the host user. v0.1.1's
  `clear` handles the wipe automatically.

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
    `last_promoted` from `state.load_state(alcatraz_dir)`. On
    successful `am`, writes back `last_promoted`,
    `last_promotion_time` (ISO 8601 UTC), and clears `paused`.
    On working-tree conflict, writes `paused = {"reason": ...}` and
    leaves `last_promoted` / `last_promotion_time` untouched. Returns
    `{status, commit_count, new_promoted_tip}`.
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

- `src/alcatrazer/templates/alcatrazer-config.toml` +
  `src/alcatrazer/start.py` (config writer + reader)
  - Adds a `schema_version = 2` top-level line to the template.
    v0.1.0's `.alcatrazer/config.toml` had no `schema_version` field;
    v0.1.1 introduces it alongside the removals.
  - Defines `start.ALCATRAZER_CONFIG_SCHEMA_VERSION = schema.ALCATRAZER_CONFIG.current_version`
    (derived from `schemas.json` per the source-of-truth convention).
  - `init` / `start` / `status` / `clear` refuse on legacy config
    signals (no `schema_version`, OR `< 2`, OR legacy
    `[promotion-daemon].mode` / `.branches` keys still present) with
    the upgrade message described in "Breaking-change posture".

- `src/alcatrazer/schemas.json`
  - Appends v=2 entries to `state` and `alcatrazer_config` histories.
    No edits to `coding_env` (no field changes in v0.1.1).

- `src/alcatrazer/state.py`
  - `SCHEMA_VERSION` is already derived from
    `schema.STATE.current_version`; appending the v=2 entry to
    `schemas.json` is the bump.
  - No `update_state()` API changes. The new fields (`inner_root`,
    `pinned_branch`, `last_promoted`, `last_promotion_time`, `paused`)
    merge in via the existing semantics.
  - Adds `validate_schema_version(data: dict) -> None` (pure
    schema-version validator on a state.json dict) and
    `require_compatible_workspace(alcatraz_dir: Path) -> None`
    (workspace-level gate composing the validator, the config-schema
    check, and the legacy-artifact check). Both raise typed
    `UnsupportedStateSchemaVersionError` with the upgrade message
    sourced from `schemas.json` (release + summary fields).
  - `load_state()` callers (in `start.py`, `daemon.py`,
    `cmd_status`, `cmd_clear`) call `require_compatible_workspace`
    once at entry before reading state.

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
  - `CHANGELOG.md` — call out the breaking change: *"v0.1.1
    reworks promotion. **Not backwards compatible.** Stop any
    running pre-v0.1.1 daemon (`alcatrazer stop` with the previous
    version installed), delete `.alcatrazer/` and
    `coding-environment.toml`, then re-run `alcatrazer init` and
    `alcatrazer start` with v0.1.1. See [link to design doc] for
    what changed and why."*

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
- **Does not provide automatic migration from v0.0.4 or v0.1.0.**
  Schema bump to v2 + refusal-with-helpful-message is the upgrade
  path (see "Breaking-change posture"). Pre-1.0.0, this is the
  right tradeoff: explicit user action over silent migration code
  that serves zero current users.

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

---

## Detailed Implementation Plan

Each step is one commit, small enough for human review. Dependencies
flow top to bottom — each step may require previous steps to be in
place.

**TDD discipline:** each step follows the RED/GREEN/BLUE cycle where
possible:

- `[RED]` commit — failing test for the planned functionality
- `[GREEN]` commit — implementation that makes the test pass
- `[BLUE]` commit — improvements / cleanup if applicable

**Phase ordering principle:** *additive first, swap next, delete
last.* Phases 1–3 build new code alongside the existing mirror path
without breaking it. Phase 4 swaps the daemon to use the new code.
Phases 6 and 7 (alcatraz-tree retirement, schema bump) come last so
existing tests stay green throughout development and the schema bump
doesn't force constant test-fixture rewrites mid-flight.

### Phase 1: New state fields (additive, no breakage)

**Step 1.1** `[RED]` — Test `snapshot_workspace` records `inner_root`
in `state.json`. Use `state.load_state(alcatraz_dir).get("inner_root")`
in the assertion; expect SHA equals `git rev-parse HEAD` of the
workspace's `Initial commit`.

**Step 1.2** `[GREEN]` — `snapshot.py` calls
`state.update_state(alcatraz_dir, inner_root=<sha>)` after creating
the initial commit.

**Step 1.3** `[RED]` — Test `snapshot_workspace` records
`pinned_branch` in `state.json` (outer's current branch name).

**Step 1.4** `[GREEN]` — `snapshot.py` reads outer's HEAD branch,
calls `state.update_state(alcatraz_dir, pinned_branch=<name>)` in
the same `state.json` write as `inner_root`.

**Step 1.5** `[RED]` — Test that snapshot uses outer's *current
branch tree*, not a hardcoded `main` tree. Outer on `feat/X` with
files differing from `main`; assert workspace tree matches `feat/X`.

**Step 1.6** `[GREEN]` — `snapshot.py` extracts the tree of outer's
HEAD branch (not hardcoded `main`).

**Step 1.7** `[RED]` — Test `alcatrazer start` refuses on detached
HEAD with the explanatory message.

**Step 1.8** `[GREEN]` — `start.py` adds detached-HEAD precondition
check before invoking `snapshot.py`.

### Phase 2: Patch-stream primitives

**Step 2.1** `[RED]` — Test `rewrite_from_header` substitutes the
`From:` line in a real mbox stream (Patricia → Alice). Operates on
bytes, preserves binary hunks.

**Step 2.2** `[GREEN]` — Implement `rewrite_from_header(stream, name,
email) -> bytes` in `promote.py`.

**Step 2.3** `[RED]` — Test `format_patch_stream` excludes
`inner_root`. Workspace with `inner_root` + N agent commits → stream
has exactly N patches.

**Step 2.4** `[GREEN]` — Implement `format_patch_stream(source,
since_sha) -> bytes` wrapping `git format-patch --stdout --binary
--keep-subject --first-parent <since>..refs/heads/main`.

**Step 2.5** `[RED]` — Test `apply_patch_stream` advances target
branch and updates working tree. Outer with one commit, apply
patches for an agent commit adding a file → outer's branch has 2
commits, file present in working tree, `git status` clean.

**Step 2.6** `[RED]` — Test `apply_patch_stream` preserves outer
history: outer with `O1`, apply N patches → branch is `O1 → A1 →
… → AN`, `O1` still ancestor of HEAD. **Direct regression test for
the manual-test bug.**

**Step 2.7** `[RED]` — Test `apply_patch_stream` rewrites both
author and committer to the configured user. Inner identity
(Patricia) appears nowhere in outer.

**Step 2.8** `[RED]` — Test `apply_patch_stream` aborts cleanly on
conflict. Pre-seed outer with conflicting edit → raises
`PromotionConflictError`, outer's HEAD and tree byte-identical to
pre-call state.

**Step 2.9** `[RED]` — Test `apply_patch_stream` drops empty
patches. Outer already has the same change → `am` exits 0 (via
`--empty=drop`), no commit added, no error.

**Step 2.10** `[GREEN]` — Implement `apply_patch_stream(target,
stream, name, email)` running `git am --committer-date-is-author-date
--keep-non-patch --whitespace=nowarn --empty=drop` with identity in
env. Raises `PromotionConflictError` (new exception type) on
non-zero exit after `git am --abort`.

### Phase 3: Pin checking and orchestration

**Step 3.1** `[RED]` — Tests for `check_pin` covering all four
states: `OK`, `OFF_PIN`, `DETACHED`, `PIN_DELETED`.

**Step 3.2** `[GREEN]` — Implement `check_pin(target, pinned_branch)
-> PinStatus`.

**Step 3.3** `[RED]` — Test `promote_once` active path: reads state,
applies patches, writes back `last_promoted` + `last_promotion_time`,
clears `paused`.

**Step 3.4** `[RED]` — Test `promote_once` held when off-pin: no
`am` call, no error, returns `held` status, state unchanged.

**Step 3.5** `[RED]` — Test `promote_once` resumes after recheckout:
held state accumulates inner commits; recheckout pin → next call
applies all piled commits in one `am`.

**Step 3.6** `[RED]` — Test `promote_once` handles inner merge
commits cleanly: inner has a merge commit on `main` that brings in
2 side-branch commits. Stream contains exactly **2 patches** (one
per non-merge side commit); the merge commit itself does not
become a patch.

> **Revised from the original spec.** The original draft said
> "`--first-parent` flattens inner merges: stream contains one
> patch for it." Empirically that's not what `git format-patch`
> does — it does NOT emit merge commits as patches under any flag
> combination tested (`--first-parent`, `-m`, `--merges`,
> `--diff-merges=first-parent`, `--cc`, `-c`). format-patch is
> fundamentally designed for non-merge commits.
>
> The revised design (drop `--first-parent` entirely; let side
> commits flow through as individual patches) is also product-
> better: parallel-agent workflows (e.g. 10 agents on separate
> branches that later merge to `main`) would otherwise produce
> one giant squashed patch per merge — unreviewable. Alcatrazer's
> "developer reviews before push" promise depends on the patches
> being atomic and readable.

**Step 3.7** `[RED]` — Test `promote_once` writes `paused` on
conflict; leaves `last_promoted` and `last_promotion_time` untouched.

**Step 3.8** `[GREEN]` — Implement `promote_once(source, target,
alcatraz_dir, name, email) -> PromotionResult`.

### Phase 4: Daemon wiring (the swap)

**Step 4.1** `[RED]` — Daemon end-to-end test using the manual-test
shape: outer on `feat/X` with one initial commit, agent commit
inside, daemon promotes → outer's `feat/X` is 2 commits with
original as ancestor, working tree contains the agent file, status
clean. **Closes the manual-test bug under automation.**

**Step 4.2** `[RED]` — Daemon held-state integration test: outer
switches to `main` mid-cycle, daemon logs `Held` (once), agent
keeps committing inside, user switches back to `feat/X`, next cycle
replays piled commits, daemon logs `Resumed`.

**Step 4.3** `[RED]` — Daemon conflict integration test: pre-seed
outer with overlapping uncommitted edit, daemon attempts promotion,
catches `PromotionConflictError`, writes `paused` state, logs
`Paused`. User commits/stashes, daemon detects clean working tree
on next cycle, applies patches, logs `Resumed`.

**Step 4.4** `[GREEN]` — Wire `daemon.run_cycle` to call `check_pin`
+ `promote_once` on the mirror branch. Add transition-only
held/resumed/paused logging via `last_logged_status`. Mirror branch
now uses the new path; alcatraz-tree branch still present and
untouched.

### Phase 5: CLI surface extensions

**Step 5.1** `[RED]` — `cmd_status` test: active state output
matches the spec (Started from / ✓ active / pending / last
promotion).

**Step 5.2** `[RED]` — `cmd_status` test: held-state output with
plain-language *"Alcatraz can only promote commits back to `feat/X`"*
message and pending count.

**Step 5.3** `[RED]` — `cmd_status` test: paused-state output with
working-tree-conflict message.

**Step 5.4** `[GREEN]` — Implement `cmd_status` extended output
reading from `state.load_state()`. Single read; no new files.

**Step 5.5** `[RED]` — `cmd_start` test: post-success message
content (*"Alcatraz started from `feat/X`. … switching branches
puts promotion on hold …"*).

**Step 5.6** `[GREEN]` — Implement `cmd_start` post-success message.

**Step 5.7** `[RED]` — `cmd_clear` test: blocks with the exact
upgrade-message text when pending commits exist and outer is off-pin.

**Step 5.8** `[RED]` — `cmd_clear` test: `--discard-pending` flag
proceeds, drops pending agent work, tears down workspace.

**Step 5.9** `[RED]` — `cmd_clear` test: proceeds on pin with
pending commits — final sync drains onto the pinned branch first,
then tears down.

**Step 5.10** `[GREEN]` — Implement `cmd_clear` four-case logic +
`--discard-pending` flag.

### Phase 6: Retire alcatraz-tree mode

Only safe to do *after* Phase 4 has the daemon running on the new
mirror path with full test coverage. Until this phase, alcatraz-tree
mode and its tests remain green as a control.

**Step 6.1** `[GREEN]` — Delete unused functions from `promote.py`:
`promote()`, `promote_with_conflict_handling()`, `resolve_branches()`,
`rewrite_refs()`, `find_conflict_branches()`,
`check_resolved_conflicts()`, `detect_diverged_branches()`,
`_promote_single_branch()`, `load_promoted_tips()`,
`save_promoted_tips()`, `load_paused_branches()`,
`save_paused_branches()`. Marks-file handling, namespace logic, and
separate JSON-state file handling all go.

**Step 6.2** `[GREEN]` — Delete the `alcatraz-tree` branch in
`daemon.run_cycle()`. Single path remaining.

**Step 6.3** `[GREEN]` — Remove `mode` and `branches` from the
`coding-environment.toml` schema definition (schema upgrade comes in
Phase 7; this just deletes the keys from the parser).

**Step 6.4** `[GREEN]` — Delete obsolete tests: alcatraz-tree mode,
marks-files behavior, multi-branch promotion, `conflict/resolve-*`
branches. Verify the full test suite still green.

### Phase 7: Schema bump and refusal

Phase 7 refuses v0.1.0 workspaces via a single
`state.require_compatible_workspace(alcatraz_dir)` gate that fires on
any of three signal classes (`state.json` schema, `.alcatrazer/config.toml`
schema or legacy keys, legacy artifact files). The multi-signal approach
catches workspaces that v0.1.0 never wrote `state.json` for. Each refusal
caller (`daemon._run_cycle_mirror`, `cmd_start`, `cmd_status`, `cmd_clear`)
invokes the gate once before reading state.

The phase also lands the infrastructure that makes future schema bumps
trivial — `src/alcatrazer/schemas.json` (single source of truth),
`src/alcatrazer/schema.py` (loader), and the
`docs/coding_conventions.md` rule that ties schema edits to CHANGELOG
mentions. That infrastructure is landed in pre-7.1 commits
(`c0acf58`, `3c2ce09`, `8985dfa`).

**Step 7.1** `[RED]` (committed as `f5c8001`) — Unit tests for
`state.validate_schema_version(data: dict)`, the pure schema-version
validator: silent on empty / current; raises
`UnsupportedStateSchemaVersionError` on old / future / stamp-absent
with the four-step upgrade message.

**Step 7.2** `[RED]` — Tests for
`state.require_compatible_workspace(alcatraz_dir)`, the workspace-level
gate. One test per signal so failure attribution stays clean:
- `state.json` with `schema_version < 2` → raise.
- `state.json` missing → silent (fresh workspace).
- legacy artifact present (`paused-branches.json`,
  `promoted-tips.json`, `promote-export-marks`,
  `promote-import-marks`) → raise, message names which artifact tripped.

**Step 7.3** `[GREEN]` — Implement `UnsupportedStateSchemaVersionError`
+ `validate_schema_version` + `require_compatible_workspace`. Append v=2
entry to `state` history in `schemas.json` (the bump —
`state.SCHEMA_VERSION` follows via derivation). Wire
`require_compatible_workspace` into `daemon._run_cycle_mirror`,
`cmd_start`, `cmd_status`, `cmd_clear`.

**Step 7.4** `[RED]` — Tests for `.alcatrazer/config.toml` legacy
refusal as a new signal in `require_compatible_workspace`:
- Config file lacks `schema_version` → raise (any v0.1.0 config trips this).
- `schema_version < 2` → raise.
- `[promotion-daemon].mode` or `[promotion-daemon].branches` key still
  present → raise (defence-in-depth: catches a hand-edited config that
  was bumped to v=2 but kept the obsolete keys).

**Step 7.5** `[GREEN]` — Add `schema_version = 2` line to
`templates/alcatrazer-config.toml`. Append v=2 entry to
`alcatrazer_config` history in `schemas.json`. Define
`start.ALCATRAZER_CONFIG_SCHEMA_VERSION = schema.ALCATRAZER_CONFIG.current_version`
(derived). Extend `require_compatible_workspace` with the config-side
checks.

### Phase 8: Documentation

> Step 8.2 (README rewrite) depends on Phase 9 having landed first —
> the "switch branch via `clear + start`" flow that the new README
> describes only becomes true behavior after Phase 9 extends
> `cmd_clear` to wipe the inner workspace and unpin. Phases 8.1, 8.3,
> 8.4 don't depend on Phase 9 and can land in either order.

**Step 8.1** `[BLUE]` — Update `docs/design_principles.md` Promotion
section's "Daemon Watches from Outside" with the new one-liner.

**Step 8.2** `[BLUE]` — Rewrite README's "Promotion" subsection for
the new flow (branch off main → alcatrazer start → agents commit →
your branch grows → push for PR). Includes the
`clear + start` switch-branch description that Phase 9 makes accurate.

**Step 8.3** `[BLUE]` — Add CHANGELOG entry with the breaking-change
notice and the four cleanup steps.

**Step 8.4** `[BLUE]` — Mark this design doc as Status: Complete and
backfill any "Implementation Notes" subsection with decisions
resolved during implementation (mirroring the
`start_from_existing_repo.md` pattern).

### Phase 9: `clear` is terminal — wipe inner workspace + unpin

Phase 9 makes `alcatrazer clear` match its intent: a terminal teardown
that leaves the project in a state where the next `alcatrazer start`
is a fresh first-run on whatever branch the user is currently on.
Today's clear stops + removes the container and prints "workspace
preserved on the host"; the workspace dir + `state.json`'s
`pinned_branch` persist, so a subsequent `start` reuses the old pin
instead of re-snapshotting. That gap was caught by a manual test
during Phase 8 (see Implementation Notes).

The intended user-facing flow Phase 9 enables:

- `alcatrazer stop` + `alcatrazer start` — freeze-restart loop, **same
  pin**. For pausing the workspace temporarily.
- `alcatrazer clear` + `alcatrazer start` — terminal teardown +
  fresh start, **new pin to current branch**. The "switch branch"
  workflow.

Both paths already wait for the daemon to drain pending commits before
the daemon exits, so the commits are durably in outer before Phase 9
removes the inner workspace.

#### Removal mechanism: two one-shot side containers

Three candidate approaches considered for clearing the agent-UID-
owned workspace bind-mount:

- **chown-back from the original container** — original container
  chowns `/workspace` back to host's UID/GID before stop; host's
  plain `rm -rf` then works. Rejected: the agent observes its
  workspace ownership shift to a foreign UID before the chown
  completes — a fingerprint of the user the host runs as. Violates
  Principle 2.
- **`docker exec` on the original container** — would need
  `prison.resume()` first because `docker exec` requires a running
  container, and `cmd_clear` has already stopped it for daemon
  final-sync race-safety. Resume is safe per se (CMD is `sleep
  infinity`; startup commands are launched separately by
  `cmd_start`), but adds two extra state transitions on the
  agent's own container and invites the "does resume re-launch
  agents" question every reader will ask.
- **two one-shot side containers** (chosen):
  1. **wipe contents as agent** — `docker run --rm -u agent
     --entrypoint find -v <workspace>:/workspace <image>
     /workspace -mindepth 1 -delete`. Deletes everything inside the
     mount-point dir as the UID that owns the files.
  2. **chown the empty mount-point to host UID:GID as root** —
     `docker run --rm -u 0:0 --entrypoint chown -v
     <workspace>:/workspace <image> <host_uid>:<host_gid>
     /workspace`. Retags the dir so the next `alcatrazer start`'s
     host-side `git init` can write into it. Without this, the dir
     stays phantom-owned and blocks the next first-run with a
     permission error.

Why chown is safe in step 2 even though it was rejected as "chown-
back" earlier: Principle 2 gates ownership shifts an active agent
can observe. A one-shot side container has no agent process — the
chown is invisible to anything that could fingerprint the host
user. See memory entry "stealth-scope-is-observed-processes" for
the general rule.

Why our image rather than alpine (the pattern test_smoke uses for
phantom-UID cleanup): no extra image pull, and the `agent` user is
already defined in `/etc/passwd` of our image so `-u agent`
resolves consistently against the file ownership in step 1.

#### Ordering — keep "stop-first" race safety

The current `cmd_clear` order — stop → daemon-final-sync → remove —
guards against an agent committing AFTER the final sync but BEFORE
removal. The side container is a separate process that bind-mounts
the host path; it doesn't need the original container to be running,
so no resume step is required.

```
1. (pre-checks: pin status + pending count — unchanged)
2. prison.stop()                      # agents frozen
3. shutdown_sync_daemon()             # final sync drains pending
4. prison.wipe_workspace_contents()   # NEW — two one-shot side
                                      # containers in sequence:
                                      #   (a) find -mindepth 1
                                      #       -delete (as agent)
                                      #   (b) chown to host UID:GID
                                      #       (as root)
5. prison.remove()                    # original container gone
6. (alcatraz_dir / "state.json").unlink(missing_ok=True)  # unpin
```

The workspace directory itself stays (empty, same name) as the bind-
mount target for the next `start`, and after step 4(b) it's owned by
the host user so `git init` succeeds. `.alcatrazer/config.toml` is
preserved so identity + daemon settings carry over —
`alcatrazer init` is not required between `clear` and `start`.

#### Alcatraz port addition

New abstract method on `Alcatraz` (`src/alcatrazer/alcatraz.py`):

```python
@abstractmethod
def wipe_workspace_contents(self) -> None:
    """Remove every file inside the workspace bind-mount, leaving
    the mount-point directory itself in place.

    Caller contract: the original container is stopped when this is
    called. Backend chooses the removal mechanism (one-shot side
    container, etc.) so long as no agent process observes foreign
    UIDs or signals that betray the Alcatrazer machinery.
    """
```

`DockerPrison.wipe_workspace_contents` implements via two one-shot
side containers, in sequence:

```
# Step 1: wipe contents as agent.
docker run --rm -u agent --entrypoint find \
    -v <host_workspace>:/workspace <image_tag> \
    /workspace -mindepth 1 -delete

# Step 2: chown the now-empty mount-point to host UID:GID as root.
docker run --rm -u 0:0 --entrypoint chown \
    -v <host_workspace>:/workspace <image_tag> \
    <host_uid>:<host_gid> /workspace
```

Step 1 bypasses the chown-and-drop entrypoint (`--entrypoint find`)
and runs as the same UID the files are owned by (`-u agent`).
`-mindepth 1` preserves the mount-point dir itself; only its
contents are removed.

Step 2 retags the empty mount-point dir from the phantom UID back to
the host user's UID:GID so the next `alcatrazer start`'s host-side
`git init` can write into it. Runs as root (`-u 0:0`) so chown has
the necessary permission. Safe to perform here even though chown-
back is forbidden in the original container — the side container
has no agent process, so Principle 2 doesn't apply.

#### Detailed Implementation Plan

**Step 9.1** `[RED]` — Unit tests for the port-method contract:
- `Alcatraz.__abstractmethods__` declares `wipe_workspace_contents`.
- `DockerPrison.wipe_workspace_contents` invokes two side containers
  in sequence: first `docker run --rm -u agent --entrypoint find ...
  -mindepth 1 -delete` (wipe), then `docker run --rm -u 0:0
  --entrypoint chown ... <host_uid>:<host_gid> /workspace` (retag).
  Verified via mocked `docker_prison.subprocess.run` — the exact argv
  shape of both calls is locked so future backends inherit a clear
  spec.
- Returns `None` on success (state-mutating contract, matches
  `start`/`stop`/`remove`).
- Raises `PrisonError` when either side container returns non-zero
  (separate tests for the find-failed and chown-failed cases — both
  cause cmd_clear to abort cleanly rather than leave the workspace
  half-cleaned or wrong-owned), and also when
  `.alcatrazer/workspace-dir` is missing (no host path to mount).

**Step 9.2** `[RED]` — End-to-end integration test of the switch-
branch flow. Lives in `src/alcatrazer/integration_tests/` next to
`test_smoke.py` (different purpose: smoke covers security invariants
and tooling availability; this covers a user-flow). Drives the real
`alcatrazer init` / `start` / `clear` sequence against a real
`DockerPrison` + real container — no mocks at the prison layer, no
host-side simulation of the wipe.

Phases in a single stateful test method (line number on failure
points at which phase broke):

  1. Outer on `feat/X`, `cmd_start` → assert `state.json.pinned_branch`
     == `"feat/X"`.
  2. Make an inner commit via `prison.query` (docker exec), wait for
     the daemon to promote it to outer `feat/X` (the realistic
     "agents have done work" precondition for clear).
  3. `cmd_clear` → assert `state.json` is gone AND the workspace dir
     exists but is empty (mount point preserved, all contents
     including `.git/` removed by the wipe).
  4. `git checkout -b other-branch`, second `cmd_start` → assert
     `state.json.pinned_branch` == `"other-branch"` AND the previous
     workspace's files (e.g. `agent.txt`) are absent.

The choice not to write a parallel mocked unit-level test for
`cmd_clear`'s post-state (an earlier draft of the plan called this
out as Step 9.2): a `Mock(spec=Alcatraz)` whose
`wipe_workspace_contents` is fed a Python side-effect doesn't verify
that `find -mindepth 1 -delete` does what it promises inside a real
container as agent UID — it verifies the mock setup. The integration
test is the source of truth here; duplicating with mocks adds
maintenance burden with no incremental verification.

**Step 9.3** `[GREEN]` — Add `wipe_workspace_contents` to the
`Alcatraz` ABC, implement on `DockerPrison` via the one-shot side
container. Extend `cmd_clear` with the wipe → remove → unlink
sequence (slotting the wipe in between `shutdown_sync_daemon` and
`prison.remove`). Update `cmd_clear`'s post-success message to
reflect the new teardown (drop the "workspace preserved on the host"
line).

**Step 9.4** `[BLUE]` — Update the upgrade-refusal message in
`state._upgrade_message`: now that v0.1.1's `clear` handles teardown,
the manual `sudo rm -rf $(cat .alcatrazer/workspace-dir)` step is
only needed for users upgrading from v0.1.0 (whose `clear` doesn't
wipe). Clarify the wording so the user knows step 2 is a one-time
v0.1.0-upgrade step, not a general fresh-start procedure.

### Implementation Notes

**Phase 7 retarget (v0.1.1 implementation).** While preparing
Step 7.1's RED tests, two facts surfaced that the original Phase 7
prose got wrong:

- The doc attributed the `[promotion-daemon].mode` /
  `[promotion-daemon].branches` removal to `coding-environment.toml`,
  but those keys actually live in `.alcatrazer/config.toml` (verified
  against the installed v0.1.0 source). `coding-environment.toml` has
  no field changes between v0.1.0 and v0.1.1.
- `.alcatrazer/config.toml` had no `schema_version` field in v0.1.0,
  so a schema-version gate against it can't refuse legacy configs
  directly — v0.1.1 introduces the field alongside the removal.
- `.alcatrazer/state.json` was lazily created in v0.1.0 (only on the
  first `alcatrazer stop` / `clear`). A user who only ran `init` +
  `start` has no `state.json`, so a `state.json`-version gate alone
  misses those workspaces.

The revised plan keeps Step 7.1 unchanged (the pure schema-version
validator), adds a workspace-level `require_compatible_workspace`
gate that fires on multiple signals (state.json schema, config.toml
schema or legacy keys, presence of v0.1.0 side files), and retargets
the second half of the phase to `.alcatrazer/config.toml` instead of
`coding-environment.toml`. The `coding_env` schema gets no v=2 entry
in `schemas.json` for this release.

The new infrastructure landed before Step 7.1 RED: schema-history
JSON (`src/alcatrazer/schemas.json`), Python loader
(`src/alcatrazer/schema.py`), the
`docs/coding_conventions.md` rule "Schema changes must land in
schemas.json + CHANGELOG before release", and a cross-check test
suite that holds version constants and JSON entries in lockstep.

**Phase 9 emerged from a Phase 8 manual test.** While drafting the
README rewrite for Step 8.2, a draft paragraph claimed `alcatrazer
clear` followed by `git checkout` + `alcatrazer start` would
re-snapshot bound to the new branch. The user tested it: clear
preserves the workspace dir + `state.json` (`pinned_branch` carries
over), so the second start reuses the old pin and `alcatrazer status`
shows ⚠ on hold on the new branch. The two paths the design intended
to distinguish — `stop`/`start` for freeze-restart, `clear`/`start`
for fresh-on-current-branch — collapsed into the same behavior because
`clear` wasn't terminal enough.

Phase 9 fixes this by extending `cmd_clear` to wipe the inner
workspace contents via a new Alcatraz port method
(`wipe_workspace_contents`) and unlink `state.json`, keeping
`.alcatrazer/config.toml` so identity + daemon settings carry over.
The wipe runs in a one-shot side container (built from this
Alcatraz's own image, `--entrypoint find`, `-u agent`) — no agent
process is involved anywhere, so Principle 2 trivially holds.

**Phase 9 chown-back: required, and safe in a side container.**
Right after Step 9.3 GREEN shipped, a fresh-install manual test
showed the next `alcatrazer start` failing on `git init` after a
`clear` cycle:

```
subprocess.CalledProcessError: Command '['git', 'init', '/tmp/
.../.codelab-d13c']' returned non-zero exit status 1
```

Root cause: the side-container wipe (`find /workspace -mindepth 1
-delete`) emptied the bind-mount's contents but left the mount-
point dir itself owned by the phantom UID — the original
container's entrypoint had chowned `/workspace` to agent on first
start, and Step 9.3 deliberately did not chown back (per
Principle 2). `git init` on the next first-run runs from the host
shell as the host user, who cannot create `.git/` inside a
foreign-UID-owned dir.

The fix introduced a second one-shot side container right after
the wipe: `docker run --rm -u 0:0 --entrypoint chown
<host_uid>:<host_gid> /workspace`. This retags the empty mount-
point dir back to host ownership so the next `git init` works.

The chown-back was originally rejected during Phase 9 design as
violating Principle 2, but that rejection was specifically about
chowning inside the ORIGINAL running container where an active
agent could observe the ownership shift. A one-shot side container
has no agent process inside — Principle 2 gates ownership shifts
the agent can observe, not ownership shifts performed in
disposable contexts with no agent. The distinction is recorded as
a memory entry (`feedback_stealth_scope_is_observed_processes`)
so the rule doesn't have to be re-derived next time someone
proposes a chown.

The two-step side-container approach also pairs cleanly with the
"side container vs exec-after-resume" choice already made: the
chown is just a second `docker run --rm` with different flags, no
new infrastructure.