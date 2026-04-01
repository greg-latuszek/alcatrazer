# Agents in Sandbox

This repository is a safe sandbox environment for experimenting with agentic AI development inside Docker containers.

## Purpose

The goal is to run AI agents (such as Claude Code or other LLM-powered coding agents) inside isolated Docker containers where they can write code and iterate on it, without any risk of compromising the host system's credentials, keys, or identity.

This repo is designed as a reusable template — clone it, run the initialization script, and start experimenting with any agentic framework (os-eco, Claude Code, custom agent swarms, etc.) in any language.

## Repository Structure

This project uses a nested git architecture — a git repo inside a git repo:

```
agents_in_sandbox/              <-- outer repo (host user's real identity, has GitHub remote)
├── .git/                       <-- outer git
├── .gitignore                  <-- ignores sandbox/, .env, test/
├── .env.example                <-- template for API keys and sandbox config
├── README.md
├── Dockerfile                  <-- sandbox container image
├── docker-compose.yml          <-- container orchestration
├── entrypoint.sh               <-- container startup (chown + privilege drop)
├── initialize_sandbox.sh       <-- creates inner repo + finds phantom UID
├── scripts/
│   └── promote.sh              <-- promotes commits from inner to outer repo
└── sandbox/                    <-- mounted into Docker containers
    ├── .git/                   <-- inner git (Sandbox Agent identity, no remote)
    └── ... agent work ...
```

- The **outer repo** is the host-side control plane. It holds infrastructure (Dockerfiles, scripts, docs) and receives promoted agent work. It has the host user's real identity and a GitHub remote for pushing.
- The **inner repo** (`sandbox/`) is the agent workspace. It has a throwaway identity, no remote, and no access to host credentials. Only this directory is mounted into Docker containers.
- The inner repo is gitignored from the outer repo. Agent work enters the outer repo only through the promotion script.

## Security Model

### Container Isolation

The container runs as a **phantom UID** — a user ID that does not exist on the host machine. This provides defense in depth: even if an agent escapes the container, the process cannot write to any host files because no host user matches that UID.

The phantom UID is determined automatically by `initialize_sandbox.sh`, which scans the host for the first unused UID starting from 1001 and stores it in `.env` for reuse across container rebuilds.

### What agents CAN do

- Read and write files inside `sandbox/` (mounted into the container)
- Create git commits using a throwaway identity (`Sandbox Agent <sandbox@localhost>`)
- Create branches, merge branches, and build complex branch/merge histories
- Access the internet to communicate with LLM APIs (e.g. Anthropic API)
- Install packages and run code inside the container
- Use mise to manage tool versions (Python, Node.js, Bun, etc.)

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of `sandbox/`
- Access the Docker socket or spawn new containers
- Access any host credentials, tokens, or secrets (SSH keys, GPG keys, API keys stored in host environment)
- Write to host-owned files even if container escape occurs (phantom UID has no host permissions)
- Delete or modify files outside the mounted workspace

## Getting Started

### 1. Initialize the sandbox

```bash
./initialize_sandbox.sh
```

This script:
1. Creates `.env` from `.env.example` (if it doesn't exist)
2. Finds the first unused UID on the host (>= 1001) and writes `SANDBOX_UID` to `.env`
3. Creates `sandbox/` with an isolated git repo configured with the sandbox identity

### 2. Configure API keys

Edit `.env` and fill in the API keys your agents will need:

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=...          # optional
MINIMAX_API_KEY=...         # optional
```

### 3. Build and run

```bash
docker compose build
docker compose run --rm sandbox
```

You are now inside the container as a non-root agent user. All tools are available: Python, Node.js, Bun, Git, Tmux, Ripgrep, mise.

### Resetting the sandbox

Files created inside the container are owned by the phantom UID and cannot be deleted by the host user directly. Use the `--reset` flag, which spins up a disposable Docker container to clean up:

```bash
./initialize_sandbox.sh --reset
```

This removes all sandbox contents and reinitializes a fresh inner git repo.

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

1. **Mount only `sandbox/`** as the working volume — never the outer repo or the host home directory.
2. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
3. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
4. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Only explicitly chosen variables from `.env` are passed.
5. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).
6. **Run as phantom UID** — the container user's UID does not exist on the host.

## Workflow

1. Human runs `./initialize_sandbox.sh` to create the inner repo and determine the phantom UID.
2. Human fills in API keys in `.env`.
3. Human runs `docker compose build` and `docker compose run --rm sandbox`.
4. Agents inside the container write code, run tests, and commit incrementally. They may use branches, delegate to sub-agents, and merge — building full branch/merge histories.
5. Human reviews agent work via Docker: `docker compose run --rm sandbox git log --graph --oneline --all`.
6. Human runs the promotion script to transfer commits from the inner repo to the outer repo. The script rewrites the author identity and preserves the full branch and merge topology.
7. Human pushes the promoted commits to GitHub from the outer repo.

This separation ensures agents can do productive work while the human retains full control over what reaches the remote repository.

## Promoting Agent Work

The promotion script (`scripts/promote.sh`) uses `git fast-export` and `git fast-import` to transfer commits from the inner repo to the outer repo. This approach:

- Preserves full branch and merge topology (branches, merge commits, parent chains)
- Rewrites author/committer from `Sandbox Agent` to the host user's identity
- Supports incremental runs — only new commits since the last promotion are transferred
- Is unidirectional: inner repo to outer repo only