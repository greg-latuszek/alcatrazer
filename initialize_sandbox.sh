#!/usr/bin/env bash
#
# Initialize the sandbox environment for agentic AI development in Docker.
#
# This script:
# 1. Finds a UID/GID that does not exist on the host (for container isolation)
# 2. Writes SANDBOX_UID into .env (creating from .env.example if needed)
# 3. Creates the inner git repo with a sandboxed identity
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="${SCRIPT_DIR}/sandbox"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"

# --- Handle --reset flag ---
# Files inside sandbox/ are owned by the phantom UID and cannot be deleted
# by the host user directly. We use a disposable Docker container to clean up.

if [ "${1:-}" = "--reset" ]; then
    echo "Resetting sandbox..."
    if [ -d "${SANDBOX_DIR}" ]; then
        docker run --rm -v "${SANDBOX_DIR}:/workspace" ubuntu:24.04 \
            sh -c "rm -rf /workspace/* /workspace/.*" 2>/dev/null || true
        rmdir "${SANDBOX_DIR}" 2>/dev/null || true
        echo "Sandbox directory cleaned."
    else
        echo "No sandbox directory to clean."
    fi
    echo "Re-running initialization..."
    echo ""
fi

# --- Step 1: Ensure .env exists ---

if [ ! -f "${ENV_FILE}" ]; then
    if [ -f "${ENV_EXAMPLE}" ]; then
        cp "${ENV_EXAMPLE}" "${ENV_FILE}"
        echo "Created .env from .env.example — fill in your API keys."
    else
        touch "${ENV_FILE}"
    fi
fi

# --- Step 2: Determine sandbox UID/GID ---

# Check if SANDBOX_UID is already set in .env
EXISTING_UID=$(grep -oP '^SANDBOX_UID=\K.*' "${ENV_FILE}" 2>/dev/null || true)

if [ -n "${EXISTING_UID}" ]; then
    SANDBOX_UID="${EXISTING_UID}"
    echo "Reusing stored sandbox UID: ${SANDBOX_UID} (from .env)"
else
    # Find the first UID >= 1001 that does not exist on the host.
    # This ensures the container user has no matching host account,
    # so even if the container escapes, the process cannot write to
    # any host files (no home dir, no owned files, no shell).
    SANDBOX_UID=1001
    while getent passwd "${SANDBOX_UID}" >/dev/null 2>&1 || \
          getent group "${SANDBOX_UID}" >/dev/null 2>&1; do
        SANDBOX_UID=$((SANDBOX_UID + 1))
    done

    echo "" >> "${ENV_FILE}"
    echo "# Sandbox container UID/GID (phantom — does not exist on host)" >> "${ENV_FILE}"
    echo "SANDBOX_UID=${SANDBOX_UID}" >> "${ENV_FILE}"
    echo "Selected sandbox UID/GID: ${SANDBOX_UID} (written to .env)"
fi

# Verify the UID still doesn't exist on this host (in case of machine change)
if getent passwd "${SANDBOX_UID}" >/dev/null 2>&1; then
    echo "WARNING: UID ${SANDBOX_UID} now exists on this host!"
    echo "Remove the SANDBOX_UID line from .env and re-run this script to pick a new UID."
    exit 1
fi

# --- Step 3: Initialize inner git repo ---

if [ -d "${SANDBOX_DIR}/.git" ]; then
    echo "Sandbox git repo already exists at ${SANDBOX_DIR}/.git"
    echo "To reinitialize, run: ./initialize_sandbox.sh --reset"
else
    # Create sandbox directory if it doesn't exist
    mkdir -p "${SANDBOX_DIR}"

    # Initialize a fresh git repo
    git init "${SANDBOX_DIR}"

    # Set sandboxed identity - agents will commit under this throwaway identity
    git -C "${SANDBOX_DIR}" config user.name "Sandbox Agent"
    git -C "${SANDBOX_DIR}" config user.email "sandbox@localhost"

    # Disable commit signing - no access to host signing keys
    git -C "${SANDBOX_DIR}" config commit.gpgsign false

    # Override signing key paths with empty values to prevent leaking host paths
    # (in case global gitconfig somehow becomes visible)
    git -C "${SANDBOX_DIR}" config user.signingkey ""
    git -C "${SANDBOX_DIR}" config gpg.ssh.allowedSignersFile ""

    echo ""
    echo "Sandbox git repo initialized at: ${SANDBOX_DIR}"
fi

# --- Summary ---

echo ""
echo "Sandbox configuration:"
echo "  UID/GID:      ${SANDBOX_UID} (phantom — does not exist on host)"
echo "  Workspace:    ${SANDBOX_DIR}"
echo "  Git identity: Sandbox Agent <sandbox@localhost>"
echo ""
echo "Local git config:"
git -C "${SANDBOX_DIR}" config --local --list
echo ""
echo "Next steps:"
echo "  1. Fill in API keys in .env"
echo "  2. Run: docker compose build"
echo "  3. Run: docker compose run --rm sandbox"