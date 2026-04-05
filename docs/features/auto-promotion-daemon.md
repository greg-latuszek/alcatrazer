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
└── promotion-daemon.log            <-- promotion daemon output (future)
```

**Key properties:**

- **`workspace/`** is the only thing mounted into Docker. Agents see a vanilla git repo — nothing else. Principle 2 is satisfied.
- **Tool state** (UID, marks, logs) lives alongside the workspace but is never visible to agents — it is outside the mount boundary.
- **One gitignore entry** (`.alcatraz/`) covers everything — workspace, tool state, daemon logs.
- **`.env` becomes simpler** — only API keys, no tool state like `ALCATRAZ_UID`. The UID moves to `.alcatraz/uid`.
- **Promotion marks move** from the outer `.git/` to `.alcatraz/` — keeps the outer repo's `.git/` clean of tool artifacts.

**Migration needed** (prerequisite, separate from daemon work):

Directory restructuring — tool source code moves to `src/`, Docker files move to `container/`:

```
src/                            <-- tool source code
├── initialize_alcatraz.sh
├── watch_alcatraz.sh           <-- new (daemon)
├── inspect_promotion.sh        <-- new (daemon)
└── promote.sh

container/                      <-- Docker infrastructure
├── Dockerfile
├── docker-compose.yml
└── entrypoint.sh
```

Other migrations:
- `docker-compose.yml`: mount `.alcatraz/workspace/` instead of `.alcatraz/`
- `initialize_alcatraz.sh`: create `workspace/` subdirectory, write UID to `.alcatraz/uid`
- `promote.sh`: read/write marks from `.alcatraz/` instead of target `.git/`
- `.env`: remove `ALCATRAZ_UID`, read from `.alcatraz/uid` instead
- Smoke test: update paths
- Promotion test: update paths

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

### 5. Logging and observability

**Silent by default.** The daemon writes to a rotating log file at `.alcatraz/promotion-daemon.log` — no terminal output. The user can inspect activity when needed without the daemon competing for terminal attention.

**Rotating log** — the log file is capped at a configurable size. When it reaches the limit, the old log is rotated to `.alcatraz/promotion-daemon.log.1` and a fresh one starts. Only the current and one previous log are kept. This prevents unbounded growth while preserving enough history for troubleshooting.

**Verbosity levels:**
- `normal` (default) — one line per promotion event: timestamp, commit count, branches affected. Conflicts and errors.
- `detailed` — full fast-export/fast-import output, branch matching, identity rewrite details. For troubleshooting.

**Live inspection** — `inspect_promotion.sh` is a thin wrapper around `tail -f .alcatraz/promotion-daemon.log` that the user runs in a separate terminal to watch promotion activity in real time.

### 6. How is the daemon started/stopped?

**Manual.** The user runs `./watch_alcatraz.sh` in a terminal tab, Ctrl+C to stop. Consistent naming with `initialize_alcatraz.sh`.

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

# Logging verbosity: "normal" or "detailed" (for troubleshooting)
verbosity = "normal"

# Maximum log file size before rotation (in KB)
max_log_size = 512
```

## Implementation Plan

1. **`src/watch_alcatraz.sh`** — the daemon script
   - Polling loop with configurable interval
   - Reads config from `alcatrazer.toml` (`[promotion-daemon]` section)
   - Runs `git fast-export` / `git fast-import` natively on host (via `safe.directory`)
   - Pipes through identity rewrite sed
   - Handles `mirror`/`alcatraz-tree` conflict modes
   - Creates conflict resolution branches on failure (`mirror` mode)
   - Writes to `.alcatraz/promotion-daemon.log` (rotating)

2. **`src/inspect_promotion.sh`** — live log viewer (`tail -f .alcatraz/promotion-daemon.log`)

3. **Update `src/promote.sh`** — add `--namespace` flag for `alcatraz-tree` mode branch prefix support

4. **Update `alcatrazer.toml`** — add `[promotion-daemon]` section

5. **Tests**
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
- **Automatic start/stop** — daemon starts alongside `docker compose up` and stops with `docker compose down`.

## Additional Decisions

### Conflict resolution detection

In `mirror` mode, after creating a `conflict/resolve-<branch>-<timestamp>` branch, the daemon watches for that branch being deleted or merged. Once gone, the daemon resumes promoting the affected branch automatically. No user intervention needed beyond resolving the conflict itself.

### Startup guard

If `.alcatraz/workspace/.git/` does not exist when the daemon starts, it exits immediately with an informative message directing the user to run `src/initialize_alcatraz.sh` first.

### Single instance protection

The daemon writes a PID file to `.alcatraz/promotion-daemon.pid` on startup. If the PID file exists and the process is still running, the daemon exits with a message. The PID file is removed on clean shutdown.

### Prerequisites

All migrations (directory restructuring to `src/` and `container/`, `.alcatraz/workspace/` split, marks relocation, `safe.directory` setup) are done on this branch (`auto-promotion-daemon`) as initial steps before daemon implementation.

Identity leak fixes (removing alcatraz branding from inside the container) are a separate feature on a separate branch. The daemon does not depend on it — implementing it later may cause minor refactoring in the daemon but nothing blocking.

---

## Detailed Implementation Plan

Each step is one commit, small enough for human review. Dependencies flow top to bottom — each step may require previous steps to be in place.

### Phase 1: Migrations (prerequisites)

