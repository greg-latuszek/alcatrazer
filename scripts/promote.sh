#!/usr/bin/env bash
#
# Promote commits from a source (sandbox) git repo to a target (outer) git repo.
# Rewrites author/committer identity while preserving full branch and merge topology.
#
# Uses git fast-export / fast-import with incremental mark files so only new
# commits are transferred on subsequent runs.
#
# Usage:
#   scripts/promote.sh \
#     --source <path-to-source-repo> \
#     --target <path-to-target-repo> \
#     --author-name "Your Name" \
#     --author-email "your@email.com" \
#     [--dry-run]

set -euo pipefail

# --- Parse arguments ---

SOURCE_REPO=""
TARGET_REPO=""
AUTHOR_NAME=""
AUTHOR_EMAIL=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)  SOURCE_REPO="$2"; shift 2 ;;
        --target)  TARGET_REPO="$2"; shift 2 ;;
        --author-name)  AUTHOR_NAME="$2"; shift 2 ;;
        --author-email) AUTHOR_EMAIL="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "${SOURCE_REPO}" ] || [ -z "${TARGET_REPO}" ] || \
   [ -z "${AUTHOR_NAME}" ] || [ -z "${AUTHOR_EMAIL}" ]; then
    echo "Usage: promote.sh --source <repo> --target <repo> --author-name <name> --author-email <email> [--dry-run]"
    exit 1
fi

# Resolve to absolute paths
SOURCE_REPO="$(cd "${SOURCE_REPO}" && pwd)"
TARGET_REPO="$(cd "${TARGET_REPO}" && pwd)"

if [ ! -d "${SOURCE_REPO}/.git" ]; then
    echo "ERROR: ${SOURCE_REPO} is not a git repository"
    exit 1
fi

if [ ! -d "${TARGET_REPO}/.git" ]; then
    echo "ERROR: ${TARGET_REPO} is not a git repository"
    exit 1
fi

# --- Mark files for incremental promotion ---
# Stored in the target repo's .git directory so they persist across runs

EXPORT_MARKS="${TARGET_REPO}/.git/promote-export-marks"
IMPORT_MARKS="${TARGET_REPO}/.git/promote-import-marks"

# Build fast-export arguments
EXPORT_ARGS=(--all)

if [ -f "${EXPORT_MARKS}" ]; then
    EXPORT_ARGS+=(--import-marks="${EXPORT_MARKS}")
fi
EXPORT_ARGS+=(--export-marks="${EXPORT_MARKS}")

# Build fast-import arguments
IMPORT_ARGS=(--force --quiet)

if [ -f "${IMPORT_MARKS}" ]; then
    IMPORT_ARGS+=(--import-marks="${IMPORT_MARKS}")
fi
IMPORT_ARGS+=(--export-marks="${IMPORT_MARKS}")

# --- Dry run: show what would be promoted ---

if [ "${DRY_RUN}" = true ]; then
    # Build dry-run export args: same as real export but without --export-marks
    # to avoid overwriting marks as a side effect
    DRY_EXPORT_ARGS=(--all)
    if [ -f "${EXPORT_MARKS}" ]; then
        DRY_EXPORT_ARGS+=(--import-marks="${EXPORT_MARKS}")
    fi

    STREAM=$(git -C "${SOURCE_REPO}" fast-export "${DRY_EXPORT_ARGS[@]}" 2>/dev/null || true)

    if [ -z "${STREAM}" ]; then
        COMMIT_COUNT=0
    else
        COMMIT_COUNT=$(printf '%s\n' "${STREAM}" | grep -c "^commit " || true)
    fi
    BRANCH_REFS=$(printf '%s\n' "${STREAM}" | grep "^commit " | sed 's/^commit //' | sort -u || true)

    if [ "${COMMIT_COUNT}" -eq 0 ]; then
        echo "Nothing to promote — target is up to date."
    else
        echo "Dry run: ${COMMIT_COUNT} commit(s) would be promoted"
        echo "Branches affected:"
        echo "${BRANCH_REFS}" | sed 's/^/  /'
        echo ""
        echo "Author/committer will be rewritten to: ${AUTHOR_NAME} <${AUTHOR_EMAIL}>"
    fi
    exit 0
fi

# --- Promote: fast-export | rewrite identity | fast-import ---

git -C "${SOURCE_REPO}" fast-export "${EXPORT_ARGS[@]}" | \
    sed \
        -e "s/^author .* <.*> \(.*\)$/author ${AUTHOR_NAME} <${AUTHOR_EMAIL}> \1/" \
        -e "s/^committer .* <.*> \(.*\)$/committer ${AUTHOR_NAME} <${AUTHOR_EMAIL}> \1/" | \
    git -C "${TARGET_REPO}" fast-import "${IMPORT_ARGS[@]}"

echo "Promotion complete: ${SOURCE_REPO} -> ${TARGET_REPO}"