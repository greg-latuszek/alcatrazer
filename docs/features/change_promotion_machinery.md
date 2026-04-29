# Re-implementing promotion: replay agent commits, don't transfer history

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
| `refs/heads/alcatraz/main`   | Does not exist — namespace mode is opt-in via `[promotion-daemon].mode = "alcatraz-tree"`, default is `"mirror"`                                             |
| `git reflog show main`       | `main@{2}` outer initial → `main@{1}` fast-import → `main@{0}` fast-import. Original commit no longer ancestor of `main` (`merge-base --is-ancestor` → 1)    |
| `git fsck`                   | Clean — no broken refs, but original outer commit only kept alive by reflog                                                                                  |

So today's mirror mode does three damaging things every cycle:

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

## Why today's pipeline is the wrong tool

`src/alcatrazer/promote.py` lines 193–230 implement the mirror path as

```
git fast-export <refs>  |  rewrite_identity (regex sub)  |  git fast-import --force
```

`fast-export` / `fast-import` is the canonical pair for **transferring
a history graph between repos that should look identical** — mirroring,
backups, dump-and-restore, format migrations. That is a different
problem from what alcatrazer needs. Specifically, the pair has three
properties that are exactly wrong here:

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

Wrapping the existing pipeline with re-parenting logic and a
working-tree updater would work, but it would be re-inventing what
git already provides — purpose-built — under a different name.

## Why the foreign root exists at all

Two design facts collide:

1. **Snapshot, not clone (Principle 2).** `snapshot.py:146-153` does
   `git init` in a fresh workspace, extracts the outer's main tree
   via `tar`, then `git add -A && git commit --allow-empty -m "Initial commit"`.
   The workspace has its own root with no parent and no relation, in
   git terms, to the outer's history. This is deliberate — agents
   running `git log` must not see outer's commits, identities, or
   internal references.
2. **Commit identity is not tree identity.** Even when the trees are
   byte-identical, the workspace's `Initial commit` and the outer's
   tip have different commit SHAs because the commit object hashes
   over `tree + parents + author + committer + timestamps + message`
   — five of those six fields necessarily differ.

The two facts together mean the outer and inner repos cannot share
commits, but they share tree content at the snapshot point. The right
abstraction is therefore **commit replay**, not history transfer:
take new commits from inside (commits *after* the workspace root)
and reapply them as new commits onto the outer's current tip,
authored as the user.

## The right machinery: `git format-patch | git am`

`git am` was designed for exactly this scenario — Linus Torvalds
applying patches mailed in by kernel contributors onto his tree.
The shape maps onto alcatrazer perfectly:

| Linux kernel scenario | Alcatrazer scenario |
| --------------------- | ------------------- |
| Contributor commits in their own tree | Agent commits inside Alcatraz |
| Patch mailed via mbox | Patch piped daemon-side |
| Linus applies via `git am` onto `master` | Outer applies via `git am` onto `main` |
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

### What gets piped, exactly

```
inner_root := <recorded once at workspace creation, persisted in
                .alcatrazer/state — see §"Recording the inner root">

# Each cycle, per branch (mirror mode):
git -C $inner format-patch                        \
    --stdout --binary --keep-subject              \
    ${last_promoted_or_inner_root}..refs/heads/main  \
  | rewrite_from_header(name, email)              \
  | git -C $outer am                              \
    --committer-date-is-author-date               \
    --keep-non-patch                              \
    --whitespace=nowarn
```

- `${last_promoted_or_inner_root}..main` excludes the workspace
  root (and any already-promoted commits) from the stream, so
  outer's existing root and prior commits stay intact.
- `--binary` keeps non-text content applicable.
- `--keep-subject` prevents `format-patch` from re-prefixing
  subjects with `[PATCH]`.
- `rewrite_from_header()` substitutes the `From:` line in each
  mbox entry. Same idea as today's `rewrite_identity()`
  (`promote.py:124`); different regex (mbox header instead of
  fast-export `author`/`committer` lines).
- `--committer-date-is-author-date` keeps the committer timestamp
  identical to the author timestamp, so reflogs and `git log`
  show one chronology, not two.

### How this removes the bugs by construction

