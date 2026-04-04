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
- **Nothing from Alcatrazer may exist inside `.alcatraz/`** — no hooks, no config files, no markers
- **Nothing from Alcatrazer may be visible in the container environment** — no branded env vars, no identifiable process names
- **The daemon must operate entirely from the host side** — it observes the workspace from outside, never touches the inside

---

## Feature Description

The auto-promotion daemon is a background process that watches the inner git repository (`.alcatraz/`) for new commits and automatically promotes them to the outer git repository using `promote.sh`. This eliminates the need for the human operator to manually run the promotion script — agent work appears in the outer repo in near real-time as it is produced.

The daemon runs on the **host** side (not inside the Docker container), watching the `.alcatraz/.git/` directory for changes. When it detects new commits, it runs the promotion pipeline (fast-export | identity rewrite | fast-import) to transfer them to the outer repo.

## Origin

From the initial design discussion:

> "What if after installation tool runs in background and decides by itself when to export git 'new increment' from within docker and pull it into outside-docker git."

The user's vision is that `promote.sh` should not be a manual step. The daemon should autonomously decide when to promote, making the experience seamless: agents commit inside Docker, and the human sees those commits appear in their real repo automatically.

## What We Know

### Existing Infrastructure

- `promote.sh` already works with incremental promotion via fast-export/fast-import mark files
- Identity rewrite is handled (agent identity -> real identity from `alcatrazer.toml`)
- The priority chain for author identity is: git config < alcatrazer.toml < CLI flags
- `.alcatraz/` is the inner workspace, mounted into Docker
- Files in `.alcatraz/` are owned by the phantom UID (not writable by host user, but readable)
- The host cannot run `git` commands directly against `.alcatraz/` due to "dubious ownership" — promotion must handle this

### Promotion Behavior

- `promote.sh` supports `--dry-run` to check for pending commits without modifying anything
- Mark files (`promote-export-marks`, `promote-import-marks`) are stored in the target repo's `.git/` directory
- Promotion is idempotent — running it when there's nothing new is a no-op
- Full branch and merge topology is preserved

## Design Decisions

### 1. What triggers promotion?

~~**Git hook inside container**~~ — **ELIMINATED by Principle 2**. A `post-commit` hook in `.alcatraz/.git/hooks/` would leave an alcatrazer fingerprint inside the workspace. Agents could read the hook, see what it does, and trace it back to the tool.

Remaining options:

- **File watcher (inotifywait)** — watches `.alcatraz/.git/refs/` for changes from the host side. Immediate reaction, low overhead. Requires `inotify-tools` on Linux. No footprint inside the container. On macOS, would use `fswatch` instead.
- **Polling** — runs promotion check on an interval (e.g. every 5-10 seconds) from the host side. Simpler, no extra dependencies, slightly delayed. No footprint inside the container.

Both options satisfy Principle 2 — they observe from outside, never touch inside.

**Recommendation:** Start with **polling** for simplicity and zero dependencies. Add inotifywait/fswatch support later as an optimization. The interval is configurable via `alcatrazer.toml`.

### 2. What gets promoted and when?

Options:

- **Every commit on every branch** — most granular, human sees everything in real time
- **Only merges to main** — cleaner outer history, but delays visibility of in-progress work
- **Configurable** — let the user choose via `alcatrazer.toml`

**Recommendation:** **Configurable**, defaulting to **every commit on every branch**. Full visibility by default, user can restrict if they prefer cleaner history.

### 3. Where does the daemon run?

~~**Inside the same container**~~ — **ELIMINATED by Principle 1 and 2**. Would require mounting the outer `.git/` into the container, breaking isolation. Agents could access the outer repo's config, history, and real identity.

Remaining options:

- **Host process** — simplest, direct access to both repos. A bash script with a loop, or a lightweight binary. No extra Docker complexity.
- **Separate Docker container** — more portable, but needs access to both `.alcatraz/` and `.git/` of the outer repo, complicating volume mounts. Also introduces a second container to manage.

**Recommendation:** **Host process**. It's simplest, has direct access to the outer repo (owned by the host user), and only needs to read `.alcatraz/` (which is world-readable despite phantom UID ownership). The daemon is a host-side tool — it belongs on the host.

### 4. How does the daemon handle the "dubious ownership" problem?

