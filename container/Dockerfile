# =============================================================================
# Stage 1: alcatraz-base
# The security layer — reusable across any project, any language.
# Provides: phantom UID, gosu, git, mise, Claude Code CLI, hardcoded
# Alcatraz Agent identity. No language-specific tools here.
# =============================================================================

FROM ubuntu:24.04 AS alcatraz-base

ENV DEBIAN_FRONTEND=noninteractive

# Phantom UID — does not exist on the host. If the container escapes,
# the process cannot write to any host files.
ARG ALCATRAZ_UID
RUN test -n "${ALCATRAZ_UID}" || { echo "ERROR: ALCATRAZ_UID build arg is required. Run ./initialize_alcatraz.sh first."; exit 1; }

# System dependencies for the security/orchestration layer
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
RUN groupadd --gid ${ALCATRAZ_UID} agent && \
    useradd --uid ${ALCATRAZ_UID} --gid ${ALCATRAZ_UID} --create-home --shell /bin/bash agent

# Install mise and Claude Code CLI as agent user
USER agent
ENV HOME=/home/agent
ENV PATH="/home/agent/.local/bin:${PATH}"

RUN curl https://mise.run | sh
RUN curl -fsSL https://claude.ai/install.sh | bash

# Activate mise shims
ENV PATH="/home/agent/.local/share/mise/shims:${PATH}"

# No global git identity — the workspace has its own local config
# (randomly generated identity, set by initialize_alcatraz.sh).
# Disable commit signing — no access to host signing keys inside container.
RUN git config --global commit.gpgsign false

# =============================================================================
# Stage 2: alcatraz-dev
# Language-specific tools — configured per project via alcatrazer.toml [tools].
# This stage is what end users customize for their stack.
# =============================================================================

FROM alcatraz-base AS alcatraz-dev

USER agent

# Tool versions from alcatrazer.toml [tools] — passed as build args
ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=22
ARG BUN_VERSION=latest

RUN mise use --global python@${PYTHON_VERSION} && \
    mise use --global node@${NODE_VERSION} && \
    mise use --global bun@${BUN_VERSION}

# Verify all tools
RUN python --version && node --version && bun --version && \
    git --version && claude --version && mise --version

# Back to root for entrypoint (drops to agent after chown)
USER root

COPY --chmod=755 container/entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
CMD ["/bin/bash"]