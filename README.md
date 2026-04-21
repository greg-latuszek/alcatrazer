# Alcatrazer

*Your code gets out. Your secrets don't.*

<p align="center">
  <img src="images/alcatraz.jpg" alt="Alcatraz Island" width="700">
  <br>
  <sub>Photo: Javier Branas — <a href="https://commons.wikimedia.org/wiki/File:Alcatraz_-_panoramio.jpg">Wikimedia Commons</a> — <a href="https://creativecommons.org/licenses/by/3.0/">CC BY 3.0</a></sub>
</p>

---

> *The year is 2026. AI agents have become the fastest coders on the planet. They write, test, refactor, and ship — tirelessly, in parallel, around the clock.*
>
> *There's just one problem: you gave them the keys to your machine.*
>
> *Your SSH keys. Your git credentials. Your browser sessions. That `.env` file with production database passwords. The tax return PDF you forgot in your Downloads folder. All of it — one careless mount, one leaked environment variable, one hallucinated `curl` command away from somewhere it should never be.*
>
> *You didn't mean to. Nobody does. You just wanted the agent to scaffold a FastAPI backend. But it runs as you. It sees what you see. And when it phones home to its LLM, it sends whatever context it thinks is relevant.*
>
> *Alcatraz was built for a different kind of prisoner. The kind that works hard, produces valuable output, and never — ever — gets to touch the mainland.*
>
> *The island is a Docker container. The inmates are your AI agents. They get a workspace, tools, and internet access to talk to their LLM. They write code, create branches, run tests, commit their work. They can even orchestrate swarms of sub-agents, each on their own branch, merging results like a well-run development team.*
>
> *But the water around the island is real. No SSH keys exist inside. No git credentials. No host filesystem. The agents don't even know your name — they commit under a randomly generated ghost identity that maps to nobody real. They run under a phantom UID that doesn't exist on your machine, so even if they tunnel through the walls, they surface as nobody, owning nothing, permitted nowhere.*
>
> *When the work is done, you — the warden — inspect it from the mainland. You review the commits, the branches, the merge history. If you approve, you run the transfer: every commit crosses the water with its topology intact, but the ghost identity is replaced with yours. The code enters your real repository, under your real name, ready to push.*
>
> *Your agents built it. You own it. And your secrets never left the mainland.*
>
> ***Alcatrazer** — the tool that builds your Alcatraz.*

---

## Why Alcatrazer Exists

Watch out developers community. Your paradigm has changed. You trust yourself - that is understood. And that was you who have coded inside container up till now. But the world has changed. It is no more you who is coding inside container. These are AI agents that might do harmful things due to hallucination or prompt injecting via accidental download of malicious software. Harmful both - to your localhost and to your public git repository. So, don't trust them - put them in Alcatraz.

### What Alcatrazer protects

**Your development environment.** Your SSH keys, GPG keys, git credentials, browser sessions, `.env` files, personal documents — everything on your localhost that agents have no business touching.

**Your professional reputation.** Your public repositories carry your name. Other developers pull from them, depend on them, trust them. Unsupervised AI coding can inject trojans, backdoors, or other malware into YOUR repositories — code that others may pull and be harmed by. It is our responsibility as software engineers to review, test, and security-scan AI-created code before we publish it under our name. No AI writing to your repo without your knowledge. Alcatrazer enforces this: agents commit to an isolated inner repo, and nothing reaches your real repository until you — the warden — inspect and approve the transfer.

---

## CAUTION

| | |
|:---:|:---|
| <img src="https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExcjJqbTZ5NHZnZ2IybThnbWg2NXpvMDJrYW1sZmpwNnI3NnIwcjhnZSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/lTOmZHdG7ycHGRdlMF/giphy.gif" alt="Under Construction" width="120"> | **This project is under active construction.** File layout, APIs, naming, and the overall architecture may change without notice. The core security model works and is tested, but the tooling is not yet packaged for distribution. Use at your own risk — and contributions are welcome. |

---

## Purpose

Alcatrazer is a secure development environment for AI-powered coding agents. It isolates agent work inside Docker containers, protecting your host machine from accidental or intentional credential leakage, while letting agents do their job: write code, commit, branch, merge, and talk to LLMs.

It is designed as a reusable template — clone it, run the initialization script, and start experimenting with any agentic framework (os-eco, Claude Code, custom agent swarms, etc.) in any language.

## Repository Structure

This project uses a nested git architecture — a git repo inside a git repo:

```
your_repo/                          <-- outer repo (host user's identity, has GitHub remote)
├── .git/                           <-- outer git
├── .gitignore                      <-- ignores .alcatrazer/, .<workspace>/, .env
├── alcatrazer.toml                 <-- tool configuration (version controlled)
├── .env.example                    <-- template for API keys
├── README.md
├── container/                      <-- Docker infrastructure
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── src/                            <-- tool source code
│   ├── initialize_alcatraz.sh      <-- bash: creates inner repo + finds phantom UID + resolves Python
│   ├── resolve_python.sh           <-- bash: four-tier Python 3.11+ detection
│   ├── snapshot.py                 <-- Python: snapshots outer repo into workspace
│   ├── promote.py                  <-- Python: promotes commits from inner to outer repo
│   ├── daemon.py                   <-- Python: auto-promotion daemon
│   ├── inspect.py                  <-- Python: live log viewer
│   └── alcatrazer/                 <-- Python package
│       ├── __init__.py
│       └── identity.py             <-- random agent identity + workspace dir name generation
├── tests/                          <-- Python unittest test suite
│   ├── test_snapshot.py
│   ├── test_promote.py
│   ├── test_identity.py
│   ├── test_initialize.py
│   ├── test_watch_alcatraz.py
│   ├── test_python_resolution.py
│   ├── seed_alcatraz.sh            <-- helper: seeds a repo with realistic branch history
│   └── smoke_test.sh               <-- Docker integration test
├── .alcatrazer/                    <-- gitignored, tool state only (never mounted into Docker)
│   ├── python -> /usr/bin/python3  <-- symlink to resolved Python 3.11+
│   ├── uid                         <-- phantom UID
│   ├── agent-identity              <-- randomly generated name + email for agent commits
│   ├── workspace-dir               <-- name of the workspace directory
│   ├── promote-export-marks        <-- incremental promotion state
│   ├── promote-import-marks
│   ├── promoted-tips.json          <-- branch tips after last promotion (conflict detection)
│   ├── paused-branches.json        <-- branches paused due to conflicts
│   ├── promotion-daemon.pid        <-- daemon PID (single-instance guard)
│   └── promotion-daemon.log        <-- daemon activity log
└── .<workspace>/                   <-- gitignored, randomly named (e.g., .devspace-7f3a/)
    ├── .git/                       <-- inner git (random agent identity, no remote)
    └── ... agent work ...
```

- The **outer repo** is the host-side control plane. It holds infrastructure (Dockerfiles, scripts, docs) and receives promoted agent work. It has the host user's real identity and a GitHub remote for pushing.
- The **inner repo** (`.<workspace>/`) is the agent workspace, in a randomly named directory separate from `.alcatrazer/`. It has a randomly generated throwaway identity, no remote, and no access to host credentials. This directory is the only thing mounted into Docker — its generic name prevents leaking "alcatrazer" via Docker's `/proc/self/mountinfo`.
- **Tool state** (UID, marks, logs, daemon PID, agent identity) lives in `.alcatrazer/` — never mounted into Docker, invisible to agents.
- **Bootstrap scripts** (`initialize_alcatraz.sh`, `resolve_python.sh`) are bash — they run before Python exists. Everything else is Python (stdlib only, no pip).
- `alcatrazer.toml` captures configuration decisions and is version controlled.

## Security Model

### Container Isolation

The container runs as a **phantom UID** — a user ID that does not exist on the host machine. This provides defense in depth: even if an agent escapes the container, the process cannot write to any host files because no host user matches that UID.

The phantom UID is determined automatically by `initialize_alcatraz.sh`, which scans the host for the first unused UID starting from 1001 and stores it in `.alcatrazer/uid` for reuse across container rebuilds.

### What we protect against

Alcatraz protects **host filesystem integrity**. The threat model is an agent (intentionally or accidentally) reading local secrets, PII, or credentials and exfiltrating them over the network. Alcatraz prevents this by ensuring agents have no access to host files outside the mounted workspace.

Agents **are expected** to talk to LLM APIs — that's their job. Claude OAuth credentials are mounted read-only so agents can use your existing Claude subscription.

### What agents CAN do

- Read and write files inside the workspace (mounted into the container as `/workspace`)
- Create git commits using a randomly generated throwaway identity (different per init)
- Create branches, merge branches, and build complex branch/merge histories
- Access the internet to communicate with LLM APIs via Claude OAuth or API keys
- Install packages and run code inside the container
- Use mise to manage tool versions (Python, Node.js, Bun, etc.)

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of the mounted workspace
- Access the Docker socket or spawn new containers
- Read host files (SSH keys, GPG keys, git config, shell history, environment variables, etc.)
- Write to host-owned files even if container escape occurs (phantom UID has no host permissions)
- Delete or modify files outside the mounted workspace

## Getting Started

### 1. Start Alcatrazer

From the root of any git repository you want to protect:

```bash
alcatrazer start
```

On the first run, `alcatrazer start` asks a few interactive questions
(promotion identity, languages, OS packages, startup commands), writes
configuration files, generates the Dockerfile, builds the image, sets up
the inner workspace with a random agent identity, and starts the container.
Subsequent runs detect what (if anything) changed in your
`coding-environment.toml` and rebuild or restart only as needed.

For the full per-step breakdown see
[docs/features/install_method.md](docs/features/install_method.md).

### 2. LLM Authentication

**Primary method (recommended):** your existing Claude OAuth credentials (`~/.claude/.credentials.json`) are mounted read-only into the container. If you've already authenticated Claude Code CLI on your host, no additional setup is needed.

**Alternative:** if you prefer API key auth (separate billing, pay-per-use), edit `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Attach to the container

`alcatrazer start` leaves the container detached. Open a shell inside it via:

```bash
docker exec -it workspace /bin/bash
```

You are now inside the container as the non-root `agent` user. All tools
you requested in `coding-environment.toml` are available: Python / Node /
Rust / Go (from `[languages.*]`), any `[os]` packages, plus the always-on
security baseline (git, mise, Claude Code CLI, gosu).

### 4. Start the promotion daemon

In a separate terminal:

```bash
.alcatrazer/python -m alcatrazer.daemon
```

The daemon watches the workspace directory for new commits and
automatically promotes them to the outer repo with your identity (from
`.alcatrazer/config.toml`). Agent work appears in your repo in near
real-time.

To watch promotion activity:

```bash
.alcatrazer/python -m alcatrazer.inspect
```

### Stopping

```bash
alcatrazer stop
```

Idempotent — no-op when nothing is running.

### Resetting

There is no `alcatrazer reset` command yet. To start over: stop the
container, then remove `.alcatrazer/` and the randomly-named workspace
directory. Because files inside the workspace are owned by the phantom
UID, use a disposable container to do the cleanup:

```bash
alcatrazer stop
docker run --rm -v "$(pwd)":/w alpine sh -c 'rm -rf /w/.alcatrazer /w/.devspace-*'
```

(Adjust the `.devspace-*` glob to whatever workspace name `alcatrazer
start` chose — stored in `.alcatrazer/workspace-dir`.) Then run
`alcatrazer start` again.

## Configuration

Alcatrazer splits configuration across three files so that nothing in the
target repo's working tree reveals the tool to agents (Principle 2).

### `coding-environment.toml` — repo root, version-controlled

Agent-visible, zero alcatrazer branding. Describes what the container
must provide before the project's own setup can run:

```toml
[os]
packages = ["build-essential", "libpq-dev"]

[languages.python]
version = "3.12"
manager = "uv"          # omit for the language default (pip)

[languages.node]
version = "22"

[startup]
commands = ["uv sync", "npm install"]
```

### `.alcatrazer/config.toml` — gitignored, per-developer

Alcatrazer-specific, invisible to agents. Contains the promotion identity
and daemon settings:

```toml
coding_environment_file = "coding-environment.toml"

[promotion]
name  = "Your Name"
email = "your@email.com"

[promotion-daemon]
interval     = 5                # polling interval (seconds)
branches     = "all"            # or "main" or ["main", "feature/*"]
mode         = "mirror"         # or "alcatraz-tree"
verbosity    = "normal"         # or "detailed"
max_log_size = 512              # log rotation threshold (KB)
```

### `.git/info/exclude` — per-repo gitignore, not committed

`alcatrazer start` appends `.alcatrazer/` and the workspace directory
here instead of the working-tree `.gitignore` — so the ignore patterns
themselves don't enter the agent snapshot.

See [docs/features/install_method.md](docs/features/install_method.md) for
the full rationale.

## Promoting Agent Work

The promotion script (`alcatrazer.promote`) uses `git fast-export` and `git fast-import` to transfer commits from the inner repo to the outer repo. This approach:

- Preserves full branch and merge topology (branches, merge commits, parent chains)
- Rewrites author/committer from the agent's random identity to the host user's identity
- Supports incremental runs — only new commits since the last promotion are transferred
- Is unidirectional: inner repo to outer repo only

### Manual promotion

If you prefer to promote manually instead of using the daemon:

```bash
# Replace <workspace> with your workspace directory name (from .alcatrazer/workspace-dir)
.alcatrazer/python -m alcatrazer.promote --source <workspace> --target .