These restructure the codebase to the target layout. No new functionality — existing tests must continue to pass after each step.

**Step 1.1** — `Move scripts to src/ directory`
> `git mv scripts/promote.sh src/promote.sh`, `git mv initialize_alcatraz.sh src/initialize_alcatraz.sh`. Update all references in tests, README, docker-compose.yml. Run all tests.

**Step 1.2** — `Move Docker files to container/ directory`
> `git mv Dockerfile container/Dockerfile`, `git mv docker-compose.yml container/docker-compose.yml`, `git mv entrypoint.sh container/entrypoint.sh`. Update build context path in docker-compose.yml. Update test references. Run all tests.

**Step 1.3** — `Split .alcatraz/ into workspace/ and tool state`
> Update `src/initialize_alcatraz.sh` to create `.alcatraz/workspace/` and init git there. Update `container/docker-compose.yml` to mount `.alcatraz/workspace/`. Update `.gitignore`. Run all tests.

**Step 1.4** — `Move phantom UID from .env to .alcatraz/uid`
> Update `src/initialize_alcatraz.sh` to write UID to `.alcatraz/uid` instead of `.env`. Update `container/docker-compose.yml` to read UID from `.alcatraz/uid`. Remove `ALCATRAZ_UID` from `.env.example`. Update smoke test. Run all tests.

**Step 1.5** — `Move promotion marks from outer .git/ to .alcatraz/`
> Update `src/promote.sh` to read/write marks from `.alcatraz/promote-export-marks` and `.alcatraz/promote-import-marks`. Update promotion tests. Run all tests.

**Step 1.6** — `Add safe.directory for workspace in initialize_alcatraz.sh`
> `src/initialize_alcatraz.sh` adds `.alcatraz/workspace/` absolute path to `git config --global --add safe.directory`. Add smoke test verifying host git can read workspace. Run all tests.

### Phase 2: TOML config extension

**Step 2.1** — `Add [promotion-daemon] section to alcatrazer.toml`
> Add the `[promotion-daemon]` section with all config keys (interval, branches, mode, verbosity, max_log_size) and default values. No code reads it yet — config only.

### Phase 3: Daemon core

Each step adds one piece of daemon functionality. Tests are written alongside (or before, TDD style).

**Step 3.1** — `Daemon startup: PID guard and workspace existence check`
> `src/watch_alcatraz.sh` — script skeleton with: argument parsing, PID file write/check/cleanup on exit, `.alcatraz/workspace/.git/` existence check, trap for clean shutdown. Test: verify PID file prevents double start. Test: verify exit message when workspace missing.

**Step 3.2** — `Daemon polling loop with configurable interval`
> Add the main loop: sleep for configured interval, invoke promotion check. Reads `interval` from `alcatrazer.toml`. No actual promotion yet — just the loop structure with a placeholder. Test: verify daemon reads interval from config. Test: verify daemon responds to Ctrl+C (SIGINT/SIGTERM).

**Step 3.3** — `Daemon promotes new commits in mirror mode`
> Integrate `promote.sh` into the polling loop. On each cycle: run promotion, log result to `.alcatraz/promotion-daemon.log`. Test: seed inner repo, start daemon, verify commits appear in outer repo with rewritten identity.

**Step 3.4** — `Daemon log rotation`
> After each log write, check file size against `max_log_size`. Rotate when exceeded (move current to `.1`, start fresh). Test: write enough log entries to trigger rotation, verify old log preserved and new one started.

**Step 3.5** — `Daemon branch filtering via branches config`
> Read `branches` from config. Filter fast-export output to only include matching branches. Support `"all"`, `"main"`, and glob pattern lists. Test: seed inner repo with multiple branches, configure `branches = "main"`, verify only main is promoted. Test: glob pattern `["main", "feature/*"]`.

**Step 3.6** — `Daemon conflict detection and resolution branching in mirror mode`
> When fast-import fails on a branch, create `conflict/resolve-<branch>-<timestamp>`, log the conflict, pause promotion for that branch. Continue promoting other branches. Test: create diverged outer repo, run daemon, verify conflict branch created and other branches still promoted.

**Step 3.7** — `Daemon resumes promotion after conflict branch resolved`
> On each poll cycle, check if any paused branches have their conflict branch deleted or merged. If so, resume promotion. Test: create conflict, delete conflict branch, verify daemon resumes.

**Step 3.8** — `Daemon alcatraz-tree mode with namespace mapping`
> When `mode = "alcatraz-tree"`, promote into `alcatraz/*` namespace. Update `src/promote.sh` with `--namespace` flag. Test: configure alcatraz-tree mode, verify inner `main` becomes outer `alcatraz/main`.

### Phase 4: Inspection tool

**Step 4.1** — `Add inspect_promotion.sh for live log viewing`
> `src/inspect_promotion.sh` — thin wrapper: check log file exists, `tail -f .alcatraz/promotion-daemon.log`. Informative message if log doesn't exist yet.

### Phase 5: Documentation

**Step 5.1** — `Update README with daemon usage and new directory layout`
> Document: new `src/` and `container/` layout, `watch_alcatraz.sh` usage, `inspect_promotion.sh`, `[promotion-daemon]` config section, mirror vs alcatraz-tree modes.

**Step 5.2** — `Update plan status from Planning to Complete`
> Mark this document as implemented. Add any notes from implementation experience.