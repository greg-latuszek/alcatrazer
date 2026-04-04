# Auto-Promotion Daemon

## Status: Planning

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
- Identity rewrite is handled (Alcatraz Agent -> real identity from `alcatrazer.toml`)
- The priority chain for author identity is: git config < alcatrazer.toml < CLI flags
- `.alcatraz/` is the inner workspace, mounted into Docker
- Files in `.alcatraz/` are owned by the phantom UID (not writable by host user, but readable)
- The host cannot run `git` commands directly against `.alcatraz/` due to "dubious ownership" — promotion must handle this

### Promotion Behavior

- `promote.sh` supports `--dry-run` to check for pending commits without modifying anything
- Mark files (`promote-export-marks`, `promote-import-marks`) are stored in the target repo's `.git/` directory
- Promotion is idempotent — running it when there's nothing new is a no-op
- Full branch and merge topology is preserved

### Key Design Questions

#### 1. What triggers promotion?

Options:
- **File watcher (inotifywait)** — watches `.alcatraz/.git/refs/` for changes. Immediate reaction, low overhead, but requires `inotify-tools` on the host.
- **Polling** — runs `promote.sh --dry-run` on an interval (e.g. every 5-10 seconds). Simpler, no extra dependencies, slightly delayed.
- **Git hook inside container** — a `post-commit` hook in `.alcatraz/.git/hooks/` that signals the daemon. Most responsive, but the hook runs as the phantom UID inside Docker.

#### 2. What gets promoted and when?

Options:
- **Every commit on every branch** — most granular, human sees everything in real time
- **Only merges to main** — cleaner outer history, but delays visibility of in-progress work
- **Configurable** — let the user choose via `alcatrazer.toml`

#### 3. Where does the daemon run?

Options:
- **Host process** — simplest, direct access to both repos. Could be a bash script with a loop, or a lightweight binary.
- **Separate Docker container** — more portable, but needs access to both `.alcatraz/` and `.git/` of the outer repo, complicating volume mounts.
- **Inside the same container** — would need access to the outer repo, which breaks the isolation model.

#### 4. How does the daemon handle the "dubious ownership" problem?

The `.alcatraz/` directory is owned by the phantom UID. The host user cannot run `git` commands against it directly. Options:
- Run `git fast-export` via Docker (same container image, brief invocation)
- Use `git config --global safe.directory` for `.alcatraz/` on the host (weakens security posture but practical)
- Run the daemon itself inside a Docker container that has both repos mounted

#### 5. What should the daemon output?

- **Silent by default** — log to a file, no terminal noise
- **Status line** — show a live status: `[alcatraz] 3 commits promoted, 2 branches active`
- **Notifications** — desktop notifications on promotion events

#### 6. How is the daemon started/stopped?

- **Manual** — `alcatrazer watch` to start, Ctrl+C to stop
- **Automatic** — started by `docker compose up`, stopped by `docker compose down`
- **Systemd/launchd** — registered as a user service for persistent background operation

#### 7. What about conflicts?

The promotion is unidirectional (inner -> outer). The outer repo should never have commits that don't come from promotion. But if the human manually commits to the outer repo between promotions, the fast-import could conflict. Options:
- Detect and warn (don't promote if outer has diverged)
- Always force (outer repo's promoted branches are overwritten)
- Separate branch namespace in outer repo (e.g. `alcatraz/main` instead of `main`)

#### 8. Configuration in alcatrazer.toml

Potential new section:

```toml
[daemon]
# What triggers promotion
mode = "poll"           # "poll", "watch", or "hook"
interval = 5            # seconds (for poll mode)

# What gets promoted
promote = "all"         # "all" (every commit), "main-only" (only merges to main)

# Output
log = ".alcatraz/daemon.log"
verbose = false
```

## Dependencies and Constraints

- Must not require root access on the host
- Must handle the phantom UID ownership boundary
- Must be idempotent (safe to restart, safe to run multiple instances)
- Must not interfere with agents working inside the container
- Must work on Linux (primary) and macOS (secondary)
- Should add minimal dependencies to the host system

## Open Items

- [ ] Decide on trigger mechanism (poll vs watch vs hook)
- [ ] Decide on promotion scope (all commits vs main-only vs configurable)
- [ ] Decide on daemon hosting (host process vs container)
- [ ] Decide on dubious ownership handling
- [ ] Decide on output/logging approach
- [ ] Decide on start/stop mechanism
- [ ] Decide on conflict handling
- [ ] Design alcatrazer.toml [daemon] section
- [ ] Consider: should the daemon also run `initialize_alcatraz.sh` if `.alcatraz/` doesn't exist?
- [ ] Consider: should the daemon auto-start when `docker compose up` is run?