| Today's bug | Why `am` cannot exhibit it |
| ----------- | -------------------------- |
| Outer's `main` history rewritten | `am` only appends to HEAD; patches have no SHA targets |
| Working tree out of sync with HEAD | `am` writes files + index + ref atomically |
| Original outer commit replaced by inner root | `inner_root` is excluded from the format-patch range |
| `git status` shows phantom "deleted" entries | Working tree IS the patch target — status is clean afterwards |
| No abort path on failure | `git am --abort` |

## Recording the inner root

The format-patch range needs a stable boundary marker — the SHA of
the workspace's `Initial commit`. Cleanest place to record it:

- `snapshot.create_initial_commit` already creates the commit
  (`snapshot.py:146-153`). After the commit, capture
  `git rev-parse HEAD` and persist it.
- Storage: `.alcatrazer/inner-root` (one line, the SHA). Lives on
  the host side, alongside the existing daemon state. Never enters
  the workspace.
- Rewritten on every `clear` + `start` cycle (each new workspace
  has a new root). The mirror-mode promoter reads it at start of
  each cycle.

Alternative: re-derive at runtime via
`git rev-list --max-parents=0 refs/heads/main` (the parentless
commit reachable from main). Works as long as the workspace
history stays linear — which it does by contract — but adds a
git call per cycle. Recording at creation time is one less moving
part.

## Conflict semantics

`am` fails with `Patch does not apply` when outer's working tree
or HEAD has diverged from what the patch expects. Three cases the
daemon needs to handle:

1. **Outer working tree has uncommitted edits, no overlap with
   patch.** `am` succeeds, edits stay where they are
   (working-tree-only changes don't conflict with patch hunks for
   different files). No daemon work required beyond the success
   path.
2. **Outer working tree has uncommitted edits that overlap.**
   `am` fails. Daemon catches non-zero exit, runs
   `git -C outer am --abort`, logs `Promotion paused on <branch> —
   outer working tree conflicts with agent commit. Resolve by
   committing or stashing outer changes.` Pauses the branch (same
   `paused-branches.json` mechanism today's mirror mode uses for
   diverged-tip detection).
3. **Outer's `main` has commits the workspace doesn't know about.**
   This is the user committing on outer between snapshot creation
   and the next cycle. Today's mirror mode would silently rewrite
   them away. With `am`, patches simply land on top of the user's
   commits as further fast-forwards — `am` doesn't care that HEAD
   moved, it just patches against current HEAD. This is **the
   correct behavior** for "transparent end-user coding": user's
   commits are preserved, agent's commits land as additional
   fast-forwards above them.

The `alcatraz-tree` mode (audit-only, namespaced refs) is unrelated
to mirror's bugs and stays as-is — `fast-export | fast-import` is
correct for that mode because the goal there really IS history
transfer into a sibling ref.

## What this proposal replaces, what it leaves alone

### Replaced

- `promote.promote()` (mirror code path, `promote.py:193-232`).
- `promote.promote_with_conflict_handling()`
  (`promote.py:334+`) — the mirror-mode entry point used by the
  daemon. Replaced by a new `promote_via_patches()` with the same
  return shape (`{branch: status}`).
- The marks-file pair `promote-export-marks` /
  `promote-import-marks` — no longer needed in mirror mode.
  `last_promoted_tip` per branch (already in
  `promoted-tips.json`) is sufficient.

### Left alone

- `alcatraz-tree` mode — keeps using
  `promote.promote(..., namespace="alcatraz")`. Different goal,
  different machinery. Documented as "audit-only mirror to
  `refs/heads/alcatraz/<branch>`; promotes by namespace, does not
  touch your branches."
- `rewrite_identity()` (`promote.py:124`) — still useful for the
  alcatraz-tree path. The new `rewrite_from_header()` is a
  parallel function for mbox streams.
- Snapshot, identity, and isolation primitives — unchanged.
  Workspace creation still produces a flat snapshot with a fresh
  root, agents still see a vanilla repo with the random human
  identity.
- Daemon polling, lifecycle, conflict-branch creation in
  alcatraz-tree mode — unchanged. The daemon's `run_cycle`
  dispatches on `mode`; only the mirror branch is rewritten.

## Concrete code touches

- `src/alcatrazer/snapshot.py`
  - `create_initial_commit()` returns the new commit SHA (or
    a sibling helper records it).
  - `snapshot_workspace()` writes the SHA to
    `.alcatrazer/inner-root` after the initial commit lands.

