#!/usr/bin/env bash
#
# Initialize the alcatraz environment for agentic AI development in Docker.
#
# This script:
# 1. Ensures .env exists for API keys
# 2. Finds a UID/GID that does not exist on the host (for container isolation)
# 3. Resolves Python 3.11+ for the promotion daemon and snapshot tool
# 3.5. Selects workspace directory (randomly named, separate from .alcatrazer/)
# 4. Creates the inner git repo in the workspace directory with random identity
# 5. Snapshots outer repo's main branch into workspace (automatic, no history)
# 6. Adds workspace to safe.directory so host git can read it
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# --- Guard: verify we are at the repository root ---
# Both .alcatrazer/ and the workspace directory must be created at the repo root.

REPO_ROOT=$(git -C "${PROJECT_DIR}" rev-parse --show-toplevel 2>/dev/null) || {
    echo "ERROR: Not inside a git repository."
    echo "Alcatrazer must be initialized from within a git repository."
    exit 1
}

if [ "$(cd "${PROJECT_DIR}" && pwd)" != "$(cd "${REPO_ROOT}" && pwd)" ]; then
    echo "ERROR: Script is not at the repository root."
    echo "  Expected: ${REPO_ROOT}/src/initialize_alcatraz.sh"
    echo "  Actual:   ${SCRIPT_DIR}/initialize_alcatraz.sh"
    echo "Alcatrazer must be installed at the root of your git repository."
    exit 1
fi

ALCATRAZ_DIR="${PROJECT_DIR}/.alcatrazer"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
# WORKSPACE_DIR is resolved after Python is available (Step 3.5)

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
        # Resolve workspace dir from stored selection (if available)
        RESET_WORKSPACE_DIR=""
        if [ -f "${ALCATRAZ_DIR}/workspace-dir" ]; then
            RESET_WORKSPACE_NAME=$(cat "${ALCATRAZ_DIR}/workspace-dir" | tr -d '[:space:]')
            RESET_WORKSPACE_DIR="${PROJECT_DIR}/${RESET_WORKSPACE_NAME}"
        fi

        # Check for unpromoted work before destroying.
        # Uses .alcatrazer/python from the *previous* init run (still on disk).
        # If Python isn't available (e.g. interrupted first init), skip the warning.
        PYTHON="${ALCATRAZ_DIR}/python"
        if [ "${FORCE}" = false ] && [ -x "${PYTHON}" ] && [ -n "${RESET_WORKSPACE_DIR}" ] && [ -d "${RESET_WORKSPACE_DIR}/.git" ]; then
            UNPROMOTED=$("${PYTHON}" -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
from snapshot import count_unpromoted_commits
print(count_unpromoted_commits('${RESET_WORKSPACE_DIR}', '${ALCATRAZ_DIR}'))
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
        # Clean workspace directory (separate from .alcatrazer/)
        if [ -n "${RESET_WORKSPACE_DIR}" ] && [ -d "${RESET_WORKSPACE_DIR}" ]; then
            docker run --rm -v "${RESET_WORKSPACE_DIR}:/workspace" ubuntu:24.04 \
                sh -c "rm -rf /workspace/* /workspace/.*" 2>/dev/null || true
            rmdir "${RESET_WORKSPACE_DIR}" 2>/dev/null || true
            echo "Workspace directory cleaned."
        fi
        # Clean .alcatrazer/
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

# --- Step 3.5: Resolve workspace directory ---
# The workspace lives in a separate directory from .alcatrazer/ (not inside it).
# This prevents the host path ".alcatrazer" from leaking via Docker's /proc/self/mountinfo.
# The directory name is randomly generated and user-selected during first init.

WORKSPACE_DIR_FILE="${ALCATRAZ_DIR}/workspace-dir"
if [ -f "${WORKSPACE_DIR_FILE}" ]; then
    WORKSPACE_NAME=$(cat "${WORKSPACE_DIR_FILE}" | tr -d '[:space:]')
else
    # First init — prompt user to select a workspace directory name
    echo ""
    echo "Choose a workspace directory name (this will be mounted into Docker):"
    CHOICES=$(PYTHONPATH="${SCRIPT_DIR}" "${PYTHON}" -c "
from alcatrazer.identity import generate_workspace_choices
for c in generate_workspace_choices('${PROJECT_DIR}'):
    print(c)
")
    CHOICE1=$(echo "${CHOICES}" | sed -n '1p')
    CHOICE2=$(echo "${CHOICES}" | sed -n '2p')
    CHOICE3=$(echo "${CHOICES}" | sed -n '3p')
    echo "  1. ${CHOICE1}"
    echo "  2. ${CHOICE2}"
    echo "  3. ${CHOICE3}"
    echo ""
    read -rp "Choose [1/2/3]: " PICK
    case "${PICK}" in
        1) WORKSPACE_NAME="${CHOICE1}" ;;
        2) WORKSPACE_NAME="${CHOICE2}" ;;
        3) WORKSPACE_NAME="${CHOICE3}" ;;
        *) WORKSPACE_NAME="${CHOICE1}" ;;
    esac
    echo "${WORKSPACE_NAME}" > "${WORKSPACE_DIR_FILE}"
    echo "Workspace directory: ${WORKSPACE_NAME}"