# Preview what would be promoted:
.alcatrazer/python -m alcatrazer.promote --source <workspace> --target . --dry-run
```

### Promotion Modes

The daemon supports two modes, configured via `mode` in `alcatrazer.toml`:

**`mirror` (default)** — Agent branches promote to the same branch names in the outer repo (`main` → `main`). Seamless sync for projects where agents do most of the coding. If the human also commits to the outer repo on a promoted branch, the daemon detects the divergence and creates a conflict branch (see below).

**`alcatraz-tree`** — Agent branches promote into an `alcatraz/*` namespace (`main` → `alcatraz/main`, `feature/auth` → `alcatraz/feature/auth`). The human's branches are never touched. Use this when both human and agents commit frequently to the same branches — the separate namespace means zero conflicts. The human merges from `alcatraz/*` when ready.

### Conflict Resolution (mirror mode)

If you commit directly to the outer repo on a branch that the daemon is also promoting, the daemon detects the divergence and pauses promotion on that branch. It:

1. Creates a `conflict/resolve-<branch>-<timestamp>` branch containing the agent's version of the work
2. Logs a warning to `.alcatrazer/promotion-daemon.log`
3. Continues promoting other branches normally

**To resolve:**

```bash
# Option A: Merge the agent's work into your branch
git merge conflict/resolve-main-20260406-120000
# Resolve any merge conflicts, then:
git branch -d conflict/resolve-main-20260406-120000

# Option B: Discard the agent's work on this branch
git branch -D conflict/resolve-main-20260406-120000
```

Once the `conflict/resolve-*` branch is deleted (merged or discarded), the daemon automatically resumes promotion on that branch. No daemon restart needed.

### Branch Filtering

Control which branches cross the water:

```toml
[promotion-daemon]
branches = "all"                    # every branch (default)
branches = "main"                   # a single branch (use your branch name: "main", "master", etc.)
branches = ["main", "feature/*"]    # branch names and glob patterns
```

## Container Details

### Base image and tools

- **Ubuntu 24.04** base image
- **mise** for runtime version management (agents can configure `mise.toml` per-project)
- **Python 3.13** (default, configurable via mise)
- **Node.js 22 LTS** (default, configurable via mise)
- **Bun** (latest, for tools like os-eco)
- **Git, Tmux, Ripgrep** for development and agent orchestration
- **gosu** for secure privilege dropping in entrypoint

### Entrypoint behavior

The container starts as root to fix ownership of the mounted workspace, then drops to the non-root `agent` user via gosu. On first run (or when cache volumes are empty), the entrypoint also runs `mise install` to ensure tools are available.

### Persistent caches

Named Docker volumes are used to persist package caches across container restarts:

- `mise-cache` — mise tool installations (Python, Node, Bun binaries)
- `pip-cache` — Python package downloads
- `npm-cache` — Node.js package downloads
- `bun-cache` — Bun package downloads

This avoids re-downloading tools and packages on every `docker compose run`.

## Docker Container Rules

These rules are enforced by `DockerPrison` (the Docker adapter of the
Alcatraz sandboxing port) when it builds and runs the workspace container:

1. **Mount only the workspace directory** as the working volume — never the outer repo or the host home directory.
2. **Mount only `~/.claude/.credentials.json`** (read-only) for LLM auth — never the entire `~/.claude/` directory (which contains project memories, settings, and other config).
3. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
4. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
5. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Only explicitly chosen variables from `.env` are passed.
6. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).
7. **Run as phantom UID** — the container user's UID does not exist on the host.

## Workflow

1. `alcatrazer start` — first-time wizard + install, or a no-op/rebuild on
   subsequent runs depending on what changed in `coding-environment.toml`.
2. `docker exec -it workspace /bin/bash` — attach a shell as the agent user.
3. `.alcatrazer/python -m alcatrazer.daemon` — start the promotion daemon
   (separate terminal).
4. Agents inside the container write code, run tests, and commit
   incrementally. They may use branches, delegate to sub-agents, and merge.
5. The daemon automatically promotes agent commits to the outer repo with
   your identity. Watch activity with `.alcatrazer/python -m alcatrazer.inspect`.
6. Human reviews promoted work in the outer repo: `git log --graph --oneline --all`.
7. Human pushes the promoted commits to GitHub from the outer repo.
8. `alcatrazer stop` when done for the day.

## Running Tests

```bash
.alcatrazer/python -m unittest discover -s src/alcatrazer/tests -v
```

The test suite covers identity generation (name/email pools, workspace dir naming, collision avoidance), initialization (repo root guard, identity wiring, workspace separation), snapshot (branch detection, extraction, .gitignore filtering, exclusions, CLI, reset warnings), promotion (identity rewrite, incremental, dry-run, topology), daemon (PID guard, config, signals, conflict detection/resolution, branch filtering, modes), Python resolution (four-tier fallback), and the inspection tool. All tests use Python's `unittest` framework with real git repos for integration tests and mocking for unit tests.
