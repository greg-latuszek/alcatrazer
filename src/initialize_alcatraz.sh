#!/usr/bin/env bash
#
# Initialize the alcatraz environment for agentic AI development in Docker.
#
# This script:
# 1. Ensures .env exists for API keys
# 2. Finds a UID/GID that does not exist on the host (for container isolation)
# 3. Resolves Python 3.11+ for the promotion daemon and snapshot tool
# 4. Creates the inner git repo at .alcatrazer/workspace/ with Alcatraz Agent identity
# 5. Snapshots outer repo's main branch into workspace (automatic, no history)
# 6. Adds workspace to safe.directory so host git can read it
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
ALCATRAZ_DIR="${PROJECT_DIR}/.alcatrazer"
WORKSPACE_DIR="${ALCATRAZ_DIR}/workspace"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

# --- Handle --reset flag ---
# Files inside .alcatrazer/ are owned by the phantom UID and cannot be deleted
# by the host user directly. We use a disposable Docker container to clean up.

RESET=false
FORCE=false
for arg in "$@"; do
    case "${arg}" in
        --reset) RESET=true ;;
        --force) FORCE=true ;;
    esac
done

if [ "${RESET}" = true ]; then
    if [ -d "${ALCATRAZ_DIR}" ]; then
        # Check for unpromoted work before destroying.
        # Uses .alcatrazer/python from the *previous* init run (still on disk).
        # If Python isn't available (e.g. interrupted first init), skip the warning.
        PYTHON="${ALCATRAZ_DIR}/python"
        if [ "${FORCE}" = false ] && [ -x "${PYTHON}" ] && [ -d "${WORKSPACE_DIR}/.git" ]; then
            UNPROMOTED=$("${PYTHON}" -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
from snapshot import count_unpromoted_commits
print(count_unpromoted_commits('${WORKSPACE_DIR}', '${ALCATRAZ_DIR}'))
" 2>/dev/null || echo "0")
            if [ "${UNPROMOTED}" -gt 0 ] 2>/dev/null; then
                echo ""
                echo "Warning: ${UNPROMOTED} commit(s) in workspace have not been promoted to outer repo."
                echo "Proceeding with --reset will discard them."
                echo ""
                echo "  1. Proceed — discard workspace, re-snapshot, reinitialize"
                echo "  2. Cancel — abort reset, no changes"
                echo ""
                read -rp "Choose [1/2]: " CHOICE
                if [ "${CHOICE}" != "1" ]; then
                    echo "Reset cancelled."
                    exit 0
                fi
            fi
        fi

        echo "Resetting alcatrazer..."
        docker run --rm -v "${ALCATRAZ_DIR}:/workspace" ubuntu:24.04 \
            sh -c "rm -rf /workspace/* /workspace/.*" 2>/dev/null || true
        rmdir "${ALCATRAZ_DIR}" 2>/dev/null || true
        echo "Alcatrazer directory cleaned."
    else
        echo "No alcatrazer directory to clean."
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

UID_FILE="${ALCATRAZ_DIR}/uid"

if [ -f "${UID_FILE}" ]; then
    ALCATRAZ_UID=$(cat "${UID_FILE}")
    # Regenerate uid.env in case it was lost (e.g. after reset)
    echo "ALCATRAZ_UID=${ALCATRAZ_UID}" > "${ALCATRAZ_DIR}/uid.env"
    echo "Reusing stored alcatraz UID: ${ALCATRAZ_UID} (from .alcatrazer/uid)"
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

    mkdir -p "${ALCATRAZ_DIR}"
    echo "${ALCATRAZ_UID}" > "${UID_FILE}"
    # Also write as env var for docker-compose consumption
    echo "ALCATRAZ_UID=${ALCATRAZ_UID}" > "${ALCATRAZ_DIR}/uid.env"
    echo "Selected alcatraz UID/GID: ${ALCATRAZ_UID} (written to .alcatrazer/uid)"
fi

# Verify the UID still doesn't exist on this host (in case of machine change)
if getent passwd "${ALCATRAZ_UID}" >/dev/null 2>&1; then
    echo "WARNING: UID ${ALCATRAZ_UID} now exists on this host!"
    echo "Delete .alcatrazer/uid and re-run this script to pick a new UID."
    exit 1
fi

# --- Step 3: Resolve Python 3.11+ for the daemon and snapshot tool ---

"${SCRIPT_DIR}/resolve_python.sh" --alcatraz-dir "${ALCATRAZ_DIR}"

PYTHON="${ALCATRAZ_DIR}/python"

# --- Step 4: Initialize inner git repo ---

if [ -d "${WORKSPACE_DIR}/.git" ]; then
    echo "Alcatraz git repo already exists at ${WORKSPACE_DIR}/.git"
    echo "To reinitialize, run: ./src/initialize_alcatraz.sh --reset"
else
    # Create workspace directory inside .alcatrazer/
    # .alcatrazer/workspace/ is the only thing mounted into Docker (Principle 2)
    # Tool state (UID, marks, logs) lives in .alcatrazer/ but outside workspace/
    mkdir -p "${WORKSPACE_DIR}"

    # Initialize a fresh git repo
    git init "${WORKSPACE_DIR}"

    # Set Alcatraz identity - agents will commit under this throwaway identity
    git -C "${WORKSPACE_DIR}" config user.name "Alcatraz Agent"
    git -C "${WORKSPACE_DIR}" config user.email "alcatraz@localhost"

    # Disable commit signing - no access to host signing keys
    git -C "${WORKSPACE_DIR}" config commit.gpgsign false

    # Override signing key paths with empty values to prevent leaking host paths
    # (in case global gitconfig somehow becomes visible)
    git -C "${WORKSPACE_DIR}" config user.signingkey ""
    git -C "${WORKSPACE_DIR}" config gpg.ssh.allowedSignersFile ""

    echo ""
    echo "Alcatraz git repo initialized at: ${WORKSPACE_DIR}"

    # --- Step 5: Snapshot outer repo into workspace ---
    # Copies current main branch files (no history) into workspace.
    # Filters .alcatrazer/ from .gitignore, excludes .env and .alcatrazer/.
    # Creates a single "Initial commit" — zero footprint (Principle 2).

    "${PYTHON}" "${SCRIPT_DIR}/snapshot.py" "${PROJECT_DIR}" "${WORKSPACE_DIR}"
fi

# --- Step 6: Add safe.directory so host git can read the workspace ---
# The workspace is owned by the phantom UID, so git refuses to operate on it
# by default ("dubious ownership"). Adding it to safe.directory is safe —
# the directory is ours, created and controlled by our tool.

WORKSPACE_ABS="$(cd "${WORKSPACE_DIR}" && pwd)"
EXISTING_SAFE=$(git config --global --get-all safe.directory 2>/dev/null || true)

if echo "${EXISTING_SAFE}" | grep -qxF "${WORKSPACE_ABS}"; then
    echo "safe.directory already configured for ${WORKSPACE_ABS}"
else
    git config --global --add safe.directory "${WORKSPACE_ABS}"
    echo "Added ${WORKSPACE_ABS} to git safe.directory"
fi

# --- Summary ---

PYTHON_PATH=$(readlink -f "${ALCATRAZ_DIR}/python" 2>/dev/null || echo "not resolved")

echo ""
echo "Alcatraz configuration:"
echo "  UID/GID:      ${ALCATRAZ_UID} (phantom — does not exist on host)"
echo "  Workspace:    ${WORKSPACE_DIR}"
echo "  Python:       ${PYTHON_PATH}"
echo "  Git identity: Alcatraz Agent <alcatraz@localhost>"
echo ""
echo "Local git config:"
git -C "${WORKSPACE_DIR}" config --local --list
echo ""
echo "Next steps:"
echo "  1. Fill in API keys in .env"
echo "  2. Run: docker compose -f container/docker-compose.yml build"
echo "  3. Run: docker compose -f container/docker-compose.yml run --rm alcatraz"