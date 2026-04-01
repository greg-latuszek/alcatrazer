# Agents in Sandbox

This repository is a safe sandbox environment for experimenting with agentic AI development inside Docker containers.

## Purpose

The goal is to run AI agents (such as Claude Code or other LLM-powered coding agents) inside isolated Docker containers where they can write code and iterate on it, without any risk of compromising the host system's credentials, keys, or identity.

## Repository Structure

This project uses a nested git architecture — a git repo inside a git repo:

```
agents_in_sandbox/              <-- outer repo (host user's real identity, has GitHub remote)
├── .git/                       <-- outer git
├── .gitignore                  <-- ignores sandbox/
├── README.md
├── initialize_sandbox.sh       <-- creates and configures the inner repo
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

### What agents CAN do

- Read and write files inside `sandbox/` (mounted into the container)
- Create git commits using a throwaway identity (`Sandbox Agent <sandbox@localhost>`)
- Create branches, merge branches, and build complex branch/merge histories
- Access the internet to communicate with LLM APIs (e.g. Anthropic API)
- Install packages and run code inside the container

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of `sandbox/`
- Access the Docker socket or spawn new containers
- Access any host credentials, tokens, or secrets (SSH keys, GPG keys, API keys stored in host environment)

## Sandbox Initialization

The inner git repo is created by `initialize_sandbox.sh`. This script:

1. Creates `sandbox/` and runs `git init` inside it
2. Sets the sandbox identity: `Sandbox Agent <sandbox@localhost>`
3. Disables commit signing (`commit.gpgsign = false`)
4. Blanks out signing key paths to prevent leaking host paths from global gitconfig

Run it once before starting any Docker containers:

```bash
./initialize_sandbox.sh
```

## Docker Container Rules

When building and running containers for this project, follow these rules:

1. **Mount only `sandbox/`** as the working volume — never the outer repo or the host home directory.
2. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
3. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
4. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Pass only explicitly chosen variables like API keys needed for LLM access.
5. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).

## Workflow

1. Human runs `./initialize_sandbox.sh` to create the inner repo.
2. Human starts a Docker container with `sandbox/` mounted as the agent workspace.
3. Agents inside the container write code, run tests, and commit incrementally. They may use branches, delegate to sub-agents, and merge — building full branch/merge histories.
4. Human reviews agent work from the host (`git -C sandbox/ log --graph`, `git -C sandbox/ diff`).
5. Human runs the promotion script to transfer commits from the inner repo to the outer repo. The script rewrites the author identity and preserves the full branch and merge topology.
6. Human pushes the promoted commits to GitHub from the outer repo.

This separation ensures agents can do productive work while the human retains full control over what reaches the remote repository.

## Promoting Agent Work

The promotion script (`scripts/promote.sh`) uses `git fast-export` and `git fast-import` to transfer commits from the inner repo to the outer repo. This approach:

- Preserves full branch and merge topology (branches, merge commits, parent chains)
- Rewrites author/committer from `Sandbox Agent` to the host user's identity
- Supports incremental runs — only new commits since the last promotion are transferred
- Is unidirectional: inner repo to outer repo only