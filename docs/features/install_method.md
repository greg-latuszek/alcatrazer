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

## CLI: Three Commands

**Design goal:** Minimalism. Developers are tired of learning new tools. Alcatrazer
should be operable and invisible — as few commands as possible, no Docker vocabulary
leaking through (no `up`/`down`/`build`/`compose`), no separate init to learn.

### The commands

```
alcatrazer start                     the only command for daily work
alcatrazer stop                      stop the container
alcatrazer upgrade                   check for and install new alcatrazer version
```

Everything else is a flag on `start` or `upgrade`:

```
alcatrazer start --run-selftest      also run security self-tests after starting
alcatrazer start --verify-checksum   also verify installed source against GitHub
alcatrazer start --rebuild           force full rebuild even if nothing changed
alcatrazer upgrade --dry-run         check for new version without installing
```

### `alcatrazer start` — does the right thing

`start` is always safe to run. It detects the current state and does what's needed.

**Detection uses two comparisons:**
1. Generate would-be Dockerfile in memory, compare against existing `.alcatrazer/Dockerfile`
   → detects `[os]` or `[languages]` changes (rebuild needed)
2. Compare `coding-environment.toml` against `.alcatrazer/coding-environment.toml.last`
   → detects `[startup]` changes (restart needed, no rebuild)

```
alcatrazer start
       │
       ├── no .alcatrazer/ ?
       │       → first time: interactive questions → generate everything
       │         → build image → create workspace snapshot → start
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

### `alcatrazer stop` — stop the container

No flags, no options. Just stop.

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
$ alcatrazer start
No alcatrazer setup found. Starting interactive setup...

What languages does this project use?
> Python 3.12

Package manager? [pip] / uv / poetry
> uv

... (questions about promotion identity, etc.) ...

Generated: coding-environment.toml
Generated: .alcatrazer/config.toml
Written:   .git/info/exclude
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

### Step 3: Implement `alcatrazer start` (first-time flow)

When no `.alcatrazer/` exists:
1. Verify git repo at repo root
2. Interactive questions: languages, versions, managers, OS packages, promotion identity
3. Generate `coding-environment.toml` from answers
4. Generate `.alcatrazer/config.toml` (promotion identity, daemon defaults)
5. Write `.git/info/exclude` patterns
6. Extract `src/alcatrazer/` tree into `.alcatrazer/src/alcatrazer/`
7. Generate Dockerfile from `coding-environment.toml` into `.alcatrazer/`
8. Run `docker build`
9. Detect phantom UID, generate agent identity
10. Create workspace with snapshot (flat, no history, one initial commit)
11. Start container, run startup commands
12. Copy `coding-environment.toml` to `.alcatrazer/coding-environment.toml.last`

This is the largest step. Substeps for implementation:

#### Step 3a: CLI skeleton — `start` command with first-time detection

Wire up `alcatrazer start` as the CLI entry point. Detect "first time" by checking
for `.alcatrazer/` existence. If missing, enter the first-time flow. If present,
delegate to subsequent-run logic (Step 4). No actual logic yet — just the routing.

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
- Write `.env.example` (template for API keys).

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
  operations the installer needs from any sandboxing backend: `build`,
  `image_exists`, `start`, `stop`, `is_running`, `exec`, `remove`. The "prison"
  in the Alcatraz metaphor.
- **`alcatrazer.docker_prison.DockerPrison(Alcatraz)`** — the only adapter
  implemented for MVP. Shells out to `docker build` / `run` / `start` / `stop` /
  `ps` / `exec` subprocesses.

Installer functions that need sandbox operations (Steps 3i, 3k, Step 4 detection
logic) accept an `Alcatraz` instance with a `DockerPrison` default, so tests can
inject a mock and future backends (podman, sysbox, …) can plug in without
editing callers.

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

Start the container via docker-compose. Run `[startup]` commands in order inside
the container. Fail-fast on first error. Report success or failure per the error
handling contract.

Copy `coding-environment.toml` to `.alcatrazer/coding-environment.toml.last`.

### Step 4: Implement `alcatrazer start` (subsequent runs)

Change detection logic:
1. Generate would-be Dockerfile in memory
2. Compare against `.alcatrazer/Dockerfile` (rebuild if different)
3. Compare `coding-environment.toml` against `.alcatrazer/coding-environment.toml.last`
   (restart if different, no rebuild)
4. Start/restart container, run startup commands
5. Update `.alcatrazer/coding-environment.toml.last`

### Step 5: Implement `alcatrazer stop`

Stop the container. Straightforward.

### Step 6: Implement `alcatrazer upgrade`

Check PyPI for newer version, re-extract `src/alcatrazer/` into `.alcatrazer/`,
preserve all state (config, workspace, marks, UID, identity).

### Step 7: Implement `--run-selftest` and `--verify-checksum`

Bundle existing test suite. `--run-selftest` runs it after start.
`--verify-checksum` downloads `SHA256SUMS` from GitHub, compares.

### Step 8: Write `install.sh` (curl|bash bootstrap)

Thin bash script: resolve Python 3.11+ (four-tier), create temp venv,
`pip install alcatrazer`, run `alcatrazer start`, delete temp venv. ~50 lines.

### Step 9: Publish to PyPI

`uvx twine upload dist/*` — first real release.

### Step 10: Publish `SHA256SUMS` on GitHub Releases

Generate checksums at tagged commit, upload as release asset.

---

## Current State

**Dev tooling set up:**
- `mise.toml` — manages python 3.12 + uv, defines tasks
- `pyproject.toml` — package config with hatchling build, ruff linting
- `src/alcatrazer/` — PyPI package skeleton with placeholder CLI
- Package builds successfully (`mise run build`)
- Version single-sourced from `src/alcatrazer/__init__.py`

**PyPI account:** Recovery in progress. Name `alcatrazer` is available.