The `.alcatraz/` directory is owned by the phantom UID. The host user cannot run `git` commands against it directly (git refuses with "dubious ownership").

Options:

- **Run `git fast-export` via a brief Docker invocation** — uses the same container image, runs as the phantom UID that owns the files. Most secure — no weakening of git's ownership checks. Adds ~1 second overhead per promotion cycle for container startup.
- **Use `git config --global safe.directory`** — tells git on the host to trust `.alcatraz/`. Weakens security posture by disabling a git safety check. Simple but goes against Principle 1.

**Recommendation:** **Docker invocation for fast-export**. Run `docker compose run --rm alcatraz git fast-export ...` to extract the stream, then pipe it through sed (on host) into `git fast-import` (on host, targeting outer repo). This keeps git's ownership checks intact and uses the infrastructure we already have.

### 5. What should the daemon output?

- **Silent by default** — log to a file, no terminal noise
- **Status updates on events** — print a line when commits are promoted
- **Verbose mode** — detailed logging for debugging

**Recommendation:** **Status updates on events** by default (one line per promotion: timestamp, commit count, branches). Verbose mode via `--verbose` flag. Log to stdout (user can redirect if they want a file).

### 6. How is the daemon started/stopped?

- **Manual** — `alcatrazer watch` (or `./scripts/watch.sh`) to start, Ctrl+C to stop
- **Automatic** — started alongside `docker compose up`

**Recommendation:** **Manual start** for now. The daemon is a host-side process, so tying it to `docker compose up` is not straightforward. A simple script that the user runs in a terminal tab. Future: could be a docker compose service with host networking.

### 7. What about conflicts?

The promotion is unidirectional (inner -> outer). The outer repo should never have commits that don't come from promotion. But if the human manually commits to the outer repo between promotions, the fast-import could conflict.

- **Separate branch namespace** — promote into `alcatraz/*` branches (e.g. `alcatraz/main`, `alcatraz/feature/auth`). The outer repo's own `main` stays untouched. Human merges from `alcatraz/*` when ready. This is the safest option and makes the boundary explicit.
- **Detect and warn** — check if outer repo has unpromoted commits, warn and skip if diverged.
- **Always force** — overwrite promoted branches. Risky if human has local work.

**Recommendation:** **Separate branch namespace** (`alcatraz/*`). This eliminates conflict entirely — promotion writes to its own namespace, human merges when ready. It also makes it clear in the outer repo which commits came from agents.

### 8. Configuration in alcatrazer.toml

```toml
[daemon]
# Polling interval in seconds
interval = 5

# What gets promoted: "all" (every commit on every branch) or "main" (only main branch)
promote = "all"

# Branch namespace prefix in the outer repo
namespace = "alcatraz"

# Output
log = ".alcatraz/daemon.log"
verbose = false
```

## Implementation Plan

1. **`scripts/watch.sh`** — the daemon script
   - Polling loop with configurable interval
   - Reads config from `alcatrazer.toml`
   - Runs `git fast-export` via Docker to handle ownership
   - Pipes through identity rewrite sed
   - Runs `git fast-import` on host targeting outer repo
   - Writes to `alcatraz/*` branch namespace
   - Status output on promotion events

2. **Update `promote.sh`** — add `--namespace` flag for branch prefix support

3. **Update `alcatrazer.toml`** — add `[daemon]` section

4. **Tests**
   - Test daemon detects new commits and promotes them
   - Test branch namespace mapping (inner `main` -> outer `alcatraz/main`)
   - Test idempotency (running when nothing new is a no-op)
   - Test daemon handles container not running gracefully

## Dependencies and Constraints

- Must not require root access on the host
- Must handle the phantom UID ownership boundary via Docker invocation
- Must be idempotent (safe to restart, safe to run multiple instances)
- Must not interfere with agents working inside the container
- Must leave zero footprint inside `.alcatraz/` (Principle 2)
- Must work on Linux (primary) and macOS (secondary)
- Should add minimal dependencies to the host system

## Open Items

- [ ] Decide: should `promote.sh` be refactored to support namespaced branches, or should `watch.sh` handle the namespace mapping separately?
- [ ] Decide: should the daemon auto-stop when the container stops?
- [ ] Consider: graceful handling of Docker not running / container not built
- [ ] Consider: should the daemon show a summary on Ctrl+C (total commits promoted, duration, etc.)?