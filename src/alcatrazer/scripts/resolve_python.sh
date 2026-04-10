#!/usr/bin/env bash
#
# Resolve a Python 3.11+ interpreter for the alcatrazer daemon.
#
# Four-tier fallback:
#   1. Detect python3 on PATH that is 3.11+
#   2. Detect mise → offer to install Python 3.11 via mise
#   3. No mise → offer to install mise, then Python 3.11 via mise
#   4. User declines everything → ask for manual path to Python 3.11+
#
# Creates .alcatrazer/python as a symlink to the resolved interpreter.
#
# Usage:
#   src/resolve_python.sh [--alcatraz-dir <dir>]
#
# Called by initialize_alcatraz.sh. Can also be run standalone for re-resolution.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script lives at src/alcatrazer/scripts/ — project root is 3 levels up
DEFAULT_PROJECT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# --- Parse arguments ---

ALCATRAZ_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --alcatraz-dir) ALCATRAZ_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

ALCATRAZ_DIR="${ALCATRAZ_DIR:-${DEFAULT_PROJECT_DIR}/.alcatrazer}"
PYTHON_FILE="${ALCATRAZ_DIR}/python"

MIN_MAJOR=3
MIN_MINOR=11

# --- Helper: validate a Python interpreter ---
# Returns 0 if the given path is Python >= 3.11 with tomllib available.

validate_python() {
    local py="$1"

    if [ ! -x "${py}" ]; then
        return 1
    fi

    # Check version
    local version_output
    version_output=$("${py}" --version 2>&1) || return 1

    local major minor
    major=$(echo "${version_output}" | grep -oP 'Python \K[0-9]+(?=\.)') || return 1
    minor=$(echo "${version_output}" | grep -oP 'Python [0-9]+\.\K[0-9]+') || return 1

    if [ "${major}" -lt "${MIN_MAJOR}" ] || { [ "${major}" -eq "${MIN_MAJOR}" ] && [ "${minor}" -lt "${MIN_MINOR}" ]; }; then
        return 1
    fi

    # Check tomllib is importable
    "${py}" -c "import tomllib" 2>/dev/null || return 1

    return 0
}

# --- Helper: resolve shims to the real interpreter path ---
# pyenv/mise shims delegate to the real binary — sys.executable gives
# the actual path so we're immune to version switches later.

resolve_real_path() {
    local py="$1"
    local real
    real=$("${py}" -c "import sys; print(sys.executable)" 2>/dev/null) || true
    if [ -n "${real}" ] && [ -x "${real}" ]; then
        echo "${real}"
    else
        echo "${py}"
    fi
}

# --- Check for previously resolved Python ---

if [ -L "${PYTHON_FILE}" ] || [ -x "${PYTHON_FILE}" ]; then
    if validate_python "${PYTHON_FILE}"; then
        RESOLVED_TARGET=$(readlink -f "${PYTHON_FILE}" 2>/dev/null || echo "${PYTHON_FILE}")
        VERSION=$("${PYTHON_FILE}" --version 2>&1)
        echo "Reusing already resolved Python: ${RESOLVED_TARGET} (${VERSION})"
        exit 0
    else
        echo "Previously resolved Python at ${PYTHON_FILE} is no longer valid. Re-resolving..."
        rm -f "${PYTHON_FILE}"
    fi
fi

mkdir -p "${ALCATRAZ_DIR}"

# --- Tier 1: Detect python3 on PATH ---

PYTHON3_PATH=$(command -v python3 2>/dev/null || true)

if [ -n "${PYTHON3_PATH}" ]; then
    if validate_python "${PYTHON3_PATH}"; then
        REAL_PATH=$(resolve_real_path "${PYTHON3_PATH}")
        VERSION=$("${REAL_PATH}" --version 2>&1)
        echo "Found ${VERSION} at ${REAL_PATH}"
        if [ "${REAL_PATH}" != "${PYTHON3_PATH}" ]; then
            echo "  (resolved from shim ${PYTHON3_PATH})"
        fi
        ln -sf "${REAL_PATH}" "${PYTHON_FILE}"
        echo "Symlinked .alcatrazer/python -> ${REAL_PATH}"
        exit 0
    else
        VERSION=$("${PYTHON3_PATH}" --version 2>&1 || echo "unknown")
        echo "Found python3 at ${PYTHON3_PATH} but it is ${VERSION} (need >= ${MIN_MAJOR}.${MIN_MINOR})."
    fi
