# Agents in Sandbox

This repository is a safe sandbox environment for experimenting with agentic AI development inside Docker containers.

## Purpose

The goal is to run AI agents (such as Claude Code or other LLM-powered coding agents) inside isolated Docker containers where they can write code and iterate on it, without any risk of compromising the host system's credentials, keys, or identity.

## Security Model

### What agents CAN do

- Read and write files inside this repository (mounted into the container)
- Create git commits locally (using a throwaway "Sandbox Agent" identity)
- Access the internet to communicate with LLM APIs (e.g. Anthropic API)
- Install packages and run code inside the container

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of this mounted repository
- Access the Docker socket or spawn new containers
- Access any host credentials, tokens, or secrets (SSH keys, GPG keys, API keys stored in host environment)

## How It Works

1. This repository is mounted into a Docker container as a volume.
2. The container has its own local git config with a sandboxed identity (`Sandbox Agent <sandbox@localhost>`, commit signing disabled).
3. The host's `~/.gitconfig`, `~/.ssh`, `~/.gnupg`, and other sensitive directories are never mounted.
4. Agents work inside the container: they write files, run tests, and make local commits.
5. Only a human operator on the host machine can review the changes and push to a remote.

## Local Git Config

This repository has a local `.git/config` that overrides global settings to prevent credential leakage:

- `user.name` = Sandbox Agent
- `user.email` = sandbox@localhost
- `commit.gpgsign` = false
- `user.signingkey` = (empty, overrides global)
- `gpg.ssh.allowedSignersFile` = (empty, overrides global)

These overrides ensure that even if a process reads `git config --list` from within this repo, it will not see the real user's signing key paths or identity as the effective values.

## Docker Container Rules

When building and running containers for this project, follow these rules:

1. **Never mount the host home directory** (`~` or `/home/<user>`) into the container.
2. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
3. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
4. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Pass only explicitly chosen variables like API keys needed for LLM access.
5. **Mount only this repository directory** as the working volume for the agent.
6. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).
7. **Use a dedicated `.gitconfig` inside the container image** with the sandboxed identity, so agents can commit freely.

## Workflow

1. Human starts a Docker container with this repo mounted.
2. Agent inside the container writes code, runs it, and commits incrementally.
3. Human reviews the commits from the host (`git log`, `git diff`).
4. Human pushes approved changes to GitHub from the host machine.

This separation ensures agents can do productive work while the human retains full control over what reaches the remote repository.