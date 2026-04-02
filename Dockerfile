FROM ubuntu:24.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Use a UID/GID that does NOT exist on the host — if the container escapes,
# the process cannot write to host files (no home dir, no owned files, no shell).
# Value is determined by initialize_sandbox.sh and passed via docker-compose.
ARG SANDBOX_UID=1001

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tmux \
    ripgrep \
    curl \
    ca-certificates \
    build-essential \
    unzip \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with the phantom UID
RUN groupadd --gid ${SANDBOX_UID} agent && \
    useradd --uid ${SANDBOX_UID} --gid ${SANDBOX_UID} --create-home --shell /bin/bash agent

# Switch to non-root user for all subsequent steps
# Install mise as agent user
USER agent
ENV HOME=/home/agent

RUN curl https://mise.run | sh
ENV PATH="/home/agent/.local/bin:${PATH}"

# Configure default tool versions via global mise config
# These serve as defaults; agents can override via mise.toml in their project
RUN mise use --global python@3.13 && \
    mise use --global node@22 && \
    mise use --global bun@latest

# Activate mise shims so all tools are available on PATH
ENV PATH="/home/agent/.local/share/mise/shims:${PATH}"

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | bash

# Verify installations
RUN python --version && node --version && bun --version && git --version && claude --version

# Set git defaults for sandbox identity (defense in depth — initialize_sandbox.sh
# also configures this per-repo, but this catches any git operation outside the repo)
RUN git config --global user.name "Sandbox Agent" && \
    git config --global user.email "sandbox@localhost" && \
    git config --global commit.gpgsign false && \
    git config --global init.defaultBranch main

# Switch back to root for entrypoint (it drops to agent after chown)
USER root

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
CMD ["/bin/bash"]