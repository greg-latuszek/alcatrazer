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
> *But the water around the island is real. No SSH keys exist inside. No git credentials. No host filesystem. The agents don't even know your name — they commit as "Alcatraz Agent", a ghost identity that maps to nobody. They run under a phantom UID that doesn't exist on your machine, so even if they tunnel through the walls, they surface as nobody, owning nothing, permitted nowhere.*
>
> *When the work is done, you — the warden — inspect it from the mainland. You review the commits, the branches, the merge history. If you approve, you run the transfer: every commit crosses the water with its topology intact, but the ghost identity is replaced with yours. The code enters your real repository, under your real name, ready to push.*
>
> *Your agents built it. You own it. And your secrets never left the mainland.*
>
> ***Alcatrazer** — the tool that builds your Alcatraz.*

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
your_repo/                      <-- outer repo (host user's real identity, has GitHub remote)
├── .git/                       <-- outer git
├── .gitignore                  <-- ignores .alcatraz/, .env
├── alcatrazer.toml             <-- tool configuration (version controlled)
├── .env.example                <-- template for API keys
├── README.md
├── Dockerfile                  <-- Alcatraz container image
├── docker-compose.yml          <-- container orchestration
├── entrypoint.sh               <-- container startup (chown + privilege drop)
├── src/
│   ├── initialize_alcatraz.sh  <-- creates inner repo + finds phantom UID
│   └── promote.sh              <-- promotes commits from inner to outer repo
└── .alcatraz/                  <-- mounted into Docker containers (gitignored)
    ├── .git/                   <-- inner git (Alcatraz Agent identity, no remote)
    └── ... agent work ...
```

- The **outer repo** is the host-side control plane. It holds infrastructure (Dockerfiles, scripts, docs) and receives promoted agent work. It has the host user's real identity and a GitHub remote for pushing.
- The **inner repo** (`.alcatraz/`) is the agent workspace. It has a hardcoded throwaway identity, no remote, and no access to host credentials. Only this directory is mounted into Docker containers.
- `alcatrazer.toml` captures configuration decisions (promotion identity, tool versions) and is version controlled.
- The inner repo is gitignored from the outer repo. Agent work enters the outer repo only through the promotion script.

## Security Model

### Container Isolation

The container runs as a **phantom UID** — a user ID that does not exist on the host machine. This provides defense in depth: even if an agent escapes the container, the process cannot write to any host files because no host user matches that UID.

The phantom UID is determined automatically by `initialize_alcatraz.sh`, which scans the host for the first unused UID starting from 1001 and stores it in `.env` for reuse across container rebuilds.

### What we protect against

Alcatraz protects **host filesystem integrity**. The threat model is an agent (intentionally or accidentally) reading local secrets, PII, or credentials and exfiltrating them over the network. Alcatraz prevents this by ensuring agents have no access to host files outside the mounted workspace.

Agents **are expected** to talk to LLM APIs — that's their job. Claude OAuth credentials are mounted read-only so agents can use your existing Claude subscription.

### What agents CAN do

- Read and write files inside `.alcatraz/` (mounted into the container)
- Create git commits using a throwaway identity (`Alcatraz Agent <alcatraz@localhost>`)
- Create branches, merge branches, and build complex branch/merge histories
- Access the internet to communicate with LLM APIs via Claude OAuth or API keys
- Install packages and run code inside the container
- Use mise to manage tool versions (Python, Node.js, Bun, etc.)

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of `.alcatraz/`
- Access the Docker socket or spawn new containers
- Read host files (SSH keys, GPG keys, git config, shell history, environment variables, etc.)
- Write to host-owned files even if container escape occurs (phantom UID has no host permissions)
- Delete or modify files outside the mounted workspace

## Getting Started

### 1. Initialize Alcatraz

```bash
./src/initialize_alcatraz.sh
```

This script:
1. Creates `.env` from `.env.example` (if it doesn't exist)
2. Finds the first unused UID on the host (>= 1001) and writes `ALCATRAZ_UID` to `.env`
3. Creates `.alcatraz/` with an isolated git repo configured with the Alcatraz Agent identity

### 2. LLM Authentication

**Primary method (recommended):** your existing Claude OAuth credentials (`~/.claude/.credentials.json`) are mounted read-only into the container. If you've already authenticated Claude Code CLI on your host, no additional setup is needed.

**Alternative:** if you prefer API key auth (separate billing, pay-per-use), edit `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Build and run

```bash
docker compose build
docker compose run --rm alcatraz
```

You are now inside the container as a non-root agent user. All tools are available: Python, Node.js, Bun, Git, Tmux, Ripgrep, mise.

### Resetting Alcatraz

Files created inside the container are owned by the phantom UID and cannot be deleted by the host user directly. Use the `--reset` flag, which spins up a disposable Docker container to clean up:

```bash
./src/initialize_alcatraz.sh --reset
```

This removes all Alcatraz contents and reinitializes a fresh inner git repo.

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

These rules are enforced by the `docker-compose.yml` configuration:

1. **Mount only `.alcatraz/`** as the working volume — never the outer repo or the host home directory.
2. **Mount only `~/.claude/.credentials.json`** (read-only) for LLM auth — never the entire `~/.claude/` directory (which contains project memories, settings, and other config).
3. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
4. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
5. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Only explicitly chosen variables from `.env` are passed.
6. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).
7. **Run as phantom UID** — the container user's UID does not exist on the host.

## Workflow

1. Human runs `./src/initialize_alcatraz.sh` to create the inner repo and determine the phantom UID.
2. Human runs `docker compose build` and `docker compose run --rm alcatraz`.
4. Agents inside the container write code, run tests, and commit incrementally. They may use branches, delegate to sub-agents, and merge — building full branch/merge histories.
5. Human reviews agent work via Docker: `docker compose run --rm alcatraz git log --graph --oneline --all`.
6. Human runs the promotion script to transfer commits from the inner repo to the outer repo. The script rewrites the author identity and preserves the full branch and merge topology.
7. Human pushes the promoted commits to GitHub from the outer repo.

This separation ensures agents can do productive work while the human retains full control over what reaches the remote repository.

## Promoting Agent Work

The promotion script (`src/promote.sh`) uses `git fast-export` and `git fast-import` to transfer commits from the inner repo to the outer repo. This approach:

- Preserves full branch and merge topology (branches, merge commits, parent chains)
- Rewrites author/committer from `Alcatraz Agent` to the host user's identity
- Supports incremental runs — only new commits since the last promotion are transferred
- Is unidirectional: inner repo to outer repo only