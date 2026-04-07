# Start from Existing Repository

## Status: Planning

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

## Open Questions

1. **Should this be opt-in or automatic?** If the outer repo has tracked files, should `initialize_alcatraz.sh` always snapshot them into the workspace? Or ask first? For a greenfield project (empty repo), this step is a no-op.

2. **What about `.gitignore` inside the workspace?** The outer repo's `.gitignore` may contain entries that don't make sense inside the workspace (like `.alcatraz/`). Should we filter or copy as-is? Probably copy as-is — agents can modify it if needed.

3. **Re-initialization:** If the user runs `--reset` and re-initializes, should it re-snapshot from the current main? Yes — that's the expected behavior. The main branch may have advanced since the first init.

4. **Large repos:** `git archive` of a large repo could be slow and the workspace large. This is inherent — if the project is big, the workspace is big. No special handling needed, but worth documenting.
