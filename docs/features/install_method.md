# Alcatrazer Installation & Operation

## Status: Ready for implementation

## Goal

A simple one-liner that installs Alcatrazer into any existing git repository.
The user runs one command, answers a few questions, and gets a working Alcatraz
environment — no manual file copying, no cloning, no reading setup docs.

After installation, daily operation is two commands: `start` and `stop`.

---

## Target Repo Layout After Installation

```
target-repo/
├── .git/
│   └── info/
│       └── exclude              <-- alcatrazer patterns (NOT in working tree)
├── coding-environment.toml      <-- version controlled, zero alcatrazer branding
├── .env.example                 <-- template for API keys (standard pattern)
├── .alcatrazer/                 <-- gitignored via .git/info/exclude
│   ├── config.toml              <-- alcatrazer config (promotion, daemon, pointer)
│   ├── Dockerfile               <-- generated from coding-environment.toml
│   ├── entrypoint.sh            <-- copied from package template (byte-exact)
│   ├── coding-environment.toml.last  <-- copy from last successful build
│   ├── uid                      <-- phantom UID
│   ├── agent-identity           <-- randomly generated name + email
│   ├── workspace-dir            <-- name of the workspace directory
│   ├── python -> ...            <-- symlink to resolved Python 3.11+
│   ├── promote-export-marks
│   ├── promote-import-marks
│   ├── src/alcatrazer/          <-- full package tree from PyPI wheel
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   ├── promote.py
│   │   ├── container/           <-- Docker templates (Dockerfile, entrypoint.sh)
│   │   ├── scripts/             <-- bash bootstrap (resolve_python.sh)
│   │   ├── templates/           <-- config templates
│   │   └── tests/               <-- bundled test suite
│   └── ... (logs, PID, etc.)
└── .<workspace>/                <-- gitignored via .git/info/exclude, randomly named
    ├── .git/                    <-- inner git (random agent identity, no remote)
    └── ... agent work ...
```

**Principle 2 compliance:** Nothing in the working tree reveals Alcatrazer.
`coding-environment.toml` has zero branding. `.env.example` is a standard pattern.
All alcatrazer-specific artifacts are in `.alcatrazer/` (gitignored via
`.git/info/exclude`, not `.gitignore` — so agents never see the ignore patterns either).

See [alcatraz_how_and_what_for.md](alcatraz_how_and_what_for.md) "Resolved: Config Split"
for the full reasoning behind this architecture.

---

## Vocabulary — naming the actors

Three concepts recur across this doc, the user-facing CLI strings, and the
code. Consistent names reduce drift between layers:

| Concept | User-facing phrase | Code / logs / config |
|---|---|---|
| The developer's outer git — the repo they `git clone`'d | **your repository** | `outer_repo`, `target_repo` |
| The inner git at `project_dir/<workspace-name>/` where AI agents commit | **Alcatraz workspace** | `workspace`, `inner_repo`, `source_repo` |
| The host-side background process that mirrors inner commits outward | **sync daemon** (verb: **sync**) | `daemon`, `promotion-daemon`, `promote()` |

The historical name `promotion` survives in the code, config sections
(`[promotion]`, `[promotion-daemon]`), and file paths (`promotion-daemon.log`,
`promotion-daemon.pid`) — renaming those is churn for no user-visible gain.
User-facing text says "sync" because that's what the user perceives happening
and they won't cross-reference with code.

---

## CLI: Five Commands

**Design goal:** Minimalism. Developers are tired of learning new tools. Alcatrazer
should be operable and invisible — as few commands as possible, no Docker vocabulary
leaking through (no `up`/`down`/`build`/`compose`).

One deliberate exception to "no separate init": **`alcatrazer init` exists**. Rationale
below in its section — in short, the first-run flow has a credentials gate (Claude
token in `.env` or host `~/.claude/.credentials.json`) that the user must fill in
**between** disk setup and container start. Smashing both into a single `start`
call either starts the container with no AI auth (the agent is dead on arrival) or
blocks in stdin asking the user to go edit `.env` while `start` is mid-flight. A
named command per phase maps 1:1 to the two decisions the user actually makes:
"configure this repo" vs. "run the sandbox".

### The commands

```
alcatrazer init                      one-time: wizards → config + recipe on disk
alcatrazer start                     daily: build (if needed) + start + run startup
alcatrazer stop                      freeze the Alcatraz (writable layer preserved)
alcatrazer clear                     throw away the Alcatraz (next start recreates)
alcatrazer upgrade                   check for and install new alcatrazer version
```

Everything else is a flag on `start` or `upgrade`:

```
alcatrazer start --run-selftest      also run security self-tests after starting
alcatrazer start --verify-checksum   also verify installed source against GitHub
alcatrazer start --rebuild           force full rebuild even if nothing changed
alcatrazer upgrade --dry-run         check for new version without installing
```

### `alcatrazer init` — one-time setup

Runs the interactive wizards (promotion identity, languages, OS packages, startup
commands), writes `coding-environment.toml` + `.alcatrazer/config.toml` +
`.env.example`, extracts the package source into `.alcatrazer/src/`, generates
`.alcatrazer/Dockerfile` + `entrypoint.sh`, records the workspace dir name, and
appends patterns to `.git/info/exclude`. **Stops before `docker build`.** No image
is produced, no container is started.

At the end it prints a guidance block about AI credentials: if
`~/.claude/.credentials.json` is not present on the host, it tells the user to
either run `claude` on the host to authenticate, or populate `ANTHROPIC_API_KEY`
in a `.env` file at the repo root. This is the user-action gate that justifies
the split — without it, `alcatrazer start` would silently launch a Claude-less
container.

`.env.example` handling:
- No existing `.env.example` in the repo → write a fresh one with commented
  `ANTHROPIC_API_KEY=` placeholder plus explanation.
- Existing `.env.example` → append an alcatrazer block bracketed by marker
  comments (idempotent on re-run; leaves the user's existing content untouched).

Re-running `alcatrazer init` on an already-initialized repo is not a daily
operation; the user's daily tool is `alcatrazer start`. A future "change the
coding environment" flow edits `coding-environment.toml` directly and lets
`start`'s drift detection do the rest (Step 4).

### `alcatrazer start` — does the right thing

`start` assumes `alcatrazer init` has already run. It detects the current state
and does what's needed (build + run on first call, restart / rebuild / no-op
from there).

**Detection uses two comparisons:**
1. Generate would-be Dockerfile in memory, compare against existing `.alcatrazer/Dockerfile`
   → detects `[os]` or `[languages]` changes (rebuild needed)
2. Compare `coding-environment.toml` against `.alcatrazer/coding-environment.toml.last`
   → detects `[startup]` changes (restart needed, no rebuild)

```
alcatrazer start
       │
       ├── no .alcatrazer/ ?
       │       → error: "no alcatrazer setup here — run `alcatrazer init` first."
       │         (exit 1; no wizard, no magic first-run)
       │
       ├── image NOT built yet (first start after init) ?
       │       → build image → create workspace snapshot → start
       │         → run startup commands
       │
       ├── container NOT running
       │       │
       │       ├── would-be Dockerfile differs from existing?
       │       │       → rebuild image, start, run startup commands
       │       │
       │       └── Dockerfile identical?
       │               → start container, run startup commands
       │
       └── container IS running
               │
               ├── would-be Dockerfile differs?
               │       → rebuild image, restart, run startup commands
               │
               ├── coding-environment.toml differs from .last?
               │   (Dockerfile same — only [startup] changed)
               │       → restart container, re-run startup commands
               │
               └── both identical?
                       → "Already running, environment up to date."
```

**Why two comparisons:** Since `[startup]` commands don't go into the Dockerfile (they
run at container start via a post-start script), changing only `[startup]` won't change
the generated Dockerfile. The `.last` comparison catches what the Dockerfile comparison
misses. Together they cover all toml changes exactly — no heuristics, no guessing.

