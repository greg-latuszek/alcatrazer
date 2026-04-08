# Start from Existing Repository

## Status: Implementation (branch: start_from_existing_repo)

## Problem

When a developer adopts Alcatrazer, they're usually not starting a greenfield project. They have an existing codebase with history, branches, and work in progress. When they run `initialize_alcatraz.sh`, the inner workspace (`.alcatraz/workspace/`) is created as an empty git repo. The agents start from nothing.

The developer wants agents to **continue** their work — not rewrite it from scratch. The inner workspace needs to start with the current state of the project.

## Design Decision: Snapshot, Not Clone

### What we copy: a snapshot of the current main branch

The inner workspace gets a **flat snapshot** of the outer repo's main (or master) branch — files only, no git history. One initial commit inside the workspace containing the current project state.

**Why no git history:**
- History increases attack surface — agents could `git log` to discover developer identities, commit patterns, internal references, ticket numbers, and other metadata that's none of their business
- History is large — copying it wastes disk and slows initialization
- Agents don't need history to write code — they need the current codebase
- Keeping the workspace minimal aligns with Principle 1 (fight for security)

**Why not a shallow clone:**
- Even `git clone --depth 1` preserves the remote URL, the branch name, and the most recent commit metadata (author, date, message)
- A shallow clone still has a `.git/config` that may leak information
- The cleanest approach is a fresh `git init` + file copy + single commit

### Which branch: main/master only

The snapshot is always taken from the outer repo's main branch (`main` or `master` — auto-detected).

**Why not allow any branch:**

It's tempting to let the user specify a branch: "I want agents to continue my `feature/payment-refactor` work." But this creates a subtle trap in mirror mode:

