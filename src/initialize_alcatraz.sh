#!/usr/bin/env bash
#
# Initialize the alcatraz environment for agentic AI development in Docker.
#
# This script:
# 1. Finds a UID/GID that does not exist on the host (for container isolation)
# 2. Writes ALCATRAZ_UID into .env (creating from .env.example if needed)
# 3. Creates the inner git repo with an Alcatraz Agent identity
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
ALCATRAZ_DIR="${PROJECT_DIR}/.alcatraz"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

# --- Handle --reset flag ---
# Files inside .alcatraz/ are owned by the phantom UID and cannot be deleted
# by the host user directly. We use a disposable Docker container to clean up.

if [ "${1:-}" = "--reset" ]; then
    echo "Resetting alcatraz..."
    if [ -d "${ALCATRAZ_DIR}" ]; then
        docker run --rm -v "${ALCATRAZ_DIR}:/workspace" ubuntu:24.04 \
            sh -c "rm -rf /workspace/* /workspace/.*" 2>/dev/null || true
        rmdir "${ALCATRAZ_DIR}" 2>/dev/null || true
        echo "Alcatraz directory cleaned."
    else
        echo "No alcatraz directory to clean."
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

# --- Step 2: Determine alcatraz UID/GID ---

# Check if ALCATRAZ_UID is already set in .env
EXISTING_UID=$(grep -oP '^ALCATRAZ_UID=\K.*' "${ENV_FILE}" 2>/dev/null || true)

if [ -n "${EXISTING_UID}" ]; then
    ALCATRAZ_UID="${EXISTING_UID}"
    echo "Reusing stored alcatraz UID: ${ALCATRAZ_UID} (from .env)"
else
    # Find the first UID >= 1001 that does not exist on the host.
    # This ensures the container user has no matching host account,
    # so even if the container escapes, the process cannot write to
    # any host files (no home dir, no owned files, no shell).
    ALCATRAZ_UID=1001
    while getent passwd "${ALCATRAZ_UID}" >/dev/null 2>&1 || \
          getent group "${ALCATRAZ_UID}" >/dev/null 2>&1; do
        ALCATRAZ_UID=$((ALCATRAZ_UID + 1))
    done

    echo "" >> "${ENV_FILE}"
    echo "# Alcatraz container UID/GID (phantom — does not exist on host)" >> "${ENV_FILE}"
    echo "ALCATRAZ_UID=${ALCATRAZ_UID}" >> "${ENV_FILE}"
    echo "Selected alcatraz UID/GID: ${ALCATRAZ_UID} (written to .env)"
fi

# Verify the UID still doesn't exist on this host (in case of machine change)
if getent passwd "${ALCATRAZ_UID}" >/dev/null 2>&1; then
    echo "WARNING: UID ${ALCATRAZ_UID} now exists on this host!"
    echo "Remove the ALCATRAZ_UID line from .env and re-run this script to pick a new UID."
    exit 1
fi

# --- Step 3: Initialize inner git repo ---

if [ -d "${ALCATRAZ_DIR}/.git" ]; then
    echo "Alcatraz git repo already exists at ${ALCATRAZ_DIR}/.git"
    echo "To reinitialize, run: ./initialize_alcatraz.sh --reset"
else
    # Create alcatraz directory if it doesn't exist
    mkdir -p "${ALCATRAZ_DIR}"

    # Initialize a fresh git repo
    git init "${ALCATRAZ_DIR}"

    # Set Alcatraz identity - agents will commit under this throwaway identity
    git -C "${ALCATRAZ_DIR}" config user.name "Alcatraz Agent"
    git -C "${ALCATRAZ_DIR}" config user.email "alcatraz@localhost"

    # Disable commit signing - no access to host signing keys
    git -C "${ALCATRAZ_DIR}" config commit.gpgsign false

    # Override signing key paths with empty values to prevent leaking host paths
    # (in case global gitconfig somehow becomes visible)
    git -C "${ALCATRAZ_DIR}" config user.signingkey ""
    git -C "${ALCATRAZ_DIR}" config gpg.ssh.allowedSignersFile ""

    echo ""
    echo "Alcatraz git repo initialized at: ${ALCATRAZ_DIR}"
fi

# --- Summary ---

echo ""
echo "Alcatraz configuration:"
echo "  UID/GID:      ${ALCATRAZ_UID} (phantom — does not exist on host)"
echo "  Workspace:    ${ALCATRAZ_DIR}"
echo "  Git identity: Alcatraz Agent <alcatraz@localhost>"
echo ""
echo "Local git config:"
git -C "${ALCATRAZ_DIR}" config --local --list
echo ""
echo "Next steps:"
echo "  1. Fill in API keys in .env"
echo "  2. Run: docker compose build"
echo "  3. Run: docker compose run --rm alcatraz"