- `src/alcatrazer/promote.py`
  - New `rewrite_from_header(stream: bytes, name: str, email: str) -> bytes`.
    Substitutes the `From:` line in each mbox entry. Operates in
    bytes (patches contain binary diffs).
  - New `format_patch_stream(source: Path, since_sha: str, ref: str) -> bytes`.
    Wrapper around `git format-patch --stdout --binary --keep-subject
    <since>..<ref>`. Returns empty bytes when there is nothing to
    promote (range is empty).
  - New `apply_patch_stream(target: Path, stream: bytes, name: str, email: str) -> None`.
    Spawns `git -C target am --committer-date-is-author-date
    --keep-non-patch --whitespace=nowarn` with `stream` on stdin
    and `GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` /
    `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` set in the environment to
    `name`/`email`. Raises a typed `PromotionConflictError` on
    non-zero exit, after running `git am --abort` to clean up.
  - New `promote_via_patches(source, target, name, email, branches,
    inner_root, paused_branches) -> dict[str, str]`. Per branch:
    resolves `since_sha` (last_promoted_tip if present, else
    inner_root), formats patches, rewrites `From:`, applies; on
    conflict, marks the branch paused and continues with other
    branches.
  - The existing `promote()` and `rewrite_refs()` functions stay
    for the `alcatraz-tree` mode.

- `src/alcatrazer/daemon.py`
  - `run_cycle()` mirror branch (`daemon.py:241+`) calls
    `promote_via_patches()` instead of `promote_with_conflict_handling()`.
  - Reads `inner-root` from `.alcatrazer/inner-root` once at
    daemon startup; passes into each cycle.
  - Logs include the per-cycle commit count and any paused
    branches' reason — the user's "what just happened" surface.

- `src/alcatrazer/start.py`
  - `cmd_start`'s post-success message gains a one-liner clarifying
    promotion semantics: `Agent commits land on \`main\` as your
    own (working tree updates automatically).` Replaces today's
    silence on the matter.
  - On promotion conflict (paused branch), `cmd_status` (or
    equivalent) surfaces the paused state and the recovery hint.

- Tests
  - `tests/test_promote.py`
    - `test_format_patch_stream_excludes_inner_root` — given a
      workspace with `inner_root` + N agent commits, the stream
      contains exactly N patches.
    - `test_rewrite_from_header_substitutes_author_in_mbox` — feeds
      a real mbox with a Patricia author, expects Alice in output.
      Operates on bytes, preserves binary hunks.
    - `test_apply_patch_stream_advances_main_and_updates_worktree`
      — outer with one commit, apply patches for an "agent commit"
      adding a file → outer's main has 2 commits, working tree
      contains the file, `git status` clean.
    - `test_apply_patch_stream_preserves_outer_history` — outer
      with one commit (call it `O1`), apply N patches → outer's
      main is `O1 → A1 → … → AN`, `O1` still ancestor of HEAD.
      **Direct regression test for the bug surfaced in manual
      testing.**
    - `test_apply_patch_stream_identity_is_user_for_author_and_committer`
      — assert both fields on each new commit equal the configured
      user (Alice). Inner identity (Patricia) appears nowhere in
      outer.
    - `test_apply_patch_stream_aborts_cleanly_on_conflict` — pre-
      seed outer with a conflicting edit, apply → raises
      `PromotionConflictError`, outer's HEAD and tree are byte-
      identical to the pre-call state.
    - `test_promote_via_patches_pauses_branch_on_conflict` —
      multi-branch, one conflicts, others succeed; conflicted
      branch is in the returned `paused` set, others marked
      `promoted`.
  - `tests/test_snapshot.py`
    - `test_create_initial_commit_records_inner_root` — after
      `snapshot_workspace`, `.alcatrazer/inner-root` exists, content
      equals `git -C workspace rev-parse HEAD`.
  - `tests/test_daemon.py`
    - Mirror-mode integration test using the same test repo shape
      as the manual probe (one outer commit, one agent commit
      inside) — assert outer's `main` is now 2 commits with outer's
      original commit as ancestor, working tree contains agent's
      file, `git status` clean. **Closes the manual-test bug under
      automation.**
  - One existing daemon test that asserted `fast-import` got
    invoked in mirror mode needs to swap its expectation to
    `git am`. Tests that exercised marks-file behavior in mirror
    mode get deleted (no marks files in the new mirror path).

