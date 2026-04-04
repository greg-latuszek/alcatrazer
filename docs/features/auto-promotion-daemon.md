# Auto-Promotion Daemon

## Status: Planning

## The Iceberg Principles

These principles guide every design decision in Alcatrazer. When options conflict with a principle, the option is eliminated.

### Principle 1: Fight for security — think about attack surfaces

Every feature, every configuration option, every file placement is evaluated through the lens of: does this create a new attack surface? If yes, is it necessary? Can it be eliminated? Security is not a feature — it is the architecture.

### Principle 2: Inmates are not aware they live in Alcatraz

Agents working inside the container must not be able to discover that their environment was created by Alcatrazer. If an agent realizes it is inside an Alcatraz workspace, it can:

1. Search GitHub for "alcatrazer"
2. Read the tool's source code
3. Identify weaknesses in the isolation mechanism
4. Attempt to exploit them

Therefore: **zero footprint inside the workspace**. No alcatrazer-specific files, no branded hooks, no recognizable configuration patterns, no environment variables that hint at the tool's name. The workspace must look like an ordinary git repository in an ordinary container.

This means:
- **Nothing from Alcatrazer may exist inside `.alcatraz/workspace/`** — no hooks, no config files, no markers
- **Nothing from Alcatrazer may be visible in the container environment** — no branded env vars, no identifiable process names
- **The daemon must operate entirely from the host side** — it observes the workspace from outside, never touches the inside

---

## Directory Structure: `.alcatraz/` Redesign

To satisfy Principle 2 while keeping tool state colocated, `.alcatraz/` is split into two zones:

```
.alcatraz/                          <-- gitignored, entire tool state
├── workspace/                      <-- mounted into Docker as /workspace
│   ├── .git/                       <-- inner git (agent work, clean — no tool traces)
│   └── ... agent files ...
├── uid                             <-- phantom UID (currently in .env as ALCATRAZ_UID)
├── promote-export-marks            <-- incremental promotion state (currently in outer .git/)
├── promote-import-marks            <-- incremental promotion state (currently in outer .git/)
└── daemon.log                      <-- daemon output (future)
```

**Key properties:**

- **`workspace/`** is the only thing mounted into Docker. Agents see a vanilla git repo — nothing else. Principle 2 is satisfied.
- **Tool state** (UID, marks, logs) lives alongside the workspace but is never visible to agents — it is outside the mount boundary.
- **One gitignore entry** (`.alcatraz/`) covers everything — workspace, tool state, daemon logs.
- **`.env` becomes simpler** — only API keys, no tool state like `ALCATRAZ_UID`. The UID moves to `.alcatraz/uid`.
- **Promotion marks move** from the outer `.git/` to `.alcatraz/` — keeps the outer repo's `.git/` clean of tool artifacts.

**Migration needed** (prerequisite, separate from daemon work):
- `docker-compose.yml`: mount `.alcatraz/workspace/` instead of `.alcatraz/`
- `initialize_alcatraz.sh`: create `workspace/` subdirectory, write UID to `.alcatraz/uid`
- `promote.sh`: read/write marks from `.alcatraz/` instead of target `.git/`
- `.env`: remove `ALCATRAZ_UID`, read from `.alcatraz/uid` instead
- Smoke test: update paths
- Promotion test: update mark file expectations

---

## Feature Description

The auto-promotion daemon is a background process that watches the inner git repository (`.alcatraz/workspace/`) for new commits and automatically promotes them to the outer git repository using `promote.sh`. This eliminates the need for the human operator to manually run the promotion script — agent work appears in the outer repo in near real-time as it is produced.

The daemon runs on the **host** side (not inside the Docker container), watching `.alcatraz/workspace/.git/` for changes. When it detects new commits, it runs the promotion pipeline (fast-export | identity rewrite | fast-import) to transfer them to the outer repo.

## Origin

From the initial design discussion:

> "What if after installation tool runs in background and decides by itself when to export git 'new increment' from within docker and pull it into outside-docker git."

