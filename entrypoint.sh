#!/usr/bin/env bash
#
# Container entrypoint — runs as root to fix workspace ownership,
# then drops to the non-root agent user via gosu.

set -euo pipefail

# Fix ownership of mounted workspace (created by host user, needs to be
# owned by the agent user inside the container)
chown -R agent:agent /workspace

# Fix ownership of mise cache volume (may be root-owned on first mount)
chown -R agent:agent /home/agent/.local/share/mise

# Ensure mise tools are available (handles empty cache volume on first run)
gosu agent mise install --yes 2>/dev/null || true
gosu agent mise reshim 2>/dev/null || true

# Drop to non-root agent user and exec the command
exec gosu agent "$@"