1. User starts inner workspace from `feature/payment-refactor`
2. Agents work on inner `main` (the workspace's main branch)
3. Daemon promotes inner `main` → outer `main`
4. The feature branch content silently merges into the outer `main`

The user didn't intend to merge their feature branch into main. But the promotion path `outer-feature-branch → inner-main → outer-main` does exactly that.

**Hard rule: start from main/master only.** This is a trust decision:
- The user always knows what happened — "agents started from my main branch"
- No accidental cross-branch contamination
- Clear mental model: main → workspace → main

If the user truly wants agents to work on a feature branch, they should merge it to main first, then initialize. That's an explicit decision, not an accident.

### How to detect the default branch

Note: `git config init.defaultBranch` is NOT the answer — it controls what `git init` uses for *new* repos, not what an existing repo considers its default branch.

Detection priority:

1. **`origin/HEAD`** (authoritative) — this is what GitHub/GitLab set as the "default branch":
   ```bash
   git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'
   ```
   If the repo has a remote with `origin/HEAD` set, this is the definitive answer. Handles the case where both `main` and `master` exist.

2. **Existence check** (fallback for repos without a remote or where `origin/HEAD` is not set):
   ```bash
   if git rev-parse --verify refs/heads/main >/dev/null 2>&1; then
       SOURCE_BRANCH="main"
   elif git rev-parse --verify refs/heads/master >/dev/null 2>&1; then
       SOURCE_BRANCH="master"
   fi
   ```
   If both `main` and `master` exist and there's no `origin/HEAD`, we cannot guess — ask the user.

3. **Ask the user** (last resort):
   ```
   Both 'main' and 'master' branches exist. Which is the default branch?
     1. main
     2. master
   ```

## Implementation

### What `initialize_alcatraz.sh` does (updated)

After creating `.alcatraz/workspace/` and initializing git:

1. Detect outer repo's main branch (main or master)
2. Check out that branch's files into a temp dir (or use `git archive`)
3. Copy files into `.alcatraz/workspace/`
4. Create initial commit: `"Initial commit"` — generic, zero Alcatraz footprint (Principle 2)

### Using `git archive` for clean extraction

```bash
# Extract current main branch files without any git metadata
git archive HEAD --format=tar | tar -xf - -C .alcatraz/workspace/
```

`git archive` is ideal:
- Extracts only tracked files (respects `.gitignore`)
- No `.git/` directory, no history, no remote URLs
- Clean file tree, ready to commit into the workspace

### What gets excluded

Files that should NOT enter the workspace:
- `.alcatraz/` — our own tool state (would be recursive)
- `.env` — host secrets
- `.git/` — handled by `git archive` (never included)

The `.gitignore` of the outer repo already controls what's tracked, so `git archive` naturally respects it. But we should also exclude `.alcatraz/` and `.env` explicitly in case they were tracked by mistake.

### Commit message

```
Initial commit
```

No metadata — no branch name, no hash, no mention of Alcatraz or snapshots. Agents see a generic initial commit, indistinguishable from any new project. Traceability is handled on the host side (the outer repo knows when init was run from git log). This aligns with Principle 2: zero footprint inside the workspace.

## Resolved Questions

### 1. Automatic snapshotting (not opt-in)

Snapshotting is **automatic**. If the outer repo has tracked files on the main branch, they are copied into the workspace during initialization. Agents in Docker start from where the outside repo has ended — that's the whole point.

### 2. Outer repo must exist

`initialize_alcatraz.sh` **requires** a git repository. If run outside a git repo, it fails with a clear error. Alcatrazer is designed to wrap an existing project — if there's no repo, there's nothing to sandbox around.

**Edge case: empty repo (no commits).** A freshly `git init`'d repo with no commits is valid — it's a greenfield project. The snapshot step is a no-op, and the inner workspace starts with an empty initial commit.

### 3. Re-initialization re-snapshots from current main

When the user runs `--reset`, the workspace is re-snapshotted from the outer repo's current main branch.

**Edge case: unpromoted work exists.** Before destroying the workspace, check if the inner repo has commits that haven't been promoted to the outer repo (same detection logic used by the promotion daemon). If unpromoted commits exist, warn the user:

```
Warning: N commits in workspace have not been promoted to outer repo.
Proceeding with --reset will discard them.
  1. Proceed — shutdown Docker, discard inner workspace, re-snapshot, reboot
  2. Cancel — abort reset, no changes
```

If the user chooses **proceed**: shutdown Docker container → remove inner `.git` and workspace files → re-snapshot outer main → fresh `git init` + initial commit → reboot Alcatrazer.

If the user chooses **cancel**: abort the reset, no changes.

**Why we don't detect coding start/end:** Alcatrazer is agent-system-agnostic. Detecting whether agents are actively coding would require knowledge of the specific agentic system running inside Docker (e.g., `ov status` for Overstory, different signals for other systems). We don't want to bind Alcatrazer to any specific agent framework. The end user knows what agents they put inside Alcatrazer and how to check their status — if they request `--reset`, they know the consequences. We just warn about unpromoted work.

### 4. Large repos — no special handling

Large repos are slow to snapshot and produce large workspaces. This is inherent — no special handling needed. If the project is big, the workspace is big.

### 5. `.gitignore` — copy with single filter

Copy the outer repo's `.gitignore` into the workspace, filtering out only the `.alcatraz/` rule. Everything else passes through unchanged.

**Why only `.alcatraz/`:** This rule exists solely because the management layer was installed — it's a footprint of Alcatrazer managing the repo, not project content. All other entries (test temp dirs, IDE configs, `node_modules/`, etc.) are legitimate project rules that agents need.

**Dogfooding edge case:** If Alcatrazer is used to develop Alcatrazer itself, entries like `tests/**/promotion_temp_output/` are part of the project the agents are building — not evidence of a hidden management layer. The distinction is: project content (agents should see) vs. management infrastructure (agents should not see).

## Open Questions

None — all questions resolved. Ready for implementation.

---

## Detailed Implementation Plan

Each step is one commit, small enough for human review. Dependencies flow top to bottom — each step may require previous steps to be in place.

**TDD discipline:** Each step follows the RED/GREEN/BLUE cycle where possible:
- `[RED]` commit — failing test for the planned functionality
- `[GREEN]` commit — implementation that makes the test pass
- `[BLUE]` commit — improvements/cleanup if applicable

### Phase 1: Default branch detection

The snapshot needs to know which branch to extract from. This is a standalone, testable unit.

**Step 1.1** — `Detect outer repo's default branch`
> A Python function that implements the three-tier detection priority:
> 1. `origin/HEAD` symbolic ref (authoritative)
> 2. Existence check — `main` then `master`; fail if both exist without `origin/HEAD`
> 3. Return `None` if no commits exist (greenfield)
>
> Must work from the outer repo (the directory where `initialize_alcatraz.sh` runs).

**Step 1.2** — `Fail if not inside a git repository`
> A Python function that verifies the current directory is inside a git working tree. Returns the repo root path, or raises an error with a clear message.

### Phase 2: Snapshot extraction

Extract files from the outer repo's main branch into the workspace. This is the core of the feature.

**Step 2.1** — `Extract snapshot via git archive`
> A Python function that takes the outer repo path, source branch, and target directory, then runs `git archive <branch> | tar -xf - -C <target>`. Must handle the empty-repo case (no commits → no-op).

**Step 2.2** — `Filter .gitignore during snapshot`
> After extraction, if `.gitignore` exists in the workspace, remove lines matching `.alcatraz/` (exact rule, not substring — don't filter `.alcatraz-something/`). If filtering leaves the file empty, remove it entirely.

**Step 2.3** — `Exclude .alcatraz/ and .env from snapshot`
> Verify `git archive` doesn't include `.alcatraz/` or `.env` if they happen to be tracked. Use `git archive` with explicit exclusion (`--worktree-attributes` or tar filtering). Test with a repo where `.env` is tracked — it must not appear in the workspace.

**Step 2.4** — `Create initial commit from snapshot`
> After files are extracted into the workspace (which already has `git init` from Step 3 of initialize_alcatraz.sh), stage all files and create a commit with message `"Initial commit"`. The commit must use the Alcatraz Agent identity already configured in the workspace. If no files were extracted (greenfield), create an empty initial commit.

### Phase 3: Integration into initialize_alcatraz.sh

Wire the snapshot into the existing initialization flow.

**Step 3.1** — `Add snapshot step to initialize_alcatraz.sh`
> Snapshot is implemented in Python (src/snapshot.py — already tested). This requires reordering initialize_alcatraz.sh: Python resolution (currently Step 5) must move before git init (currently Step 3), so `.alcatraz/python` is available when the snapshot step runs. The full regression test suite validates nothing breaks from the reorder.
>
> New step order: .env → UID → Python resolution → git init → snapshot → safe.directory.

### Phase 4: Reset with unpromoted work warning

Enhance `--reset` to warn about unpromoted commits before destroying the workspace.

**Step 4.1** — `Detect unpromoted commits in workspace`
> A Python function that checks whether the inner repo has commits that haven't been promoted. Reuses the same logic as `promote.py --dry-run`: run fast-export with import-marks and check if output contains any commits. Returns the count of unpromoted commits.
>
> Edge cases: marks file doesn't exist (never promoted — all commits are unpromoted), workspace has no commits (nothing to warn about).

**Step 4.2** — `Add unpromoted-work warning to --reset flow`
> Before destroying the workspace, call the detection function. If unpromoted commits exist, print the warning and prompt (proceed/cancel). If the user cancels, exit without changes. If no unpromoted work exists, proceed silently.
>
> Must handle non-interactive mode (e.g., `--reset --force` to skip the prompt for scripted usage).

**Step 4.3** — `Re-snapshot after reset`
> After cleaning the workspace, re-run the snapshot from the outer repo's current main branch. The reset flow becomes: clean → re-init git → re-snapshot → re-configure identity → re-resolve Python (or reuse existing `.alcatraz/python`).

### Phase 5: End-to-end integration test

**Step 5.1** — `Integration test: init from existing repo`
> Create a temp git repo with files and commits on main. Run the full initialization flow. Verify:
> - Workspace contains the files from outer main
> - Workspace has exactly one commit ("Initial commit")
> - No git history from outer repo is visible
> - `.gitignore` doesn't contain `.alcatraz/` rule
> - `.env` is not in the workspace even if tracked in outer repo
> - `.alcatraz/` directory is not in the workspace

**Step 5.2** — `Integration test: init from empty repo`
> Create a temp git repo with `git init` but no commits. Run initialization. Verify:
> - Workspace exists with git initialized
> - Workspace has an empty initial commit (or no commits — decide during implementation)
> - No errors during initialization

**Step 5.3** — `Integration test: reset with unpromoted work`
> Create a workspace with promoted and unpromoted commits. Run `--reset`. Verify:
> - Warning is displayed with correct unpromoted commit count
> - Cancel aborts without changes
> - Proceed destroys and re-snapshots from current outer main

### Phase 6: Documentation

**Step 6.1** — `Update README with snapshot behavior`
> Document that initialization automatically snapshots the outer repo's main branch. Document `--reset` behavior including the unpromoted work warning. Document the `.gitignore` filtering.

**Step 6.2** — `Mark plan complete`
> Update status to "Complete" at the top of this document.

### Implementation Notes

Decisions resolved during implementation:
- **Python for snapshot step (decided):** Snapshot is Python (src/snapshot.py), not bash. Python resolution moves earlier in initialize_alcatraz.sh (before git init). Full regression suite guards the reorder.
- **Empty initial commit for greenfield:** `git commit --allow-empty -m "Initial commit"` — decide if this is valuable or if an empty workspace with no commits is cleaner.