fi
WORKSPACE_DIR="${PROJECT_DIR}/${WORKSPACE_NAME}"

# Add workspace directory to .gitignore if not already present
GITIGNORE_FILE="${PROJECT_DIR}/.gitignore"
if [ -f "${GITIGNORE_FILE}" ]; then
    if ! grep -qxF "${WORKSPACE_NAME}/" "${GITIGNORE_FILE}" 2>/dev/null; then
        echo "${WORKSPACE_NAME}/" >> "${GITIGNORE_FILE}"
    fi
else
    echo "${WORKSPACE_NAME}/" > "${GITIGNORE_FILE}"
fi

# --- Step 4: Initialize inner git repo ---

if [ -d "${WORKSPACE_DIR}/.git" ]; then
    echo "Workspace git repo already exists at ${WORKSPACE_DIR}/.git"
    echo "To reinitialize, run: ./src/initialize_alcatraz.sh --reset"
else
    # Create workspace directory at repo root (separate from .alcatrazer/)
    mkdir -p "${WORKSPACE_DIR}"

    # Initialize a fresh git repo
    git init "${WORKSPACE_DIR}"

    # Generate random agent identity (or reuse existing one)
    IDENTITY=$(PYTHONPATH="${SCRIPT_DIR}" "${PYTHON}" -m alcatrazer.identity "${ALCATRAZ_DIR}")
    AGENT_NAME=$(echo "${IDENTITY}" | head -1)
    AGENT_EMAIL=$(echo "${IDENTITY}" | tail -1)

    git -C "${WORKSPACE_DIR}" config user.name "${AGENT_NAME}"
    git -C "${WORKSPACE_DIR}" config user.email "${AGENT_EMAIL}"

    # Disable commit signing - no access to host signing keys
    git -C "${WORKSPACE_DIR}" config commit.gpgsign false

    # Override signing key paths with empty values to prevent leaking host paths
    # (in case global gitconfig somehow becomes visible)
    git -C "${WORKSPACE_DIR}" config user.signingkey ""
    git -C "${WORKSPACE_DIR}" config gpg.ssh.allowedSignersFile ""

    echo ""
    echo "Workspace git repo initialized at: ${WORKSPACE_DIR}"

    # --- Step 5: Snapshot outer repo into workspace ---
    # Copies current main branch files (no history) into workspace.
    # Filters .alcatrazer/ from .gitignore, excludes .env and .alcatrazer/.
    # Also excludes the workspace dir name from snapshot (in case it was tracked).
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
SUMMARY_NAME=$(head -1 "${ALCATRAZ_DIR}/agent-identity" 2>/dev/null || echo "unknown")
SUMMARY_EMAIL=$(tail -1 "${ALCATRAZ_DIR}/agent-identity" 2>/dev/null || echo "unknown")
echo "  Git identity: ${SUMMARY_NAME} <${SUMMARY_EMAIL}>"
echo ""
echo "Local git config:"
git -C "${WORKSPACE_DIR}" config --local --list
echo ""
echo "Next steps:"
echo "  1. Fill in API keys in .env"
echo "  2. Run: docker compose -f container/docker-compose.yml build"
echo "  3. Run: docker compose -f container/docker-compose.yml run --rm alcatraz"