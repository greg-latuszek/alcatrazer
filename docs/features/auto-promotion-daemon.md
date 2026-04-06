# Auto-Promotion Daemon

## Status: Complete

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
├── initialize_alcatraz.sh      <-- bash (bootstrap — runs before Python exists)
├── resolve_python.sh           <-- bash (finds/installs Python 3.11+)
├── watch_alcatraz.py           <-- Python (daemon)
├── inspect_promotion.py        <-- Python (daemon, future)
└── promote.py                  <-- Python (promotion pipeline)

container/                      <-- Docker infrastructure
├── Dockerfile
├── docker-compose.yml
└── entrypoint.sh
```

Other migrations (all completed in Phase 1):
- `docker-compose.yml`: mount `.alcatraz/workspace/` instead of `.alcatraz/`, read UID from `.alcatraz/uid.env`
- `initialize_alcatraz.sh`: create `workspace/` subdirectory, write UID to `.alcatraz/uid`, add `safe.directory`, resolve Python 3.11+
- `promote.py`: marks stored in `.alcatraz/` (not target `.git/`), `--marks-dir` flag for testing
- `.env`: API keys only — `ALCATRAZ_UID` moved to `.alcatraz/uid`

---

## Feature Description

The auto-promotion daemon is a background process that watches the inner git repository (`.alcatraz/workspace/`) for new commits and automatically promotes them to the outer git repository using `promote.py`. This eliminates the need for the human operator to manually run the promotion script — agent work appears in the outer repo in near real-time as it is produced.

The daemon runs on the **host** side (not inside the Docker container), watching `.alcatraz/workspace/.git/` for changes. When it detects new commits, it runs the promotion pipeline (fast-export | identity rewrite | fast-import) to transfer them to the outer repo.

## Origin

From the initial design discussion:

> "What if after installation tool runs in background and decides by itself when to export git 'new increment' from within docker and pull it into outside-docker git."

The user's vision is that promotion should not be a manual step. The daemon should autonomously decide when to promote, making the experience seamless: agents commit inside Docker, and the human sees those commits appear in their real repo automatically.

## What We Know

### Existing Infrastructure

- `promote.py` handles incremental promotion via fast-export/fast-import mark files
- Identity rewrite is handled via regex (agent identity -> real identity from `alcatrazer.toml`)
- The priority chain for author identity is: git config < alcatrazer.toml < CLI flags
- `.alcatraz/workspace/` is the inner workspace, mounted into Docker
- Files in `.alcatraz/workspace/` are owned by the phantom UID (not writable by host user, but readable)
- Tool state (UID, marks, logs) lives under `.alcatraz/` but outside `workspace/` — host-only, never visible to agents
- The host adds `.alcatraz/workspace/` as `safe.directory` so git can operate on it despite phantom UID ownership

### Promotion Behavior

- `promote.py` supports `--dry-run` to check for pending commits without modifying anything
- Mark files (`promote-export-marks`, `promote-import-marks`) live in `.alcatraz/`
- Promotion is idempotent — running it when there's nothing new is a no-op
- Full branch and merge topology is preserved

## Design Decisions

### 1. What triggers promotion?

**Polling** — runs promotion check on a configurable interval (default 5 seconds) from the host side. No external dependencies, no footprint inside the container, satisfies Principle 2. The interval is configurable via `alcatrazer.toml`.

### 2. Which branches get promoted?

Configurable via `alcatrazer.toml`. The user chooses which branches cross the water:

- `"all"` — every branch, full git tree with all work-in-progress visible (default)
- A branch name (e.g. `"main"` or `"master"`) — only that branch, just the final merged results
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
#   A branch name — only that branch, e.g. "main" or "master"
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

## Dependencies and Constraints

- Must not require root access on the host
- Must handle the phantom UID ownership boundary via `safe.directory`
- Must be idempotent (safe to restart, safe to run multiple instances)
- Must not interfere with agents working inside the container
- Must leave zero footprint inside `.alcatraz/workspace/` (Principle 2)
- Tool state (marks, logs) lives under `.alcatraz/` but outside `workspace/`
- Must work on Linux (primary) and macOS (secondary)
- Daemon requires Python 3.11+ (for `tomllib`); resolved during init with four-tier fallback
- Init and promote scripts remain bash-only (no Python dependency)
- Daemon uses Python stdlib only — no pip packages required

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

**TDD discipline:** Starting from Phase 2 onward, each step follows the RED/GREEN/BLUE cycle where possible:
- `[RED]` commit — failing test for the planned functionality
- `[GREEN]` commit — implementation that makes the test pass
- `[BLUE]` commit — improvements/cleanup if applicable

Phase 1 (migrations) is exempt — it works with already existing tests that must keep passing after each migration step.

### Phase 1: Migrations (prerequisites) ✅

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

### Phase 2: TOML config extension ✅

**Step 2.1** — `Add [promotion-daemon] section to alcatrazer.toml` ✅
> Added the `[promotion-daemon]` section with all config keys (interval, branches, mode, verbosity, max_log_size) and default values.

### Phase 2.5: Python resolution and migration ✅

All tool code beyond bootstrap is Python (stdlib only — `tomllib`, `pathlib`, `subprocess`, `re`, `threading`). Only `initialize_alcatraz.sh` and `resolve_python.sh` remain bash (they run before Python exists).

Python 3.11+ is required (for `tomllib`). The resolution happens during `initialize_alcatraz.sh` with a four-tier fallback:

1. Detect `python3` on PATH that is 3.11+ → use it (pyenv/mise shims resolved via `sys.executable`)
2. Detect `mise` → offer to install Python 3.11 via mise
3. No mise → offer to install mise (single curl, no root), then Python 3.11 via mise
4. User declines everything → ask for manual path to Python 3.11+

The resolved interpreter is stored as `.alcatraz/python` — a **symlink** to the real binary. The daemon is invoked directly: `.alcatraz/python src/watch_alcatraz.py`.

**Step 2.5.1** — `Add Python 3.11+ resolution to initialize_alcatraz.sh` ✅
> `src/resolve_python.sh`: four-tier Python detection, shim resolution via `sys.executable`, stores result as `.alcatraz/python` symlink. Called by `initialize_alcatraz.sh` as Step 5. 9 unittest tests.

**Step 2.5.2** — `Rewrite daemon and promote in Python` ✅
> `src/watch_alcatraz.py`: PID guard, workspace check, signal handling via `threading.Event`, `tomllib` config. Direct entry point (no bash wrapper). 13 unittest tests.
>
> Beyond-plan refactoring done during this step:
> - `src/promote.sh` → `src/promote.py` (argparse, tomllib, regex identity rewrite)
> - `.alcatraz/python` changed from text file to symlink (simpler wrapper, then wrapper removed entirely)
> - `test/` renamed to `tests/` (Python stdlib `test` package conflict)
> - All bash tests migrated to Python unittest (46 tests total)
> - Promotion tests call `promote.py` functions directly instead of subprocess
> - Added unit tests for `rewrite_identity` and `resolve_identity` with mocking

### Phase 3: Daemon core ✅

**Step 3.1** — ~~`Daemon startup`~~ ✅ (PID guard, workspace check, signal handling, config loading)

**Step 3.2** — ~~`Daemon polling loop`~~ ✅ (`threading.Event.wait(timeout=interval)` loop, config tests)

**Step 3.3** — `Daemon promotes new commits in mirror mode` ✅
> Calls `promote.promote()` directly each cycle, logs to `.alcatraz/promotion-daemon.log`.

**Step 3.4** — `Daemon log rotation` ✅
> `RotatingFileHandler` rotates at `max_log_size` KB, keeps one backup.

**Step 3.5** — `Daemon branch filtering` ✅
> `resolve_branches()` supports `"all"`, `"main"`, and glob lists via `fnmatch`.

**Step 3.6** — `Conflict detection and resolution branching` ✅
> Tracks promoted tips in `promoted-tips.json`. Diverged branches get `conflict/resolve-*` branches. Paused branches persisted in `paused-branches.json`.

**Step 3.7** — `Resume after conflict resolved` ✅
> Checks if conflict branches have been deleted. Unpauses and updates tips. State persists across daemon restarts.

**Step 3.8** — `alcatraz-tree mode with namespace mapping` ✅
> `rewrite_refs()` prefixes branch names in fast-export stream. No conflict handling needed.

### Phase 4: Inspection tool ✅

**Step 4.1** — `Add inspect_promotion.py` ✅
> Tails `.alcatraz/promotion-daemon.log`. Helpful message if log doesn't exist yet.

### Phase 5: Documentation ✅

**Step 5.1** — `Update README` ✅
> Full rewrite: new directory layout, Python requirement, daemon usage, config reference, promotion modes, conflict resolution, branch filtering, manual promotion, running tests.

**Step 5.2** — `Mark plan complete` ✅

### Implementation Notes

Deviations from original plan discovered during implementation:
- **Python for all tools** — `promote.sh` was rewritten to `promote.py` (not planned, but fragile bash TOML parsing forced the move). Only `initialize_alcatraz.sh` and `resolve_python.sh` stay bash.
- **No bash wrapper** — `watch_alcatraz.sh` was created then removed. The daemon is invoked directly via `.alcatraz/python src/watch_alcatraz.py`.
- **Symlink** — `.alcatraz/python` is a symlink to the resolved interpreter, not a text file containing a path. Simpler invocation.
- **test/ → tests/** — renamed to avoid conflict with Python's stdlib `test` package.
- **All tests in Python** — bash tests migrated to `unittest`. 65 tests total covering promotion, daemon, Python resolution, and inspection tool.
- **Conflict detection via tips tracking** — instead of catching fast-import failures (original plan), we track promoted branch tips in JSON and detect divergence proactively. Cleaner than error-based detection.