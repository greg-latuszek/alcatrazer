#!/usr/bin/env bash
#
# Auto-promotion daemon — watches .alcatraz/workspace/ for new commits
# and promotes them to the outer repo using promote.sh.
#
# Runs on the host side, polling at a configurable interval.
# Silent by default — writes to .alcatraz/promotion-daemon.log.
#
# Usage:
#   src/watch_alcatraz.sh
#   src/watch_alcatraz.sh --alcatraz-dir <dir> --project-dir <dir>  # for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# --- Parse arguments ---

ALCATRAZ_DIR=""
PROJECT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --alcatraz-dir) ALCATRAZ_DIR="$2"; shift 2 ;;
        --project-dir)  PROJECT_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Defaults
PROJECT_DIR="${PROJECT_DIR:-${DEFAULT_PROJECT_DIR}}"
ALCATRAZ_DIR="${ALCATRAZ_DIR:-${PROJECT_DIR}/.alcatraz}"

WORKSPACE_DIR="${ALCATRAZ_DIR}/workspace"
PID_FILE="${ALCATRAZ_DIR}/promotion-daemon.pid"
TOML_FILE="${PROJECT_DIR}/alcatrazer.toml"

# --- Workspace existence check ---

if [ ! -d "${WORKSPACE_DIR}/.git" ]; then
    echo "ERROR: No workspace found at ${WORKSPACE_DIR}/.git"
    echo "Run ./src/initialize_alcatraz.sh first to create the workspace."
    exit 1
fi

# --- PID guard: single instance protection ---

if [ -f "${PID_FILE}" ]; then
    EXISTING_PID=$(cat "${PID_FILE}")
    if kill -0 "${EXISTING_PID}" 2>/dev/null; then
        echo "ERROR: Daemon already running (PID ${EXISTING_PID})."
        echo "Stop it first or remove ${PID_FILE} if stale."
        exit 1
    else
        # Stale PID file — process is dead, clean up
        rm -f "${PID_FILE}"
    fi
fi

# Write our PID
echo $$ > "${PID_FILE}"

# --- Clean shutdown: remove PID file on exit ---

cleanup() {
    rm -f "${PID_FILE}"
}
trap cleanup EXIT INT TERM

# --- Read config ---

INTERVAL=5
if [ -f "${TOML_FILE}" ]; then
    TOML_INTERVAL=$(grep -A20 '^\[promotion-daemon\]' "${TOML_FILE}" | grep '^interval' | sed 's/^interval *= *//' | tr -d ' ' || true)
    if [ -n "${TOML_INTERVAL}" ]; then
        INTERVAL="${TOML_INTERVAL}"
    fi
fi

# --- Main polling loop ---

while true; do
    # sleep & wait makes the loop interruptible by signals (SIGTERM/SIGINT)
    sleep "${INTERVAL}" &
    wait $! || break
done