**What is NOT auto-detected:** Changes to external scripts referenced by `[startup]`
commands (e.g., `"bash scripts/setup-bmad.sh"` where the script was edited but the toml
line didn't change). Detecting this would require parsing bash commands to find file
references — fragile and unreliable. Instead: `stop` + `start` re-runs all startup
commands, which picks up external script changes. Simple, predictable, no magic.

After each successful build, the current `coding-environment.toml` is copied to
`.alcatrazer/coding-environment.toml.last` as a record of what the last build used.

**Sync daemon launch.** After the Alcatraz is up (first-run branch, resume,
or any case where the container transitions to running), `alcatrazer start`
launches the sync daemon in the background and prints a transparency block:

```
Sync daemon started.
  What:   one-way sync from the Alcatraz workspace → your repository
          (commits in .devspace-7f3a/ → commits in /home/you/your-repo)
  When:   polls every 5s (see .alcatrazer/config.toml [promotion-daemon])
  PID:    41287  (written to .alcatrazer/promotion-daemon.pid)
  Logs:   .alcatrazer/promotion-daemon.log

One-way: agent commits flow OUT, your commits do NOT flow in.
Your repository's default branch was snapshotted into the Alcatraz
workspace ONCE at creation (flat, no history). To replay a fresh
snapshot after external changes, use `alcatrazer clear` + `start`.
```

On the fast path (`Already running, environment up to date.`) where the
daemon is already alive, the CLI emits a single line instead:
`Sync daemon already running (PID 41287).` — self-heals by relaunching
silently if a stale PID file points at a dead process.

### `alcatrazer stop` — freeze the Alcatraz

Stop the Alcatraz without throwing it away. The container's writable overlay
layer (and thus its caches — mise, pip, npm — see "Ephemeral caches" below)
survives. A subsequent `alcatrazer start` with no config changes resumes
from exactly where you left off.

**Daemon interaction.** Before the container is stopped would reopen a
race (agent commits during daemon sync), so the ordering is:
1. `docker stop` — freezes agents.
2. Signal the sync daemon (SIGTERM). Daemon runs one final sync against
   the now-frozen inner repo, logs the result, exits.
3. CLI tails the last lines of the daemon log to surface "synced N
   commits" or conflict info to the user.

If the final sync hits a conflict, `stop` exits non-zero — the container
stays stopped, daemon is gone, unsynced commits remain in the Alcatraz
workspace (which is on the host filesystem, preserved across docker
lifecycle). User resolves the conflict in their repository; the next
`alcatrazer start` relaunches the daemon, which picks up the paused
branches automatically.

No flags.

### `alcatrazer clear` — throw away the Alcatraz

Remove the container entirely. The writable layer and its caches are
discarded. The image stays; `.alcatrazer/` config stays. Next
`alcatrazer start` hits the "fresh start" branch of the lifecycle table
(Step 4) and recreates the container from the existing image — no
rebuild needed, caches populated fresh from scratch.

Typical use: "my container state got weird, but I don't want to re-init
the repo." For a fully fresh image too, follow with
`alcatrazer start --rebuild`.

**Daemon interaction.** Same three-step dance as `stop` before the
container is removed:
1. `docker stop` (agents frozen).
2. SIGTERM the sync daemon, which does a final sync + exits.
3. `docker rm` the container.

**The Alcatraz workspace (inner repo) is preserved on the host** across
`clear`. Only the sandbox runtime (container + writable overlay layer)
goes away. This is the design guarantee that no agent work is lost: even
after `clear`, every commit the agents produced has either (a) been
synced to your repository, or (b) still lives in
`project_dir/<workspace-name>/` awaiting conflict resolution.

No flags. Idempotent (no container present = no-op). Does NOT remove
`.alcatrazer/` config, does NOT remove the Alcatraz workspace, does NOT
touch user-owned repo-root files (`coding-environment.toml`, `.env`,
`.env.example`). A future `alcatrazer reset --workspace` (out of scope
for v1) would be the "destroy everything, start fresh snapshot" verb.

### Sync daemon — background promotion

Not a CLI command. A host-side background process (`alcatrazer.daemon`)
that `alcatrazer start` spawns and `alcatrazer stop` / `alcatrazer clear`
signals to exit. It polls the Alcatraz workspace every N seconds
(configurable in `.alcatrazer/config.toml [promotion-daemon]`), runs
`promote()` to replay any new commits into your repository with the
promotion identity (rewriting author/committer), and handles conflicts
by surfacing `conflict/resolve-*` branches in your repository.

**Key property: daemon is host-side.** It runs outside docker, reads the
inner git via the bind-mounted host path (not through docker), and
survives any docker lifecycle event except an explicit CLI shutdown.
This is what makes the ordering rule "docker down FIRST, daemon
finalizes AFTER" possible: daemon doesn't depend on docker being alive
to read the inner repo.

**Lifecycle contract:**

| When | What happens to the daemon |
|---|---|
| `alcatrazer start` (container up) | Daemon spawned, polls indefinitely. |
| `alcatrazer start` fast path | CLI checks PID file; if daemon alive, no-op; if dead, relaunches (self-heal). |
| `alcatrazer stop` | CLI stops docker, then SIGTERMs daemon. Daemon runs one final sync against the now-frozen inner repo, exits. |
| `alcatrazer clear` | Same as stop, then `docker rm`. Inner repo preserved. |
| Daemon crashed mid-run | Next `alcatrazer start` detects stale PID file and relaunches. |
| Conflict during final sync | Daemon logs the conflict, exits. CLI surfaces the conflict line from the log; user resolves in their repository; next `start` relaunches daemon, which auto-resumes paused branches. |

**Why daemon, not CLI, owns the final sync:** all sync machinery
(conflict detection, paused-branches state, marks-file updates) already
lives in the daemon's promote path. Having the CLI run a separate final
sync would duplicate that code — same class of bug that caused the
Steps 6a–e daemon drift. Instead, the CLI signals and tails the log.

**Graceful vs. unexpected shutdown — `.alcatrazer/state.json`.** SIGTERM
alone can't tell the daemon whether its shutdown was triggered by
`alcatrazer stop` / `clear` (expected — docker is stopped, final sync
is safe) or by something unrelated (user `kill <pid>`, laptop suspend,
systemd shutdown, OOM). The CLI signals its intent via a single field
in `.alcatrazer/state.json`:

```json
{ "schema_version": 1, "daemon_shutdown": "requested" }
```

`cmd_stop` / `cmd_clear` write `"requested"` before sending SIGTERM and
update to `"done"` after observing the daemon exit. The daemon reads
the field once in its shutdown path and picks its log prefix:

- `"requested"` → `Final sync (graceful shutdown): N commits synced`
- anything else (absent, `"done"` from a previous cycle, corrupt file,
  unknown value) → `Final sync (unexpected shutdown): N commits synced`

**Behavior does not differ between the two cases** — the daemon always
attempts a final sync either way; only the log line differs. Same
eventual-consistency guarantee holds (any commit missed by a racy
unexpected-shutdown final sync is caught on the daemon's first poll
after the next `alcatrazer start`).

This is also the seed of the future **filesystem + infocenter**
abstraction (see `refactor_for_infocenter.md`). `state.json` is the
file that composite queries will migrate into over time; today it
carries exactly one cooperation flag, deliberately scoped small.

### `alcatrazer upgrade` — self-update

Checks PyPI for a newer version of alcatrazer and installs it into `.alcatrazer/`.

```
$ alcatrazer upgrade
Current: 0.2.0
Latest:  0.3.1
Upgrading... done.
Run 'alcatrazer start --rebuild' to apply changes to the container.

$ alcatrazer upgrade --dry-run
Current: 0.2.0
Latest:  0.3.1
Run 'alcatrazer upgrade' to install.
```

Per-repo upgrade — consistent with per-repo install. Each repo controls its own version.

### `--run-selftest` — verify security properties

Runs the bundled test suite that verifies: phantom UID isolation, no credential leaks,
no Docker socket, no git remotes, identity rewriting, file ownership.

Named `--run-selftest` (not `--test`) to make clear this tests alcatrazer itself,
not the target repository's test suite.

### `--verify-checksum` — verify source integrity

Verifies installed source code against `SHA256SUMS` from GitHub Releases.
A convenience — the user can always do this manually with `sha256sum -c`.

### What the user experiences

**First time:**
```
$ alcatrazer init
Starting interactive setup...

What languages does this project use?
> Python 3.12

Package manager? [pip] / uv / poetry
> uv

... (questions about promotion identity, etc.) ...

Generated: coding-environment.toml
Generated: .alcatrazer/config.toml
Written:   .env.example (ANTHROPIC_API_KEY placeholder)
Written:   .git/info/exclude
Generated: .alcatrazer/Dockerfile + entrypoint.sh

AI credentials: ~/.claude/.credentials.json not found.
  Either:
    (a) run `claude` on your host to authenticate, then `alcatrazer start`; or
    (b) copy .env.example to .env and fill in ANTHROPIC_API_KEY, then `alcatrazer start`.

$ cp .env.example .env && $EDITOR .env    # user fills in the token

$ alcatrazer start
Building container image... ████████████████ done (47s)
Creating workspace snapshot... done
Starting container...
Running startup commands...
  ✓ uv sync
Ready.
```

**Daily work:**
```
$ alcatrazer start
Container started. Environment up to date.

$ alcatrazer stop
Container stopped.
```

**After editing coding-environment.toml ([os] or [languages]):**
```
$ alcatrazer start
coding-environment.toml changed — rebuilding image.
Building... ████████████████ done (12s)
Restarting container...
Running startup commands...
  ✓ uv sync
  ✓ npm install
Ready.
```

**After editing coding-environment.toml ([startup] only):**
```
$ alcatrazer start
Restarting container...
Running startup commands...
  ✓ uv sync
  ✓ pnpm install
Ready.
```

**Startup failure:**
```
$ alcatrazer start
Running startup commands...
  ✓ uv sync
  ✗ npm install
    npm ERR! gyp ERR! build error — missing python

ERROR: Startup command #2 failed.
  → Check [startup] commands in coding-environment.toml
```

User fixes toml, runs `alcatrazer start` again.

---

## Configuration Files

### `coding-environment.toml` (version controlled)

Defines the agent's coding environment. Zero alcatrazer branding anywhere in the file.
If the repo already has a file by this name, use a random hex suffix:
`coding-environment-a3f7.toml`.

**Guiding principles:**

- **Usable startup, not lockdown** — pre-installs tools for convenience and speed, but
  agents have sudo and can install anything at runtime. The container boundary is the
  security perimeter, not the toml.
- **Don't repeat what the repo already defines** — language libraries belong in
  `requirements.txt`, `package.json`, etc. The toml defines what must exist BEFORE
  those files can be consumed (runtimes, managers, OS deps).
- **Agents can improve it** — agents see this file in the workspace and can add missing
  dependencies. Changes get promoted, reviewed by the developer, and used to rebuild.

#### Sections and installation order

The sections follow a strict dependency order. OS packages may be dependencies for
language runtimes or their libraries. Language runtimes must exist before package
managers can run. Startup commands depend on everything above. The reverse is
essentially never true.

The Dockerfile generator enforces this order regardless of how the file is written,
but the file should reflect the order for readability.

**1. `[os]` — system packages (installed first)**

```toml
[os]
packages = ["build-essential", "libpq-dev", "ffmpeg"]
```

Installed via the OS package manager (`apt-get`) at Docker build time. Needed for:
compilation dependencies, system tools that language packages can't provide,
libraries with C bindings.

Optional section. Omit if the project has no system-level dependencies.

**2. `[languages.<name>]` — runtimes and package managers (installed second)**

```toml
[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"
```

One subtable per language runtime. `version` is required — no `"latest"`,
reproducibility matters. Usually 1–2 languages, but no artificial limit.

`manager` is optional — defaults to the language's standard package manager:

| Language | Default manager | Non-default examples |
|----------|----------------|---------------------|
| python   | pip            | uv, poetry, pipenv  |
| node     | npm            | pnpm, yarn          |
| rust     | cargo          | (rarely overridden)  |
| go       | go modules     | (rarely overridden)  |

Specify `manager` only when using a non-default. Non-default managers are installed
as a separate step after the runtime.

Language runtimes are installed via **mise** — a multi-language version manager already
present in the alcatrazer base layer. See
[alcatraz_how_and_what_for.md](alcatraz_how_and_what_for.md) "Language runtime
installation: mise" for the comparison against alternatives.

**3. `[startup]` — boot-up commands (run last, at container start)**

```toml
[startup]
commands = [
    "uv sync",
    "npm install",
    "bash scripts/setup-bmad-framework.sh",
]
```

Ordered list of commands run after container start. This is where:
- Repo dependencies get installed from the project's own files (`uv sync`, `npm install`)
- Agentic frameworks with non-standard setup procedures get configured
- Any arbitrary bash command can go — the open-ended escape hatch

Commands run as the container user (not root), in order. Fail-fast: a failing command
stops execution (see "Build & Startup Error Handling" below).

Optional section. Omit if agents can figure out setup themselves (they have sudo).

#### Dockerfile generation mapping

The Dockerfile has three stages that cleanly separate concerns. Only the last
one is driven by `coding-environment.toml`:

| TOML section | Dockerfile action | Stage |
|---|---|---|
| (none — hardcoded) | phantom UID `agent` user, `gosu`, `git`, `mise`, `curl`, `ca-certificates` | `dev-base` |
| (none — hardcoded for MVP) | Claude Code CLI install | `ai-base` |
| `[os]` packages | `RUN apt-get install -y ...` | `dev` (build time, layer 1) |
| `[languages.*]` version | `RUN mise use --global <lang>@<version>` | `dev` (build time, layer 2) |
| `[languages.*]` manager | `RUN mise use --global <manager>` (non-default only) | `dev` (build time, layer 2) |
| `[startup]` commands | run at container start (not baked into image) | — |

- **`dev-base`** — security + core infrastructure. Only what alcatrazer's own
  machinery needs: the phantom-UID `agent` user, `gosu`, `git`, `mise`, `curl` +
  `ca-certificates` (needed by this stage's own installs). Hardcoded, always
  identical. Dev-ergonomics packages (`tmux`, `ripgrep`, …) and project-specific
  build deps (`build-essential`, `libpq-dev`, …) do not belong here — they live
  in the user's `[os]` section.

- **`ai-base`** — the AI agent CLI layer. Hardcoded to install Claude Code for the
  MVP. This stage exists to separate "AI tooling" from "security infrastructure"
  so it can later be generated dynamically from a new `[ai]` section of
  `coding-environment.toml` (letting a repo pick Claude, another agent, or none)
  without disturbing `dev-base`.

- **`dev`** — language runtimes, OS packages, project-specific bits. Fully
  generated from `coding-environment.toml`.

#### Examples

**Minimal (Python-only project):**

```toml
[languages.python]
version = "3.12"
```

**Typical (Python backend + TypeScript frontend):**

```toml
[os]
packages = ["build-essential", "libpq-dev"]

[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"

[startup]
commands = [
    "uv sync",
    "npm install",
]
```

**Complex (multi-language with agentic framework):**

```toml
[os]
packages = ["build-essential", "libpq-dev", "ffmpeg", "graphviz"]

[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"
manager = "pnpm"

[startup]
commands = [
    "uv sync",
    "pnpm install",
    "bash scripts/setup-bmad-framework.sh",
]
```

#### What's NOT in this file

- **Language libraries** — live in `requirements.txt`, `package.json`, `Cargo.toml`, etc.
  Installed by startup commands or by agents at runtime.
- **Alcatrazer configuration** — lives in `.alcatrazer/config.toml` (gitignored).
- **Promotion identity** — lives in `.alcatrazer/config.toml` (sensitive, per-developer).
- **Daemon settings** — lives in `.alcatrazer/config.toml`.
- **Anything that reveals Alcatrazer** — no branding, no security config, no tool-specific
  comments.

### `.alcatrazer/config.toml` (gitignored, per-developer)

Contains everything alcatrazer-specific. Invisible to agents (never in the snapshot).

```toml
# Path to the coding environment definition (relative to repo root)
coding_environment_file = "coding-environment.toml"

[promotion]
# Identity used when promoting commits from the workspace to the outer repo.
# Priority chain (lowest to highest):
#   git config (local > global) < this file < CLI --author-name/--author-email
name = "Grzegorz Latuszek"
email = "latuszek.grzegorz@gmail.com"

[promotion-daemon]
# Polling interval in seconds
interval = 5

# Which branches to promote from the inner repo:
#   "all" — every branch
#   A branch name — e.g. "main"
#   A list of patterns: ["main", "feature/*"]
branches = "all"

# Conflict handling mode:
#   "mirror" — promote to same branch names (default)
#   "alcatraz-tree" — promote to alcatraz/* namespace
mode = "mirror"

# Logging verbosity: "normal" or "detailed"
verbosity = "normal"

# Maximum log file size before rotation (in KB)
max_log_size = 512
```

Generated during first `alcatrazer start`. The interactive flow asks about promotion
identity; daemon settings default to the values above and can be edited later.

### `.git/info/exclude`

Git's built-in per-repo ignore mechanism. NOT version controlled, NOT in the working
tree — so it never enters the workspace snapshot.

Written by `alcatrazer start` during first-time setup:

```
# alcatrazer patterns (written by alcatrazer start)
.alcatrazer/
.<workspace-dir>/
```

The workspace directory name is randomly generated (e.g., `.devspace-7f3a/`).

---

## Build & Startup Error Handling

Installation from `coding-environment.toml` is a pipeline that can fail at each stage.
Alcatrazer detects these failures, reports them clearly, and guides the developer
through the fix cycle.

### Two failure times

**Build time** (`docker build` — image creation):
- `[os]` packages — typo in package name, package doesn't exist, dependency conflict,
  network unreachable
- `[languages.*]` runtimes — version doesn't exist, build from source fails
- `[languages.*]` managers — installation script fails, network unreachable

**Container start time** (after image is built):
- `[startup]` commands — `uv sync` fails (broken `pyproject.toml`), `npm install` fails
  (native module needs a missing OS dep), custom script errors out

Build-time failure: no image created, cannot start. Very visible.
Start-time failure: image exists, container starts, but environment is broken. More subtle.

### Fail fast

Each phase runs in dependency order. If a step fails, execution stops immediately.

```
[os] packages  →  fails?  →  STOP, report
       ↓ ok
[languages] runtimes + managers  →  fails?  →  STOP, report
       ↓ ok
--- image built, layers cached ---
[startup] command 1  →  fails?  →  STOP, report
       ↓ ok
[startup] command 2  →  fails?  →  STOP, report
       ↓ ok
...
READY
```

### Error reporting contract

Every error message must provide:

1. **Which phase failed** — "OS packages", "Python 3.12 installation",
   "startup command #2 (`npm install`)"
2. **The actual error output** — raw output from apt-get/pip/npm/bash
3. **Which TOML entry caused it** — pointer to the file and section

### The fix-rebuild loop

```
see error → edit coding-environment.toml → alcatrazer start → see if it passes
```

Docker layer caching makes this fast:

| What you fix | What re-runs | Cost |
|---|---|---|
| `[startup]` command | nothing rebuilt, just restart | cheapest — seconds |
| `[languages]` version/manager | language layer + after | medium — minutes |
| `[os]` package | OS layer + after | most expensive — minutes |

### Suggested fixes for common errors

Smart suggestions are a convenience, not a requirement for MVP. The essential contract is:
**never fail silently, always show what failed and where to edit.**

| Error pattern | Suggestion |
|---|---|
| `Unable to locate package X` | Fuzzy match against known packages |
| `Python/Node version X not found` | List available versions |
| `command not found: uv` | "Add `manager = \"uv\"` to the language's section" |
| `fatal error: X.h: No such file` | "Missing system header — add `-dev` package to `[os]`" |
| Startup command exits non-zero | Show the command, output, index in `[startup]` |

---

## Ephemeral caches — no shared Docker volumes

### The shared-volume trap

An earlier design mounted named Docker volumes for per-runtime caches, intending
that every Alcatraz on the laptop reuse the same downloads:

```
-v alcatraz-mise-cache:/home/agent/.local/share/mise
-v alcatraz-pip-cache:/home/agent/.cache/pip
-v alcatraz-npm-cache:/home/agent/.npm
```

Three shared writable volumes across independent sandboxes. It sounds economical.
It breaks Alcatraz's isolation promise in three concrete ways:

**Cross-sandbox attack surface (the critical one).** The agent running inside
Alcatraz A has write access to `/home/agent/.local/share/mise/installs/python/
3.12/bin/python`. A single compromised agent can plant a trojaned `python` there.
The next time Alcatraz B — *any other repo on the same laptop* — invokes `python`
via a `[startup]` command or during an agent's own work, it executes A's trojan.
Phantom UID, no-docker-socket, no-SSH, host-credential scrubbing — every other
isolation primitive is rendered irrelevant by one shared writable directory.
**One agent compromise escalates to universal agent compromise.**

**Silent reproducibility drift.** A runs `pip install` and refreshes a cached
wheel. B re-uses the new wheel on its next startup even though B's
`coding-environment.toml` never changed. Cross-repo coupling invisible from any
single repo's config — debugging "why did my build break, I didn't change
anything" is harder when the real cause was a different repo's cache write.

**Debugging dead-end.** Cache corruption has no traceable origin — any Alcatraz
could have caused it. Recovery means wiping the shared volume, which also nukes
every other repo's downloads.

### Why per-workspace volume naming is insufficient

A natural patch is to prefix volume names with the workspace name
(`buildkit-b0dc-mise-cache`), mirroring the workspace-name-as-marker technique
used for `.env.example`. It looks per-repo, but the isolation is probabilistic,
not structural:

- `identity.generate_workspace_dir_name()` returns `.{word}-{4hex}`. The
  collision check in `generate_workspace_choices()` only scans **the target repo's
  own directory**, not the developer's laptop or the Docker daemon's volume list.
- With ~23 words × 65536 hex suffixes ≈ 1.5M combinations, the birthday-paradox
  crossover for independent installs on the same laptop is low but nonzero.
  Collision probability ≥ 1% at ~170 installs — unlikely for one developer, not
  unthinkable at team scale or on CI runners that churn workspaces.
- When collision hits, the two Alcatrazes silently share the same volume, and
  the attack + drift + debug failure modes return — harder to reproduce, same
  blast radius.

A sandbox whose isolation depends on a dice roll fails exactly when it matters.
"Low probability" is not a security property.

### The fix: caches live in the container's writable overlay

Drop named volumes entirely. Each Alcatraz's container owns its own writable
overlay layer — per-container by construction, with zero shared filesystem
surface between Alcatrazes. A cache-poisoning agent inside A can't reach B's
filesystem at all, because B's filesystem is another container's overlay.

**Lifecycle consequence.** Today `_subsequent_run` calls `prison.remove()`
unconditionally on every `alcatrazer start`, which would erase the writable
layer (and its caches). The refactor removes the container only when a rebuild
is genuinely required:

| Trigger (detected in `_subsequent_run`) | Action | Cache state |
|---|---|---|
| Nothing changed, running | no-op + early return | preserved |
| Nothing changed, stopped | `docker start` | preserved |
| `[startup]` changed only | `docker start` (if stopped) + re-run startup via `exec` | preserved |
| `[os]` / `[languages]` changed (rebuild) | `docker rm` + `docker run` | cleared (correct — runtime/OS changed, stale cache would be wrong) |
| `.env` changed (mtime or hash) | `docker rm` + `docker run` | cleared (env is baked at `docker run` time) |

Caches clear exactly when the user's change makes them stale, and persist
whenever nothing runtime-affecting has changed. No shared state, no attack
surface, no volumes to inventory on uninstall.

**Uninstall becomes trivial.** Removing an Alcatraz from a repo is
`docker rm -f <container> && docker rmi <image>` — no volume hunt, no "did I
catch them all?" ambiguity.

---

## Installation Entry Points

All entry points converge to the same PyPI package. The only difference is who provides
the temporary Python environment.

```
pipx run alcatrazer start    →  pipx manages temp venv   →  alcatrazer start
uvx alcatrazer start         →  uv manages temp venv     →  alcatrazer start
curl | bash                  →  we manage temp venv      →  alcatrazer start
```

One installer codebase. One test surface. Three entry points.

### Option A: `pipx run alcatrazer start`

- Alcatrazer published to PyPI with CLI entry point
- `pipx run` downloads into temp venv, runs, discards
- Version pinning: `pipx run alcatrazer==1.2.0 start`
- **Requires:** pipx (which requires Python 3.11+)

### Option A2: `uvx alcatrazer start`

Same as A but via `uv`. Faster (Rust-based), gaining adoption rapidly.
- **Requires:** uv

### Option C: `curl -fsSL .../install.sh | bash`

Three-stage bootstrap that converges to the same PyPI package:

1. **Stage 1 (bash):** Resolve Python 3.11+ using four-tier fallback (detect system
   python3 → offer mise install → offer mise bootstrap → ask for manual path).
   Creates `.alcatrazer/python` symlink.
2. **Stage 2 (bash → Python stdlib):** Create temp venv via `python -m venv`.
   `ensurepip` (stdlib) provides pip.
3. **Stage 3 (same as pipx/uvx):** `pip install alcatrazer && alcatrazer start`.
   After installation, delete temp venv.

**Requires:** bash + curl (universal on Linux/macOS). Python is NOT assumed.

### Recommended: Hybrid

Document all three in the README. Meet users where they are:

```bash
# For pipx users:
pipx run alcatrazer start

# For uv users:
uvx alcatrazer start

# For everyone else (only assumes bash + curl):
curl -fsSL https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh | bash
```

---

## Trust & Verification

### The trust problem

Alcatrazer is a security tool. It asks users to run AI agents inside Docker containers
with the promise that their host is protected. This creates a fundamental trust question:

> "Isn't Alcatrazer a wise social engineer that claims to protect my laptop but
> instead is a thief itself?"

### Trust layers

**Layer 1: Open source on GitHub.**
The entire codebase is public. Anyone can read the Dockerfile, the entrypoint,
the volumes, the promotion scripts.

**Layer 2: Installed source is readable.**
Installation extracts into `.alcatrazer/src/alcatrazer/` — not compiled bytecode,
not obfuscated. The user can read every script:

```bash
cat .alcatrazer/src/alcatrazer/container/Dockerfile
cat .alcatrazer/src/alcatrazer/container/entrypoint.sh
cat .alcatrazer/src/alcatrazer/promote.py
```

**Layer 3: Post-installation verification tests.**
The test suite is bundled in `.alcatrazer/src/alcatrazer/tests/`. The user runs:

```bash
alcatrazer start --run-selftest
```

Tests verify: phantom UID isolation, no credential leaks, no Docker socket,
no git remotes, identity rewriting, file ownership.

**Layer 4: Checksum verification via independent channel.**
Each release publishes `SHA256SUMS` as a GitHub Releases asset.
The real proof is manual:

```bash
# Download checksums from GitHub (independent of PyPI)
curl -sL https://github.com/greg-latuszek/alcatrazer/releases/download/v0.3.0/SHA256SUMS -o /tmp/SHA256SUMS
# Verify with standard Unix tools
cd .alcatrazer/src/alcatrazer/ && sha256sum -c /tmp/SHA256SUMS
```

`alcatrazer start --verify-checksum` automates this as a convenience.

### Target audience

The target user is a developer. They can read Python and bash. They understand Docker
volumes and git. They may not read the source before first use — but knowing they *can*
is a trust signal. And when a security-conscious team lead asks "how do we know this is
safe?", the answer is: "read the source, run the tests, diff against GitHub."

---

## Implementation Plan

Build order for the installer (before first PyPI publish):

### Step 1: Bundle tool files as package data ✅

All tool files inside `src/alcatrazer/`. Hatch auto-includes everything in the wheel.

### Step 2: Replace old templates

Delete `src/alcatrazer/templates/alcatrazer.toml` (old design, pre-config-split).
Create templates for:
- `coding-environment.toml` (with placeholder sections)
- `.alcatrazer/config.toml` (with placeholder identity and daemon defaults)

### Step 3: Implement `alcatrazer init` (first-time disk setup)

When no `.alcatrazer/` exists:
1. Verify git repo at repo root
2. Interactive questions: languages, versions, managers, OS packages, promotion identity
3. Generate `coding-environment.toml` from answers
4. Generate `.alcatrazer/config.toml` (promotion identity, daemon defaults)
5. Write `.env.example` (with `ANTHROPIC_API_KEY` hint when host has no Claude creds)
6. Write `.git/info/exclude` patterns
7. Extract `src/alcatrazer/` tree into `.alcatrazer/src/alcatrazer/`
8. Generate `.alcatrazer/Dockerfile` + `entrypoint.sh` from `coding-environment.toml`
9. Print AI-credentials guidance block + "run `alcatrazer start` to build and launch"

**Stops before `docker build`.** Build + workspace creation + container start +
startup commands all move to Step 3.5 under `alcatrazer start` (the first-run
branch). Rationale: the user must be able to populate `.env` or authenticate
Claude on the host between disk setup and container start — see
"`alcatrazer init`" in the CLI section for the full argument.

Substeps for implementation:

#### Step 3a: CLI skeleton — `init` and `start` commands with state detection

Wire up two argparse subcommands:

- `alcatrazer init` — errors out if `.alcatrazer/` already exists ("already
  initialized; edit `coding-environment.toml` and run `alcatrazer start` to
  apply changes"). Otherwise runs the first-time disk setup (Steps 3b–3h).
- `alcatrazer start` — errors out if `.alcatrazer/` is missing ("no alcatrazer
  setup here — run `alcatrazer init` first"). Otherwise routes on image
  presence: no image yet → first-run branch (Step 3.5); image exists → Step 4
  detection logic.

No actual logic yet — just the two commands, the routing, and the two guard errors.

#### Step 3b: Verify git repo

Confirm we're inside a git repository and at the repo root (`os.path.exists('.git')`).
Abort with a clear message if not: "alcatrazer must be run from a git repository root."

#### Step 3c: Interactive questions — promotion identity

Read `user.name` and `user.email` from git config (local first, global fallback).
Present to user: "Detected git identity: X. Use for promoted commits? [Y/n]"
If declined, prompt for name and email. Store answers for Step 3e.

#### Step 3d: Interactive questions — coding environment

Ask about:
- Languages: "What languages does this project use?" (present common choices,
  allow multiple). For each: ask version, ask package manager (show default, allow override).
- OS packages: "Any system packages needed? (e.g., libpq-dev, ffmpeg)" — optional,
  allow empty.
- Startup commands: "Commands to run after container start? (e.g., uv sync, npm install)"
  — optional, allow empty.

Validation: language name must be supported by mise, version must be a concrete number
(no "latest"). Package manager must be a known name for the language.

#### Step 3e: Generate configuration files

From the answers collected in 3c and 3d:
- Write `coding-environment.toml` at repo root (from template, fill in sections).
  Zero alcatrazer branding in content or comments.
- Write `.alcatrazer/config.toml` (promotion identity from 3c, daemon defaults,
  pointer to coding-environment file).
- Write `.env.example` with a commented `ANTHROPIC_API_KEY=` placeholder plus
  a short explanation of when to fill it in. **Anti-leak rule:** `.env.example`
  is committed to the outer repo AND snapshotted into `/workspace`, so the
  agent reads its content. The block MUST contain zero `alcatraz` /
  `alcatrazer` strings, and the prose uses "workspace" rather than
  "container" (backend-agnostic — a future podman/sysbox/VM backend reuses
  the same file).
  - Write order: 3f (workspace-name generation) runs **before** 3e's
    `.env.example` write, so the write can take `workspace_name` as a
    direct argument (Python zen: direct over indirect — no re-reading from
    `.alcatrazer/workspace-dir`).
  - Markers use the workspace-name tag, not an alcatrazer string:
    `# --- <workspace-tag> begin ---` / `# --- <workspace-tag> end ---`
    (leading dot stripped for readability; the hex suffix keeps the marker
    collision-resistant inside the file). This gives the block a stable,
    neutral-looking identifier for idempotent in-place rewrites on re-run,
    without leaking product branding.
  - `.env.example` already exists in the target repo: append our block
    below the user's existing content (leave it untouched). On re-run,
    detect the marker and rewrite in place — never duplicate.

#### Step 3f: Write `.git/info/exclude`

Append patterns to `.git/info/exclude`:
- `.alcatrazer/`
- `.<workspace-dir>/` (workspace name generated here — random, e.g., `.devspace-7f3a/`)

Store workspace dir name in `.alcatrazer/workspace-dir`.

#### Step 3g: Extract package source

Copy the full `src/alcatrazer/` tree from the installed package into
`.alcatrazer/src/alcatrazer/`. This gives the user readable source and bundled tests.

#### Step 3h: Generate Dockerfile and entrypoint.sh

Read `coding-environment.toml`. Write two files into `.alcatrazer/`:

**`.alcatrazer/Dockerfile`** — three stages (see "Dockerfile generation mapping"
above for the full scope of each):

- **Stage 1 `FROM ubuntu:24.04 AS dev-base`** — security + core infrastructure.
  Emitted verbatim from a Python constant; not driven by the TOML.
- **Stage 2 `FROM dev-base AS ai-base`** — the AI CLI layer. Emitted verbatim
  from a Python constant for the MVP (installs Claude Code). A future step will
  generate this stage from a new `[ai]` section of `coding-environment.toml`.
- **Stage 3 `FROM ai-base AS dev`** — fully generated from the TOML:
    - `RUN apt-get install -y …` from `[os]` packages (skipped if the section is absent)
    - `RUN mise use --global <lang>@<version>` per `[languages.*]` entry
    - `RUN mise use --global <manager>` per non-default manager (skipped when the
      `manager` field is absent, which means the language default is in use)
    - Verify block: a single `RUN` that `&&`-chains `git --version`,
      `mise --version`, `claude --version` (always present) plus the per-language
      check command drawn from the hardcoded `SUPPORTED_LANGUAGES` map. Each
      language carries its own check (`python --version`, `rustc --version`,
      `go version`, …) because not every tool accepts `--version`.
    - Entrypoint tail: `COPY --chmod=755 entrypoint.sh …`, `WORKDIR`,
      `ENTRYPOINT`, `CMD`.

**`.alcatrazer/entrypoint.sh`** — byte-exact copy of the packaged template. Nothing
to parameterize; the script chowns `/workspace`, primes the mise cache, and drops
to the agent user.

**Not generated: `docker-compose.yml`.** A single-service compose wrapper is a
leaky abstraction here — users never run `docker compose` themselves; everything
goes through `alcatrazer start/stop/upgrade`. Container orchestration uses
`docker` subprocess calls from Python instead. This keeps the sandboxing
machinery internal and swappable (see "Hexagonal sandboxing architecture"
below), avoids a second source of truth for container config, and gives us
tighter control over volume / network / container names (better Principle-2
hygiene than compose's project-name prefixing).

#### Hexagonal sandboxing architecture

The sandboxing backend is a hexagonal port with dependency injection so the
implementation can change without touching the installer logic:

- **`alcatrazer.alcatraz.Alcatraz`** — abstract base class. Defines the
  operations the installer needs from any sandboxing backend:
  `generate_prison`, `needs_rebuild`, `build`, `image_exists`, `start`,
  `stop`, `is_running`, `exec`, `query`, `remove`. The "prison" in the
  Alcatraz metaphor.
- **`alcatrazer.docker_prison.DockerPrison(Alcatraz)`** — the only adapter
  implemented for MVP. Shells out to `docker build` / `run` / `start` / `stop` /
  `ps` / `exec` subprocesses.

Installer functions that need sandbox operations (Steps 3i, 3k, Step 4
detection logic, Step 7 selftest) accept an `Alcatraz` instance with a
`DockerPrison` default, so tests can inject a mock and future backends
(podman, sysbox, full VMs, …) can plug in without editing callers.

#### Naming convention for abstract vs concrete layers

Abstract code — the port interface, shared test bases, cross-backend
helpers — uses **"Alcatraz"** in identifiers, not "container". Rationale:
"container" is Docker-specific vocabulary, and Docker is only one of
several sandboxing techniques we might add (podman, sysbox, full VMs).
A VM-based prison has no container. "Alcatraz" is the generic prison
metaphor the repo coined, carries intended humor, and stays accurate
across backends.

Concrete implementation modules (`alcatrazer.docker_prison` today, a
future `alcatrazer.vm_prison`, etc.) freely use technology-specific
names where appropriate: `DockerPrison` has a `container_name` attribute,
for example, and its internals deal with `docker exec`, `docker ps`, etc.
The abstraction holds at the port — below it, each adapter speaks its
backend's native vocabulary.

Examples:

- Port / shared bases: `Alcatraz` (ABC), `_AlcatrazSecurityInvariants`
  (shared test base), `make_alcatraz_selftest_testcase` (factory),
  `SELFTEST_SCRIPT` — all neutral names.
- Adapter: `DockerPrison(Alcatraz)` — `container_name`, "container",
  `docker build`, `docker exec` all acceptable inside the module.

Drift to watch for: the temptation to name things after the current
implementation (`TestContainer…`, `CONTAINER_SCRIPT`, `run_in_container`)
because Docker is what we see every day. That naming burns in when a
second backend arrives.

#### Port methods: `exec` for humans, `query` for programs

The Alcatraz port exposes two distinct execution primitives:

- **`exec(command) -> int`** — runs inside the sandbox, streams output
  directly to the caller's terminal, returns only the exit code. Used
  for human-facing work: `[startup]` commands (whose progress the user
  watches), interactive attaches, long-running tasks where live
  feedback matters.
- **`query(command) -> subprocess.CompletedProcess`** — runs inside the
  sandbox, captures stdout / stderr / exit code, returns the result
  object. Used by programs that need to *read and decide* — security
  self-tests, future `show status` / diagnostic commands.

The contracts are genuinely different: `exec`'s job is to surface output
to a human; `query`'s job is to surface output to code. Conflating them
would force one path to compromise (silently swallow output, or
buffer-and-replay it after completion). Separating them keeps each
primitive simple and honest about what it delivers.

### Step 3.5: Implement `alcatrazer start` (first-run branch, after init)

Substeps 3i / 3j / 3k are what `alcatrazer start` does on its **first call
after `alcatrazer init`** — image doesn't exist yet, no container, no
workspace snapshot. They retain their numbering for continuity with earlier
TDD commits, but conceptually they live under `alcatrazer start`, not
`alcatrazer init`.

Entry guard: if `.alcatrazer/Dockerfile` is missing, abort with "no recipe
found — run `alcatrazer init` first". If it exists but the image hasn't been
built yet, run 3i → 3j → 3k in order. If the image already exists, fall
through to Step 4's subsequent-run detection.

No `.env` / Claude-creds check at this boundary — the user has already been
told what to do in init's guidance block. If they ignore it, the container
starts Claude-less and the agent surfaces its own auth error. We don't
gate `start` on credential presence because (a) the check isn't free
(read + parse + stat across two possible locations) and (b) future
`[ai]` configurations may not require any host-side credentials at all.

#### Step 3i: Docker build

Run `docker build` with the generated Dockerfile. Pass `USER_UID` as build arg
(phantom UID detected via `getent passwd`). Capture output for error reporting
(see "Build & Startup Error Handling" above). Fail fast if build fails.

Also: create `.alcatrazer/python` symlink from `sys.executable`.

#### Step 3j: Agent identity and workspace

Generate random human-looking name and email, store in `.alcatrazer/agent-identity`.
Create workspace directory (`.<workspace-dir>/`). Initialize inner git repo.
Create flat snapshot from outer repo's main branch — files only, no history,
one initial commit with generic message. Configure workspace-local git with
agent identity, no remote, `commit.gpgsign false`.

#### Step 3k: Start container and run startup commands

Start the container via `DockerPrison.start()` (raw `docker run -d` — no
docker-compose, see "Hexagonal sandboxing architecture" above). The container
runs detached with the workspace bind-mounted at `/workspace`, Claude
credentials mounted read-only, `.env` wired in, and `sleep infinity` as the
long-lived CMD so the container stays alive for later `docker exec` attaches.
**No named cache volumes** — mise / pip / npm caches live in the container's
writable overlay layer, per the "Ephemeral caches" section above.

Run `[startup]` commands in order via `DockerPrison.exec()`, wrapping each
toml entry as `bash -c <command>` and running as the `agent` user. Fail-fast:
a non-zero exit stops execution and reports per the "Build & Startup Error
Handling" contract (phase = "startup command #N"; raw output already
streamed to the terminal; pointer = the `[startup]` block in
`coding-environment.toml`).

Copy the current coding-environment file (filename read from
`.alcatrazer/config.toml`'s `coding_environment_file` pointer) to
`.alcatrazer/coding-environment.toml.last`. Step 4 uses this `.last` record
for change detection.

### Step 4: Implement `alcatrazer start` (subsequent runs, image already built)

Reached when `.alcatrazer/Dockerfile` exists AND the image has already been
built (`prison.image_exists() == True`). `_subsequent_run` branches on
detected change signals; the guiding principle is **only recreate the
container when a change actually invalidates it**, so the container's
writable layer (and thus its caches — see "Ephemeral caches — no shared
Docker volumes" above) survives every "nothing important changed" restart.

**Change-detection signals:**

| Signal | Source | Triggers |
|---|---|---|
| `rebuild` | `prison.needs_rebuild(coding_env)` — would-be recipe vs on-disk Dockerfile | new image required |
| `toml_changed` | `coding-environment.toml` content vs `.alcatrazer/coding-environment.toml.last` | `[startup]`-only changes (Dockerfile byte-identical but startup script list differs) |
| `env_changed` | hash of `.env` vs `.alcatrazer/env.hash.last` (both absent counts as unchanged) | env is baked at `docker run` time, so any change means recreate |
| `running` | `prison.is_running()` | container state |
| `exists` | `prison.exists()` | Alcatraz present in any state (running or stopped) — backend-neutral name at the port; DockerPrison implements it with a `docker ps -a` check (the pre-existing private `_container_exists` got promoted to this name) |

**Lifecycle branches:**

| Case | Detected | Action |
|---|---|---|
| Fast path | `running && !rebuild && !toml_changed && !env_changed` | no-op; print "Already running, environment up to date." |
| Full recreate | `rebuild \|\| env_changed` | stop (if running) + remove + (rebuild: generate_prison + build) + start (fresh `docker run`, writable layer cleared — correct: the runtime version / OS packages / env that shaped the cache have changed) |
| Resume stopped | `exists && !running && !rebuild && !env_changed` | `prison.resume()` (maps to `docker start`) — writable layer intact, caches preserved |
| Startup-only change on a running container | `running && toml_changed && !rebuild && !env_changed` | keep container, re-run `[startup]` via `exec` |
| Fresh start (rare) | `!exists` | `prison.start()` — user manually `docker rm`'d; equivalent to first-run-after-init |

**New Alcatraz port additions (Step 4a):** two methods land on the abstract
port so `DockerPrison` can implement them and future adapters stay honest:

- `resume()` — start an existing stopped container, preserving its writable
  layer. For `DockerPrison`: `docker start <container_name>`. Raises
  `PrisonStartError` on failure. Distinct from `start()` which always creates
  a fresh container via `docker run`.
- `exists()` — True if an Alcatraz instance with the configured
  identity exists in any state (running or stopped). Backend-neutral name
  per the memory's `feedback_alcatraz_naming.md` rule; `DockerPrison`
  implements it with a `docker ps -a --filter name=^<name>$` check (the
  pre-existing private `_container_exists` got promoted to this name,
  since keeping two identically-shaped helpers would be noise). Needed so
  `_subsequent_run` can distinguish "no Alcatraz, must `start`" from
  "stopped Alcatraz, can `resume`".

`ALCATRAZ_OPERATIONS` surface test in `test_alcatraz.py` grows by two.

**Volume drop (Step 4b):** remove the three `-v alcatraz-*-cache:...` args
from `DockerPrison.start`'s `docker run` command. Existing installs' orphan
volumes (`alcatraz-mise-cache` etc.) become dead weight in the user's Docker
daemon — `alcatrazer` does not auto-clean them (it can't reliably identify
which were its own). Release notes should call out
`docker volume rm alcatraz-mise-cache alcatraz-pip-cache alcatraz-npm-cache`
as a one-shot cleanup for early adopters.

**`.env` change detection (Step 4c):** two small helpers in `start.py`,
hashing only the **meaningful** content (comments and blank lines are
noise — a user who only tweaks a `# comment` line shouldn't pay the cost
of a recreate):

- `env_file_changed(project_dir) -> bool` — read `.env` (if present),
  normalize by dropping blank lines and full-line comments (lines whose
  first non-whitespace char is `#`), **preserve line order** (duplicate
  keys with different values may matter to some parsers; conservative
  default), SHA256 the UTF-8-encoded filtered content, compare against
  `.alcatrazer/env.hash.last`. Rules:
    - Absent `.env` + absent `.hash.last` → unchanged.
    - Absent `.env` + present `.hash.last` → changed (user removed the
      file; recreate to drop the env vars that were baked in).
    - Present `.env` + hash matches → unchanged.
    - Otherwise → changed.
  **Do NOT** strip inline `# ...` tails on a `KEY=VALUE` line — docker's
  `--env-file` treats `FOO=bar  # x` as value `bar  # x`, so stripping
  would change semantics. Only whole-line comments are free to ignore.
- `save_env_snapshot(project_dir)` — write the current hash to
  `.alcatrazer/env.hash.last` on successful run. Symmetric to
  `save_coding_environment_snapshot`. Writes empty hash when `.env`
  is absent (so the "absent + present last" transition detects correctly).

**Lifecycle rewrite (Step 4d):** `_subsequent_run` grows the new branches
above. `SubsequentRunTests` grows cases for: resume-from-stopped,
env-changed-triggers-recreate, startup-only-change-without-recreate (new —
container stays, `exec` runs the new commands against existing caches),
container-missing-falls-back-to-start.

Substeps land in TDD order: 4a (port surface — smallest, unblocks the rest),
4b (volume drop — standalone), 4c (env-change helpers — standalone), 4d
(lifecycle rewrite — depends on all three prior).

### Step 5: Implement `alcatrazer stop`

Stop the Alcatraz without removing it — `prison.stop()` maps to
`docker stop <container>`. The writable overlay layer (and its caches)
is preserved. Idempotent: no-op when not running. Straightforward.

### Step 5.5: Implement `alcatrazer clear`

`cmd_clear(project_dir, prison=None) -> int` removes the Alcatraz
entirely (writable layer + caches discarded), leaves the image and
`.alcatrazer/` config untouched so `alcatrazer start` recreates
trivially on next invocation.

Implementation reuses existing port methods only — no new abstractions:
- `prison.stop()` — idempotent; skipped if not running.
- `prison.remove()` — idempotent; `docker rm -f <container>` in
  `DockerPrison`.

Guards:
- `.alcatrazer/` missing → print "no alcatrazer setup here — run
  `alcatrazer init` first" and return 1 (symmetric to `cmd_start` /
  `cmd_stop`).
- No Alcatraz present (`!prison.exists()`) → no-op exit 0 with
  a friendly message ("Nothing to clear — Alcatraz not present.").

Does NOT:
- Remove the image (use `alcatrazer start --rebuild` afterwards if
  wanted).
- Remove `.alcatrazer/` config, `coding-environment.toml`, `.env`,
  `.env.example`, or the workspace directory. Those are either user-
  owned repo-root artifacts or fall under a future `alcatrazer
  uninstall` command.

CLI wiring: `alcatrazer clear` subcommand → `start_module.cmd_clear`.
No flags.

Tests: `CmdClearTests` mirroring `CmdStopTests` (guard for missing
`.alcatrazer/`, no-op when no Alcatraz, stop-then-remove when present)
+ `CliClearTests` for argparse wiring.

### Step 5.7: Sync daemon lifecycle wiring

Today the sync daemon exists (`alcatrazer.daemon`, refreshed in Steps
6a–e — config path, workspace pointer, default project_dir all fixed)
but launch and shutdown are manual (`mise run start-promotion`). This
step wires it into `cmd_start` / `cmd_stop` / `cmd_clear` so users
never need to think about it. See "Sync daemon — background promotion"
in the CLI section for the user-facing contract.

**Ordering rule (non-negotiable):** docker down BEFORE daemon finalizes
— otherwise agents can commit after the "final" sync, leaving a
commit unsynced until the next `alcatrazer start`. See the rationale
in the CLI section's lifecycle table.

**Substeps:**

**5.7a — `alcatrazer.state` module (shared CLI ↔ daemon cooperation file).**
Small new module backing `.alcatrazer/state.json`. Two functions:

- `load_state(alcatraz_dir: Path) -> dict` — best-effort read. Returns
  `{}` on missing file, unreadable file, or corrupt JSON. Callers that
  expect specific fields handle their absence defensively.
- `update_state(alcatraz_dir: Path, **fields) -> None` — read existing
  state (best-effort), merge `fields`, atomic-write (tmp file + rename)
  so the file is never half-written on process crash. Always stamps
  `schema_version: 1` so readers can migrate in future.

Unit tests in `test_state.py`: empty-dict on missing / corrupt; create
file when missing; preserve existing unrelated fields on update;
atomic-write doesn't clobber on simulated mid-write crash
(`update_state` followed by patched os.rename that raises — state file
remains the old content, not truncated).

This module is explicitly the seed of the future infocenter abstraction
(see `refactor_for_infocenter.md`) — scope is deliberately limited to
read/merge/write today; composite queries come later.

**5.7b — Daemon final-sync on shutdown (reads state.json).**
In `daemon.py`'s main-loop `finally` block (currently just logs
"Daemon stopped" + removes PID file), two changes:

1. Before the final sync, read state via `state.load_state(alcatraz_dir)`
   and select a log prefix: `daemon_shutdown == "requested"` →
   `Final sync (graceful shutdown): …`; anything else →
   `Final sync (unexpected shutdown): …`. Behavior does not branch;
   only the log text does.
2. Run one promote cycle. Log outcome (`N commits synced` or
   `CONFLICT on branch X`).

The daemon is already SIGTERM-aware via `shutdown_event =
threading.Event()`; by the time the `finally` runs, the event is set.
If `cmd_stop` / `cmd_clear` followed the contract, docker is already
stopped when we reach here. If a user `kill`-ed the daemon directly,
docker may still be running — the final sync is best-effort either way
(eventual consistency restored on next start).

Unit tests: drive daemon's main-loop function with a mock promote;
assert final sync is attempted exactly once on SIGTERM, log prefix
flips based on state.json contents (four cases: "requested", "done",
absent, corrupt).

**5.7c — CLI helper: `launch_sync_daemon(project_dir) -> DaemonLaunchInfo`.**
Lives in `alcatrazer.start` (or a small `alcatrazer.daemon_lifecycle`
module if it grows). Spawns `python -m alcatrazer.daemon --project-dir
<X>` detached, waits up to ~2 seconds for `.alcatrazer/promotion-daemon.pid`
to appear (liveness signal), returns a dataclass with `pid`,
`pid_file`, `log_file`, `config_file`, `interval` for the CLI to
print. Self-heal path: if PID file exists and `os.kill(pid, 0)`
succeeds → return info for the existing daemon, skip relaunch. If PID
file exists but process is dead → delete PID file and relaunch.
Unit tests: tempdir + mocked subprocess.Popen; assert spawn once on
absent-PID case, zero spawns on alive-PID case, one spawn on
stale-PID case.

**5.7d — CLI helper: `shutdown_sync_daemon(project_dir) -> ShutdownResult`.**
Four-step protocol:

1. `state.update_state(alcatraz_dir, daemon_shutdown="requested")` —
   write the intent flag BEFORE signaling, so the daemon sees it in
   its shutdown handler (5.7b).
2. Read `.alcatrazer/promotion-daemon.pid`, send `SIGTERM`, poll until
   the process exits (10s default timeout, with `SIGKILL` fallback on
   timeout as a safety valve).
3. Read the last ~20 lines of `.alcatrazer/promotion-daemon.log`, parse
   the final-sync outcome (regex over the strings the daemon logged in
   5.7b), build `ShutdownResult(outcome, synced_count, conflict_branches)`.
4. `state.update_state(alcatraz_dir, daemon_shutdown="done")` — close
   the cooperation cycle. Done even if no daemon was running, so the
   flag doesn't linger as `"requested"` from a prior crashed cycle.

If no daemon was running (no PID file or stale PID), return a "no-op"
outcome — not an error.

Unit tests: fake PID file + fake log file + patched os.kill; cover
success, conflict-on-final-sync, no-daemon-present, timeout-fallback,
and the state-flag transitions in all four cases.

**5.7e — `cmd_start` wires daemon launch.**
At the end of the happy path (right before `print("Ready.")` in
`_first_run_after_init`, or after `_subsequent_run` returns 0 via the
resume/fresh-start branches), call `launch_sync_daemon(project_dir)`
and print the transparency block (full block on actual launch,
single-line on already-alive). Fast-path also calls the helper — it
self-heals if a stale PID lingers. Tests: extend `FirstRunAfterInit`
and `SubsequentRunTests` with mocked launch helper; assert the helper
is called and its info surfaced; assert no crash when daemon launch
fails (launch error → warning, don't fail `start`).

**5.7f — `cmd_stop` wires daemon shutdown.**
Reorder inside `cmd_stop`: `prison.stop()` FIRST (the non-negotiable
ordering rule), THEN `shutdown_sync_daemon(project_dir)`. Print the
`ShutdownResult` — "Synced N commits" or conflict info. If conflict:
exit non-zero (container stays stopped, daemon stays dead, user
resolves in their repository). Extend `CmdStopTests`.

**5.7g — `cmd_clear` wires daemon shutdown.**
Same ordering as stop: `prison.stop()` → `shutdown_sync_daemon` →
`prison.remove()`. Inner repo directory is explicitly NOT removed
(preservation invariant from the "Sync daemon" section). Extend
`CmdClearTests`.

**5.7h — Smoke-test lifecycle coverage.**
Extend `test_smoke.py` with an end-to-end case: `init` → `start` →
verify daemon PID file exists and process is alive → commit inside
the Alcatraz workspace via `docker exec` → wait one poll cycle →
verify the commit appears in the outer repo via `git log` → `stop` →
verify daemon PID file gone + container stopped + inner repo dir
still present → `start` again → verify daemon relaunched + container
resumed → `clear` → verify daemon gone + container gone + **inner
repo dir STILL present**.

**Dependencies:** 6a–e already landed. 5.7 is pure orchestration on
top of the corrected daemon.

### Step 6: Implement `alcatrazer upgrade`

Check PyPI for newer version, re-extract `src/alcatrazer/` into `.alcatrazer/`,
preserve all state (config, workspace, marks, UID, identity).

### Step 7: Implement `--run-selftest` and `--verify-checksum`

#### Three-tier test organization

The bundled test suite is organized to match the Dockerfile's three-stage
architecture, so each stage's promises have their own test tier:

| Tier | What it tests | Backed by | Runs in |
|---|---|---|---|
| **Security invariants** | `dev-base` layer: phantom UID, host-credential isolation, `/workspace` ownership by phantom UID, signing keys cleared, `commit.gpgsign=false`, docker-socket absence, no git remotes, workspace git identity is the random agent (not host) | `alcatrazer.selftest._AlcatrazSecurityInvariants` | `--run-selftest` (user) + CI smoke |
| **Tooling availability** | `ai-base` + `dev` layers: Claude CLI present, `python`/`node`/… runtimes present, `mise` manages them | smoke-only mixin | CI smoke |
| **Workflow invariants** | `dev` layer usage: git branch + merge works, Python/Node can execute code, identity flows through new commits | smoke-only mixin | CI smoke |

Only the **security invariants** tier runs for `--run-selftest`. The
other two tiers are smoke-test territory — they verify that *tooling we
happened to install* works, which is an integration concern, not a
security promise Alcatrazer makes to the user.

#### Non-intrusiveness discipline for selftest

`--run-selftest` runs on the user's live project. It **must never**:

- Write files to `/workspace` (bind-mounted to `.<workspace-dir>/` in
  the user's host repo — changes persist, and the promotion daemon
  would later push them to the outer repo under the agent identity).
- Create commits, branches, or merges in the workspace git repo — same
  reason: promotion would expose our test pollution to the user's real
  repo.
- Mutate any bind-mounted host path.

Selftest assertions are instead **read-only observers**:

- Identity checks read the existing initial commit (`git log -1
  --format=…`), created by `create_workspace` at install time — no new
  commits needed.
- File-ownership checks read `/workspace` directory metadata (owned by
  the phantom UID after the entrypoint's `chown -R agent:agent`) — no
  file creation needed.
- Attack-surface checks use `test -e`, `ls -d`, `stat` — read-only
  probes only.

Smoke-test workflow checks that genuinely need write state (branch +
merge proofs, code execution from a file) use `/tmp/` inside the
container as a fresh scratch area, with proper `setUp` / `tearDown` per
test. `/tmp/` is writable inside the container but is not bind-mounted
to the host — it never leaks into the user's repo.

#### Per-test independence (no batch scripts)

Each assertion is a standalone unittest method that issues its own
`self.prison.query([...])`. Earlier iterations used a single bash
script gathering all data into delimited sections with test methods
parsing the capture — that design was an accommodation of the old
`docker compose run --rm workspace` execution model, where every test
run paid full container boot cost (~1–3 s) and minimizing calls
mattered. In the current long-lived-container design with
`docker exec`, per-call overhead is ~50–150 ms; 20 standalone exec
calls add ~2 seconds total, which is noise against CI's minutes and
interactive selftest's already-slow image build.

The per-test design is strictly better in five ways:

1. **Backend-agnostic paths.** Each test specifies what it needs. No
   embedded `/workspace` assumption at the script level — when a
   future VMPrison implements `query` over SSH, the test base is
   untouched.
2. **unittest lifecycle works.** Each test has real `setUp` and
   `tearDown`; tests are independent; subsets selectable; parallel
   execution possible.
3. **Non-intrusiveness enforced per-test.** Any test that needs write
   state owns its scratch area, set up and torn down explicitly inside
   that test.
4. **One place to read intent.** A test method reads as "do this,
   check that" — no cross-reference to a bash-script section.
5. **No hidden coupling.** The old batch had `PYTHON_EXEC` silently
   depending on a file `COMMIT_TEST` had created. Per-test design
   makes such dependencies impossible or at least visible.

The port's `query` method is what makes this viable: it captures
stdout / stderr / exit for programmatic inspection, while `exec` stays
focused on streaming for humans (see "Port methods: `exec` for humans,
`query` for programs" above).

#### `--verify-checksum`

Downloads `SHA256SUMS` from the GitHub Releases page for the installed
version, compares against hashes of the source tree in
`.alcatrazer/src/alcatrazer/`. **Parked until Step 10** ships real
SHA256SUMS assets — implementing it against infrastructure that
doesn't exist yet would mean any "passing" test validates only the
mock, not the real backend. See `feedback_real_source_testing.md` for
the principle.

### Step 8: Write `install.sh` (curl|bash bootstrap)

Thin bash script: resolve Python 3.11+ (four-tier), create temp venv,
`pip install alcatrazer`, run `alcatrazer start`, delete temp venv. ~50 lines.

### Step 9: Publish to PyPI

`uvx twine upload dist/*` — first real release.

### Step 10: Publish `SHA256SUMS` on GitHub Releases

Generate checksums at tagged commit, upload as release asset.

### Step 11 (post-v1): Filesystem + Infocenter abstractions

Not scheduled for v0.1.0. Layout-knowledge duplication between the command
stack and the promotion daemon caused drift during the install_method.md
refactor; the long-term fix is two layers (`alcatrazer.paths` +
`alcatrazer.infocenter`) so callers never concatenate `.alcatrazer/...`
strings inline.

Deferral rationale, scope estimate, preconditions for coming back to it,
and shape sketches live in
[refactor_for_infocenter.md](refactor_for_infocenter.md). Do **not** open
that doc until (a) Step 6a–6e has landed, (b) the daemon has been wired
into the `start` / `stop` / `clear` lifecycle, (c) v0.1.0 is on PyPI, and
(d) at least one real pain point points at the duplication.

---

## Current State

**Dev tooling set up:**
- `mise.toml` — manages python 3.12 + uv, defines tasks
- `pyproject.toml` — package config with hatchling build, ruff linting
- `src/alcatrazer/` — PyPI package skeleton with placeholder CLI
- Package builds successfully (`mise run build`)
- Version single-sourced from `src/alcatrazer/__init__.py`

**PyPI account:** Recovery in progress. Name `alcatrazer` is available.

---

## Manual tests and troubleshooting

Running log of real-world install/run sessions — the things the automated
smoke test doesn't catch. Add new entries at the bottom as they come up.

### 2026-04-23 — First real end-to-end install via built wheel

**Scenario.** Built wheel locally via `mise run build`, installed it into
a separate repo on the same machine (`markdown-knowledge-base` —
`/gitrepos/train_ai/build_ai_agents_training`). `alcatrazer
init` and `alcatrazer start` both succeeded: config files generated,
workspace snapshot taken, Docker image built, container up, promotion
daemon launched (PID visible in `ps`). `alcatrazer stop` reported
success at the CLI level but the daemon's final sync failed, and the
promotion log showed the daemon had been failing every poll since start.

**Symptom 1 — init's `[startup]` template is incomplete.**
First `alcatrazer start` aborted with:

```
→ Running startup command #1: mise env-create
mise ERROR Config files in /workspace/mise.toml are not trusted.
Trust them with `mise trust`.
```

Init had written only `mise env-create`, but a freshly-created workspace
needs `mise trust /workspace/mise.toml` run first. Re-running `start`
after hand-editing `coding-environment.toml` to prepend the `mise trust`
line worked. The `init` template should emit the `mise trust` line
automatically when the user chooses a language with a mise runtime.

**Symptom 2 — promotion daemon fails every poll.**
`.alcatrazer/promotion-daemon.log` after a normal session:

```
2026-04-23 18:23:46 Daemon started (PID 18800, interval=5s, branches=all, mode=mirror)
2026-04-23 18:23:52 Promotion failed: 'utf-8' codec can't decode byte 0xff in position 351656: invalid start byte
2026-04-23 18:23:57 Promotion failed: Command '[... 'fast-import', '--force', '--quiet', '--export-marks=...promote-import-marks']' returned non-zero exit status 128.
2026-04-23 18:24:02 Promotion failed: Command '[... 'fast-import', '--force', '--quiet', '--import-marks=...promote-import-marks', '--export-marks=...promote-import-marks']' returned non-zero exit status 128.
... (repeats every 5s until shutdown)
2026-04-23 18:27:32 Final sync (graceful shutdown) failed: ...fast-import... exit 128
```

**Root cause — `text=True` on a `git fast-export` pipe.**
`src/alcatrazer/promote.py:215` (and `:429` for the single-branch path):

```python
export_proc = subprocess.run(export_cmd, capture_output=True, text=True, check=True)
```

`text=True` forces stdout to be decoded as UTF-8. `git fast-export`
streams **raw blob bytes inline** — every binary file in git history
(images, fonts, compiled artifacts, PDFs, anything) is emitted verbatim
inside `data <n>\n<bytes>\n` sections. The first byte that isn't valid
UTF-8 (`0xff` here, ~344 KB into the stream) raises `UnicodeDecodeError`
before `promote()` ever returns. The `markdown-knowledge-base` repo has
binary content in history, which is what tripped this.

**Secondary cascade — marks-file desync.**
After the first Unicode error the daemon can't self-heal:

1. `git fast-export` ran to completion and flushed
   `.alcatrazer/promote-export-marks` (fast-export writes marks on exit).
   The Python decode error only fired *after* `subprocess.run` returned.
2. But `fast-import` was never called, so
   `.alcatrazer/promote-import-marks` was never created.
3. On every subsequent poll, `fast-export --import-marks=<full marks>`
   produces a tiny stream that references mark numbers (e.g. `from :3`)
   already recorded as exported. `fast-import` has no matching
   `--import-marks` file, doesn't know those marks, and exits 128.

So the first error is the *cause*; the long tail of `fast-import exit
128` is the daemon spinning on a desynced pair of marks files it can't
recover from on its own.

**Fix.**
Two things, in order of importance:

1. **Rewrite the fast-export → fast-import pipeline in bytes mode.**
   Drop `text=True` on the two fast-export subprocess calls in
   `promote.py`, change `rewrite_identity` and `rewrite_refs` to operate
   on `bytes` (use `rb"…"` patterns), and pipe bytes into fast-import's
   stdin. Plain-text git commands elsewhere (`git branch --format`,
   `rev-parse`, `config`) can keep `text=True` — only the
   fast-export/fast-import I/O needs to be binary. Add a regression test
   with a binary blob in history so this can't come back.

2. **Make `init` emit `mise trust /workspace/mise.toml` as the first
   startup command** when a mise-managed language is selected, so a
   fresh workspace doesn't die on its first `start`.

**Recovery for an already-broken install** (until the fix lands): delete
the desynced marks so the next run starts fresh —

```
rm .alcatrazer/promote-export-marks \
   .alcatrazer/promote-import-marks \
   .alcatrazer/promoted-tips.json
```

— or simply `alcatrazer clear && alcatrazer start`.

**Why the smoke test didn't catch this.** `test_smoke.py` seeds the
Alcatraz workspace with commits of small text files, so `fast-export`'s
output stays inside the ASCII subset of UTF-8 and `text=True` happens to
work. The regression test for the bytes-mode fix should include a
binary blob in the seeded history.