fi

# --- Tier 2: mise available → offer to install Python ---

MISE_PATH=$(command -v mise 2>/dev/null || true)

if [ -n "${MISE_PATH}" ]; then
    echo ""
    echo "mise is available. Python ${MIN_MAJOR}.${MIN_MINOR}+ can be installed via mise."
    read -rp "Install Python ${MIN_MAJOR}.${MIN_MINOR} via mise? [y/N] " REPLY
    if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
        echo "Installing Python ${MIN_MAJOR}.${MIN_MINOR} via mise..."
        "${MISE_PATH}" use --global "python@${MIN_MAJOR}.${MIN_MINOR}"

        MISE_PYTHON=$("${MISE_PATH}" which python3 2>/dev/null || true)
        if [ -n "${MISE_PYTHON}" ] && validate_python "${MISE_PYTHON}"; then
            REAL_PATH=$(resolve_real_path "${MISE_PYTHON}")
            VERSION=$("${REAL_PATH}" --version 2>&1)
            echo "Installed ${VERSION} via mise at ${REAL_PATH}"
            ln -sf "${REAL_PATH}" "${PYTHON_FILE}"
            echo "Symlinked .alcatrazer/python -> ${REAL_PATH}"
            exit 0
        else
            echo "mise install completed but python3 validation failed."
        fi
    fi
fi

# --- Tier 3: No mise → offer to install mise, then Python ---

if [ -z "${MISE_PATH}" ]; then
    echo ""
    echo "No Python ${MIN_MAJOR}.${MIN_MINOR}+ or mise found."
    echo "mise is a lightweight tool version manager (single binary, no root required)."
    read -rp "Install mise and then Python ${MIN_MAJOR}.${MIN_MINOR}? [y/N] " REPLY
    if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
        echo "Installing mise..."
        curl -fsSL https://mise.run | sh

        # mise installs to ~/.local/bin by default
        MISE_PATH="${HOME}/.local/bin/mise"
        if [ -x "${MISE_PATH}" ]; then
            echo "mise installed at ${MISE_PATH}"
            echo "Installing Python ${MIN_MAJOR}.${MIN_MINOR} via mise..."
            "${MISE_PATH}" use --global "python@${MIN_MAJOR}.${MIN_MINOR}"

            MISE_PYTHON=$("${MISE_PATH}" which python3 2>/dev/null || true)
            if [ -n "${MISE_PYTHON}" ] && validate_python "${MISE_PYTHON}"; then
                REAL_PATH=$(resolve_real_path "${MISE_PYTHON}")
                VERSION=$("${REAL_PATH}" --version 2>&1)
                echo "Installed ${VERSION} via mise at ${REAL_PATH}"
                ln -sf "${REAL_PATH}" "${PYTHON_FILE}"
                echo "Symlinked .alcatrazer/python -> ${REAL_PATH}"
                exit 0
            else
                echo "mise Python install completed but validation failed."
            fi
        else
            echo "mise installation failed."
        fi
    fi
fi

# --- Tier 4: Ask for manual path ---

echo ""
echo "Could not automatically resolve Python ${MIN_MAJOR}.${MIN_MINOR}+."
read -rp "Provide path to Python ${MIN_MAJOR}.${MIN_MINOR}+ binary (or leave empty to abort): " MANUAL_PATH

if [ -n "${MANUAL_PATH}" ] && validate_python "${MANUAL_PATH}"; then
    REAL_PATH=$(resolve_real_path "${MANUAL_PATH}")
    VERSION=$("${REAL_PATH}" --version 2>&1)
    echo "Validated ${VERSION} at ${REAL_PATH}"
    ln -sf "${REAL_PATH}" "${PYTHON_FILE}"
    echo "Symlinked .alcatrazer/python -> ${REAL_PATH}"
    exit 0
fi

echo ""
echo "ERROR: Python ${MIN_MAJOR}.${MIN_MINOR}+ is required for the promotion daemon."
echo "Options:"
echo "  - Install Python ${MIN_MAJOR}.${MIN_MINOR}+ and re-run this script"
echo "  - Install mise (https://mise.run) and re-run this script"
echo "  - Run: src/alcatrazer/scripts/resolve_python.sh --alcatraz-dir .alcatrazer"
exit 1