- Documentation
  - `docs/design_principles.md` — extend the "Promotion" section's
    "Daemon Watches from Outside" to add a one-liner: *"Mirror
    mode replays new agent commits onto outer's current tip via
    `git format-patch | git am` — outer keeps its history,
    working tree updates atomically, identity is rewritten to the
    user."*
  - `README.md` — the "Promotion" subsection mentions: outer's
    working tree updates automatically when agents commit; if
    outer has uncommitted changes that conflict with an agent
    patch, the daemon pauses that branch and surfaces a recovery
    message.

## What this proposal does NOT do

- **Does not change the snapshot model.** Workspace remains a flat
  snapshot with a fresh root and a random human identity. All of
  Principle 2 stands.
- **Does not change the daemon's polling or lifecycle.** Same poll
  loop, same PID file, same shutdown semantics. Only the inner
  promote-call swaps.
- **Does not touch alcatraz-tree mode.** That mode's
  fast-export/fast-import pipeline is correct for its goal
  (namespaced audit ref). Both modes coexist; users choose via
  `[promotion-daemon].mode`.
- **Does not add merge-commit handling.** Workspace history is
  linear by contract. If a future feature creates merges in the
  workspace, this design's `--no-merges` (drop) and
  `git merge --ff-only` (keep) extensions are both straightforward
  but out of scope here.
- **Does not auto-stash outer's uncommitted changes on conflict.**
  We pause and surface a message instead. Auto-stash adds a class
  of failure modes (stash conflicts, dropped stashes) that the
  user has no expectation of and would silently destabilize their
  working tree.
- **Does not change the `[promotion]` config schema.** Identity is
  still resolved through the existing priority chain
  (`promote.py:96-115`).
- **Does not introduce a new CLI subcommand.** Behavior is
  daemon-internal; surfaces via existing log + status outputs.

## Recommended order

This is one logically-coherent change; no value in splitting the
RED/GREEN/BLUE pieces across multiple PRs.

1. **RED:** add the manual-test regression as an automated test —
   one outer commit, one agent commit, assert outer's original is
   still an ancestor of `main` after promotion + working tree is
   clean + author/committer are the user. Will fail on current
   `main`.
2. **RED+:** add unit tests for `rewrite_from_header`,
   `format_patch_stream`, `apply_patch_stream` covering the
   identity, range, and conflict-abort behaviors above.
3. **GREEN:** implement the three new functions in `promote.py`,
   wire `daemon.py`'s mirror branch to `promote_via_patches`,
   record `inner-root` from `snapshot.py`, drop marks-file
   handling from the mirror path.
4. **GREEN+:** update existing daemon mirror-mode tests to assert
   the new pipeline; delete obsolete marks-file tests.
5. **BLUE:** README + `design_principles.md` updates; add the
   post-success line in `cmd_start`; surface paused-branch state
   in user-facing output.

Estimated total: ~4–6 hours including tests and doc.

## Open questions

- **Conflict UX granularity.** When a branch pauses, do we
  surface it only via the daemon log, or also via a CLI like
  `alcatrazer status`? Lean toward log-first; add `status` only
  if real users surface a need.
- **Backwards compatibility with running alcatrazers.** A user
  who already has a daemon running on the old mirror path will
  not have `.alcatrazer/inner-root` recorded. Two options: (a) the
  daemon falls back to `git rev-list --max-parents=0` to derive
  inner_root at runtime when the file is absent — simple, costs
  one git call per startup; (b) require `alcatrazer clear &&
  start` to re-snapshot. Lean (a) — it's free and keeps the
  upgrade transparent.
- **Whether to retire `alcatraz-tree` mode entirely.** With mirror
  mode actually working, the namespaced-audit-ref mode loses most
  of its motivation. Keep for one release as a non-default
  fallback for users who want a paper trail without touching
  their branches; reassess removal after.
- **Promotion of new branches the agent creates inside the
  workspace.** Today's `branches = "all"` mode promotes every
  inner branch. With `am`, each new inner branch needs an explicit
  outer-side counterpart. Either: (a) auto-create the matching
  outer branch on first promotion, or (b) only mirror branches
  that already exist in outer (matches "agents propose, user
  decides"). Lean (b).