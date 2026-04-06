#!/usr/bin/env bash
#
# Auto-promotion daemon wrapper — .alcatraz/python is a symlink to
# the resolved Python 3.11+ interpreter. Just exec it.
#
# Usage:
#   src/watch_alcatraz.sh
#   src/watch_alcatraz.sh --alcatraz-dir <dir> --project-dir <dir>  # for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Parse --alcatraz-dir from args to find the python symlink, pass all args through
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
PYTHON="${ALCATRAZ_DIR}/python"

if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: No Python found at ${PYTHON}"
    echo "Run ./src/initialize_alcatraz.sh first."
    exit 1
fi

exec "${PYTHON}" "${SCRIPT_DIR}/watch_alcatraz.py" "${ARGS[@]}"
