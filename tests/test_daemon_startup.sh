#!/usr/bin/env bash
#
# TDD test for watch_alcatraz.sh — daemon startup behavior
#
# Tests:
# 1. Exits with error when .alcatraz/workspace/.git/ does not exist
# 2. Creates PID file on startup
# 3. PID file prevents double start
# 4. PID file is cleaned up on SIGTERM
# 5. Stale PID file (dead process) is overwritten
#
# Usage:
#   ./tests/test_daemon_startup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
DAEMON_SCRIPT="${PROJECT_DIR}/src/watch_alcatraz.sh"

# Use a temp directory to simulate .alcatraz/ without touching the real one
TEMP_DIR="${SCRIPT_DIR}/daemon_temp_output"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cleanup() {
    # Kill any leftover daemon processes from this test
    if [ -f "${TEMP_DIR}/alcatraz/promotion-daemon.pid" ]; then
        local pid
        pid=$(cat "${TEMP_DIR}/alcatraz/promotion-daemon.pid" 2>/dev/null || true)
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
            wait "${pid}" 2>/dev/null || true
        fi
    fi
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

echo "========================================="
echo "  Daemon Startup Test"
echo "========================================="

# --- Setup ---
rm -rf "${TEMP_DIR}"
mkdir -p "${TEMP_DIR}/alcatraz"

# --- Test 1: Exits when workspace/.git/ does not exist ---

echo ""
echo "--- Test 1: Exits when workspace missing ---"
OUTPUT=$("${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" 2>&1 || true)
EXIT_CODE=0
"${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" >/dev/null 2>&1 || EXIT_CODE=$?

if [ "${EXIT_CODE}" -ne 0 ]; then
    pass "Daemon exits with non-zero when workspace missing"
else
    fail "Daemon should exit with non-zero when workspace missing"
fi

if echo "${OUTPUT}" | grep -qi "workspace"; then
    pass "Error message mentions workspace"
else
    fail "Error message should mention workspace, got: ${OUTPUT}"
fi

# --- Setup workspace for remaining tests ---
mkdir -p "${TEMP_DIR}/alcatraz/workspace"
git init "${TEMP_DIR}/alcatraz/workspace" >/dev/null 2>&1

# Also need alcatrazer.toml for config
cp "${PROJECT_DIR}/alcatrazer.toml" "${TEMP_DIR}/alcatrazer.toml"

# --- Test 2: Creates PID file on startup ---

echo ""
echo "--- Test 2: Creates PID file on startup ---"
PID_FILE="${TEMP_DIR}/alcatraz/promotion-daemon.pid"

# Start daemon in background (it will poll forever, so we kill it after checking)
"${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" --project-dir "${TEMP_DIR}" &
DAEMON_PID=$!

# Give it a moment to start
sleep 1

if [ -f "${PID_FILE}" ]; then
    STORED_PID=$(cat "${PID_FILE}")
    if [ "${STORED_PID}" = "${DAEMON_PID}" ]; then
        pass "PID file created with correct PID (${DAEMON_PID})"
    else
        fail "PID file has wrong PID: expected ${DAEMON_PID}, got ${STORED_PID}"
    fi
else
    fail "PID file not created at ${PID_FILE}"
fi

# --- Test 3: PID file prevents double start ---

echo ""
echo "--- Test 3: PID file prevents double start ---"
DOUBLE_OUTPUT=$("${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" --project-dir "${TEMP_DIR}" 2>&1 || true)
DOUBLE_EXIT=0
"${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" --project-dir "${TEMP_DIR}" >/dev/null 2>&1 || DOUBLE_EXIT=$?

if [ "${DOUBLE_EXIT}" -ne 0 ]; then
    pass "Second daemon instance exits with non-zero"
else
    fail "Second daemon instance should exit with non-zero"
fi

if echo "${DOUBLE_OUTPUT}" | grep -qi "already running\|pid"; then
    pass "Double-start error message mentions already running or PID"
else
    fail "Double-start message unclear, got: ${DOUBLE_OUTPUT}"
fi

# --- Test 4: PID file cleaned up on SIGTERM ---

echo ""
echo "--- Test 4: PID file cleaned up on SIGTERM ---"
kill "${DAEMON_PID}" 2>/dev/null || true
wait "${DAEMON_PID}" 2>/dev/null || true

# Small delay for cleanup
sleep 0.5

if [ ! -f "${PID_FILE}" ]; then
    pass "PID file removed after SIGTERM"
else
    fail "PID file still exists after SIGTERM"
fi

# --- Test 5: Stale PID file is overwritten ---

echo ""
echo "--- Test 5: Stale PID file (dead process) is overwritten ---"

# Write a fake PID that doesn't exist
echo "99999" > "${PID_FILE}"

# Daemon should detect the stale PID and start anyway
"${DAEMON_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/alcatraz" --project-dir "${TEMP_DIR}" &
DAEMON_PID2=$!

sleep 1

if [ -f "${PID_FILE}" ]; then
    STORED_PID2=$(cat "${PID_FILE}")
    if [ "${STORED_PID2}" = "${DAEMON_PID2}" ]; then
        pass "Stale PID file overwritten with new PID (${DAEMON_PID2})"
    else
        fail "PID file not updated: expected ${DAEMON_PID2}, got ${STORED_PID2}"
    fi
else
    fail "PID file missing after stale PID recovery"
fi

# Clean up daemon
kill "${DAEMON_PID2}" 2>/dev/null || true
wait "${DAEMON_PID2}" 2>/dev/null || true

# --- Summary ---

echo ""
echo "========================================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "========================================="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