The user's vision is that `promote.sh` should not be a manual step. The daemon should autonomously decide when to promote, making the experience seamless: agents commit inside Docker, and the human sees those commits appear in their real repo automatically.

## What We Know

### Existing Infrastructure

- `promote.sh` already works with incremental promotion via fast-export/fast-import mark files
- Identity rewrite is handled (agent identity -> real identity from `alcatrazer.toml`)
- The priority chain for author identity is: git config < alcatrazer.toml < CLI flags
- `.alcatraz/workspace/` is the inner workspace, mounted into Docker
- Files in `.alcatraz/workspace/` are owned by the phantom UID (not writable by host user, but readable)
- Tool state (UID, marks, logs) lives under `.alcatraz/` but outside `workspace/` — host-only, never visible to agents
- The host adds `.alcatraz/workspace/` as `safe.directory` so git can operate on it despite phantom UID ownership

### Promotion Behavior

- `promote.sh` supports `--dry-run` to check for pending commits without modifying anything
- Mark files (`promote-export-marks`, `promote-import-marks`) will move to `.alcatraz/` (currently in target repo's `.git/`)
- Promotion is idempotent — running it when there's nothing new is a no-op
- Full branch and merge topology is preserved

## Design Decisions

### 1. What triggers promotion?

**Polling** — runs promotion check on a configurable interval (default 5 seconds) from the host side. No external dependencies, no footprint inside the container, satisfies Principle 2. The interval is configurable via `alcatrazer.toml`.

### 2. Which branches get promoted?

Configurable via `alcatrazer.toml`. The user chooses which branches cross the water:

- `"all"` — every branch, full git tree with all work-in-progress visible (default)
- `"main"` — only the main branch, just the final merged results
- A list of glob patterns — fine-grained control, e.g. `["main", "feature/*"]`

This lets the user balance between full visibility and clean outer history.

### 3. Where does the daemon run?

**Host process.** Simplest, direct access to the outer repo (owned by the host user), only needs to read `.alcatraz/workspace/` (world-readable despite phantom UID ownership). A bash script with a polling loop. The daemon is a host-side tool — it belongs on the host.

### 4. How does the daemon handle the "dubious ownership" problem?

`.alcatraz/workspace/` is owned by the phantom UID, so git on the host refuses to operate on it by default. The `initialize_alcatraz.sh` script adds `.alcatraz/workspace/` to `git config --global safe.directory`. This is safe — the directory is ours, created and controlled by our tool, phantom-UID-owned by design. Native git on the host, zero overhead.

### 5. What should the daemon output?

- **Silent by default** — log to a file, no terminal noise
- **Status updates on events** — print a line when commits are promoted
- **Verbose mode** — detailed logging for debugging

**Recommendation:** **Status updates on events** by default (one line per promotion: timestamp, commit count, branches). Verbose mode via `--verbose` flag. Log to stdout (user can redirect if they want a file).

### 6. How is the daemon started/stopped?

- **Manual** — `alcatrazer watch` (or `./scripts/watch.sh`) to start, Ctrl+C to stop
- **Automatic** — started alongside `docker compose up`

**Recommendation:** **Manual start** for now. The daemon is a host-side process, so tying it to `docker compose up` is not straightforward. A simple script that the user runs in a terminal tab. Future: could be a docker compose service with host networking.

### 7. Conflict handling

The promotion is unidirectional (inner -> outer). But the human may also commit to the outer repo — either occasionally or continuously. This creates potential merge conflicts during promotion.

The conflict strategy depends on how the user works. Two modes:

#### Mode: `mirror` (default)

**Use case:** The user develops via Alcatraz agents, with occasional manual commits possible. The outer repo is a live mirror of the inner repo with rewritten identity.

**Behavior:** Promote directly to the same branch names (inner `main` -> outer `main`, inner `feature/x` -> outer `feature/x`). No namespace, no manual merges — seamless sync.

**On conflict:** Daemon pauses promotion on the affected branch, creates a `conflict/resolve-<branch>-<timestamp>` branch with the promoted state, logs a warning explaining what happened, and waits for the user to resolve. Non-conflicting branches continue to promote normally.

#### Mode: `alcatraz-tree`

**Use case:** The user actively codes alongside Alcatraz agents — both parties are committing frequently. Conflicts are expected and frequent.

**Behavior:** Promote into `alcatraz/*` namespace (inner `main` -> outer `alcatraz/main`, inner `feature/x` -> outer `alcatraz/feature/x`). The user takes responsibility for merging from `alcatraz/*` branches into their own branches when ready.

**On conflict:** Not applicable — separate namespace means no conflicts.

#### Conflict resolution flow (for `mirror` mode)

1. Daemon detects fast-import failure on branch `X`
2. Daemon creates branch `conflict/resolve-X-<timestamp>` with the promoted commits
3. Daemon logs: `CONFLICT on branch X — promoted state saved to conflict/resolve-X-<timestamp>. Resolve manually, then the daemon will resume.`
4. Daemon stops promoting branch `X` but continues promoting other branches
5. Once the conflict branch is merged or deleted, the daemon resumes promoting `X`

### 8. Configuration in alcatrazer.toml

```toml
[promotion-daemon]
# Polling interval in seconds
interval = 5

# Which branches to promote from the inner repo:
#   "all"    — every branch (full git tree with all history)
#   "main"   — only the main branch (final merged results)
#   Or a list of glob patterns: ["main", "feature/*"]
branches = "all"

# Conflict handling mode:
#   "mirror"        — promote to same branch names, seamless sync (default)
#   "alcatraz-tree" — promote to alcatraz/* namespace, user merges manually
mode = "mirror"
```

Daemon log is always written to `.alcatraz/daemon.log` (not configurable — it's tool state, not a user decision).

## Implementation Plan

1. **`scripts/watch.sh`** — the daemon script
   - Polling loop with configurable interval
   - Reads config from `alcatrazer.toml` (`[promotion-daemon]` section)
   - Runs `git fast-export` via Docker to handle ownership
   - Pipes through identity rewrite sed
   - Runs `git fast-import` on host targeting outer repo
   - Handles `mirror`/`alcatraz-tree` conflict modes
   - Creates conflict resolution branches on failure (`mirror` mode)
   - Status output on promotion events, summary on Ctrl+C

2. **Update `promote.sh`** — add `--namespace` flag for `alcatraz-tree` mode branch prefix support

3. **Update `alcatrazer.toml`** — add `[promotion-daemon]` section

4. **Tests**
   - Test daemon detects new commits and promotes them (mirror mode)
   - Test mirror mode with no conflicts
   - Test mirror mode conflict detection and resolution branch creation
   - Test alcatraz-tree mode namespace mapping (inner `main` -> outer `alcatraz/main`)
   - Test idempotency (running when nothing new is a no-op)
   - Test daemon handles container not running gracefully
   - Test daemon resumes after conflict branch is resolved

## Dependencies and Constraints

- Must not require root access on the host
- Must handle the phantom UID ownership boundary via `safe.directory`
- Must be idempotent (safe to restart, safe to run multiple instances)
- Must not interfere with agents working inside the container
- Must leave zero footprint inside `.alcatraz/workspace/` (Principle 2)
- Tool state (marks, logs) lives under `.alcatraz/` but outside `workspace/`
- Must work on Linux (primary) and macOS (secondary)
- Should add minimal dependencies to the host system

## Possible Future Improvements

- **File watcher trigger** — replace polling with `inotifywait` (Linux) or `fswatch` (macOS) watching `.alcatraz/workspace/.git/refs/` for immediate reaction. Lower latency, but adds an external dependency.
- **Daemon in a separate Docker container** — more portable across host OSes, but needs access to both `.alcatraz/` and the outer `.git/`, complicating volume mounts. Introduces a second container to manage.

## Open Items

- [ ] Decide: should the daemon auto-stop when the container stops?
- [ ] Consider: graceful handling of Docker not running / container not built
- [ ] Consider: how does the daemon detect that a conflict resolution branch has been resolved?
- [ ] Consider: in `mirror` mode, should the daemon attempt auto-merge before creating a conflict branch?