#!/usr/bin/env bash
#
# Initialize the alcatrazer environment for agentic AI development in Docker.
#
# Bash bootstrap — handles pre-Python steps, then delegates to Python:
# 1. Guard: verify we are at the repository root
# 2. Ensure .env exists
# 3. Find phantom UID (getent — must be bash)
# 4. Resolve Python 3.11+ (resolve_python.sh)
# === PIVOT: Python available ===
# 5. Everything else: python -m alcatrazer.init
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script lives at src/alcatrazer/scripts/ — project root is 3 levels up
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# --- Guard: verify we are at the repository root ---

REPO_ROOT=$(git -C "${PROJECT_DIR}" rev-parse --show-toplevel 2>/dev/null) || {
    echo "ERROR: Not inside a git repository."
    echo "Alcatrazer must be initialized from within a git repository."
    exit 1
}

if [ "$(cd "${PROJECT_DIR}" && pwd)" != "$(cd "${REPO_ROOT}" && pwd)" ]; then
    echo "ERROR: Script is not at the repository root."
    echo "  Expected: ${REPO_ROOT}/src/alcatrazer/scripts/initialize_alcatraz.sh"
    echo "  Actual:   ${SCRIPT_DIR}/initialize_alcatraz.sh"
    echo "Alcatrazer must be installed at the root of your git repository."
    exit 1
fi

SRC_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ALCATRAZ_DIR="${PROJECT_DIR}/.alcatrazer"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

# --- Parse flags (pass through to Python) ---

ARGS=()
for arg in "$@"; do
    ARGS+=("${arg}")
done

# --- Step 1: Ensure .env exists ---

if [ ! -f "${ENV_FILE}" ]; then
    if [ -f "${ENV_EXAMPLE}" ]; then
        cp "${ENV_EXAMPLE}" "${ENV_FILE}"
        echo "Created .env from .env.example — fill in your API keys."
    else
        touch "${ENV_FILE}"
    fi
fi

# --- Step 2: Determine container user UID/GID ---
# Must be bash — uses getent to probe host passwd/group database.

UID_FILE="${ALCATRAZ_DIR}/uid"

if [ -f "${UID_FILE}" ]; then
    USER_UID=$(cat "${UID_FILE}")
    echo "Reusing stored UID: ${USER_UID} (from .alcatrazer/uid)"
else
    USER_UID=1001
    while getent passwd "${USER_UID}" >/dev/null 2>&1 || \
          getent group "${USER_UID}" >/dev/null 2>&1; do
        USER_UID=$((USER_UID + 1))
    done

    mkdir -p "${ALCATRAZ_DIR}"
    echo "${USER_UID}" > "${UID_FILE}"
    echo "Selected UID/GID: ${USER_UID} (written to .alcatrazer/uid)"
fi

# Write USER_UID to .env for docker-compose build arg interpolation
if grep -q "^USER_UID=" "${ENV_FILE}" 2>/dev/null; then
    sed -i "s|^USER_UID=.*|USER_UID=${USER_UID}|" "${ENV_FILE}"
else
    echo "USER_UID=${USER_UID}" >> "${ENV_FILE}"
fi

# Verify the UID still doesn't exist on this host (in case of machine change)
if getent passwd "${USER_UID}" >/dev/null 2>&1; then
    echo "WARNING: UID ${USER_UID} now exists on this host!"
    echo "Delete .alcatrazer/uid and re-run this script to pick a new UID."
    exit 1
fi

# --- Step 3: Resolve Python 3.11+ ---

"${SCRIPT_DIR}/resolve_python.sh" --alcatraz-dir "${ALCATRAZ_DIR}"

PYTHON="${ALCATRAZ_DIR}/python"

# === PIVOT: Python available — delegate everything else ===

PYTHONPATH="${SRC_DIR}" exec "${PYTHON}" -m alcatrazer.init "${PROJECT_DIR}" "${ALCATRAZ_DIR}" "${ARGS[@]}"
