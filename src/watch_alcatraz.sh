#!/usr/bin/env bash
#
# Auto-promotion daemon wrapper — reads the resolved Python path from
# .alcatraz/python and execs the Python daemon (watch_alcatraz.py).
#
# Usage:
#   src/watch_alcatraz.sh
#   src/watch_alcatraz.sh --alcatraz-dir <dir> --project-dir <dir>  # for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Parse --alcatraz-dir from args to find the python file, pass all args through
ALCATRAZ_DIR=""
PROJECT_DIR="${DEFAULT_PROJECT_DIR}"
ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --alcatraz-dir) ALCATRAZ_DIR="$2"; shift 2 ;;
        --project-dir)  PROJECT_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

ALCATRAZ_DIR="${ALCATRAZ_DIR:-${PROJECT_DIR}/.alcatraz}"
PYTHON_FILE="${ALCATRAZ_DIR}/python"

if [ ! -f "${PYTHON_FILE}" ]; then
    echo "ERROR: No Python path found at ${PYTHON_FILE}"
    echo "Run ./src/initialize_alcatraz.sh first."
    exit 1
fi

PYTHON=$(cat "${PYTHON_FILE}")

if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: Python at ${PYTHON} is not executable."
    echo "Run ./src/resolve_python.sh to re-resolve."
    exit 1
fi

exec "${PYTHON}" "${SCRIPT_DIR}/watch_alcatraz.py" "${ARGS[@]}"
