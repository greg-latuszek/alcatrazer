#!/usr/bin/env bash
#
# TDD test for Python 3.11+ resolution in initialize_alcatraz.sh
#
# Tests the four-tier fallback strategy:
# 1. Detects python3 3.11+ on PATH
# 2. Uses mise to install Python when python3 missing
# 3. Installs mise then Python when both missing
# 4. Accepts manual path from user when all else declined
#
# Strategy: extract the resolution logic into a standalone helper script
# (src/resolve_python.sh) that can be tested in isolation with a faked
# PATH and simulated stdin input.
#
# Usage:
#   ./test/test_python_resolution.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
RESOLVE_SCRIPT="${PROJECT_DIR}/src/resolve_python.sh"

TEMP_DIR="${SCRIPT_DIR}/python_resolution_temp"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cleanup() {
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

echo "========================================="
echo "  Python Resolution Test"
echo "========================================="

rm -rf "${TEMP_DIR}"

# =========================================================================
# Test 1: Detects python3 3.11+ on PATH
# =========================================================================

echo ""
echo "--- Test 1: Detects system python3 3.11+ ---"

mkdir -p "${TEMP_DIR}/t1/alcatraz" "${TEMP_DIR}/t1/fakebin"

# Create a fake python3 that reports 3.12.0
cat > "${TEMP_DIR}/t1/fakebin/python3" << 'PYEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.12.0"
elif [ "${1:-}" = "-c" ]; then
    # Handle version check and tomllib import test
    eval "$2"
fi
PYEOF
chmod +x "${TEMP_DIR}/t1/fakebin/python3"

OUTPUT=$(PATH="${TEMP_DIR}/t1/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t1/alcatraz" 2>&1) || true

PYTHON_FILE="${TEMP_DIR}/t1/alcatraz/python"
if [ -f "${PYTHON_FILE}" ]; then
    RESOLVED=$(cat "${PYTHON_FILE}")
    if [ "${RESOLVED}" = "${TEMP_DIR}/t1/fakebin/python3" ]; then
        pass "Resolved to system python3 at correct path"
    else
        fail "Wrong path resolved: ${RESOLVED}"
    fi
else
    fail "No .alcatraz/python file created. Output: ${OUTPUT}"
fi

if echo "${OUTPUT}" | grep -qi "python 3.12"; then
    pass "Output mentions detected Python version"
else
    fail "Output should mention Python version, got: ${OUTPUT}"
fi

# =========================================================================
# Test 2: No python3 on PATH, mise available — installs Python via mise
# =========================================================================

echo ""
echo "--- Test 2: No python3, mise installs Python ---"

mkdir -p "${TEMP_DIR}/t2/alcatraz" "${TEMP_DIR}/t2/fakebin"

# No python3 on PATH, but mise is available
# After "mise use", python3 becomes available — simulate this with a state file
cat > "${TEMP_DIR}/t2/fakebin/mise" << 'MEOF'
#!/usr/bin/env bash
case "$1" in
    use)
        # Simulate installing python — create a python3 binary in fakebin
        FAKEBIN="$(dirname "$0")"
        cat > "${FAKEBIN}/python3" << 'PYEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.11.9"
elif [ "${1:-}" = "-c" ]; then
    eval "$2"
fi
PYEOF
        chmod +x "${FAKEBIN}/python3"
        echo "mise: installing python@3.11..."
        ;;
    which)
        FAKEBIN="$(dirname "$0")"
        if [ -x "${FAKEBIN}/python3" ]; then
            echo "${FAKEBIN}/python3"
        else
            exit 1
        fi
        ;;
esac
MEOF
chmod +x "${TEMP_DIR}/t2/fakebin/mise"

# Simulate user answering "y" to "Install Python via mise?"
OUTPUT=$(echo "y" | PATH="${TEMP_DIR}/t2/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t2/alcatraz" 2>&1) || true

PYTHON_FILE="${TEMP_DIR}/t2/alcatraz/python"
if [ -f "${PYTHON_FILE}" ]; then
    pass "Python resolved via mise install"
else
    fail "No .alcatraz/python after mise install. Output: ${OUTPUT}"
fi

if echo "${OUTPUT}" | grep -qi "mise"; then
    pass "Output mentions mise"
else
    fail "Output should mention mise, got: ${OUTPUT}"
fi

# =========================================================================
# Test 3: No python3, no mise — installs mise then Python
# =========================================================================

echo ""
echo "--- Test 3: No python3, no mise — bootstrap both ---"

mkdir -p "${TEMP_DIR}/t3/alcatraz" "${TEMP_DIR}/t3/fakebin" "${TEMP_DIR}/t3/home/.local/bin"

# Empty fakebin — no python3, no mise
# We need a fake curl that "installs" mise
cat > "${TEMP_DIR}/t3/fakebin/curl" << CEOF
#!/usr/bin/env bash
# Fake curl that simulates mise installer — creates a mise binary
MISE_BIN="${TEMP_DIR}/t3/home/.local/bin/mise"
cat > "\${MISE_BIN}" << 'MEOF'
#!/usr/bin/env bash
case "\$1" in
    use)
        FAKEBIN="${TEMP_DIR}/t3/home/.local/bin"
        cat > "\${FAKEBIN}/python3" << 'PYEOF'
#!/usr/bin/env bash
if [ "\${1:-}" = "--version" ]; then
    echo "Python 3.11.11"
elif [ "\${1:-}" = "-c" ]; then
    eval "\$2"
fi
PYEOF
        chmod +x "\${FAKEBIN}/python3"
        echo "mise: installing python@3.11..."
        ;;
    which)
        FAKEBIN="${TEMP_DIR}/t3/home/.local/bin"
        if [ -x "\${FAKEBIN}/python3" ]; then
            echo "\${FAKEBIN}/python3"
        else
            exit 1
        fi
        ;;
esac
MEOF
chmod +x "\${MISE_BIN}"
CEOF
chmod +x "${TEMP_DIR}/t3/fakebin/curl"

# Fake bash to execute the piped installer (curl | sh pattern)
# The resolve script will do: curl https://mise.run | sh
# With our fake curl, it outputs nothing, but we need sh to run it
# Actually, let's make our fake curl output a script that creates mise:
cat > "${TEMP_DIR}/t3/fakebin/curl" << CEOF
#!/usr/bin/env bash
# Output a shell script that creates mise
cat << 'INSTALLER'
#!/usr/bin/env bash
MISE_BIN="${TEMP_DIR}/t3/home/.local/bin/mise"
mkdir -p "${TEMP_DIR}/t3/home/.local/bin"
cat > "\${MISE_BIN}" << 'MEOF'
#!/usr/bin/env bash
case "\$1" in
    use)
        FAKEBIN="\$(dirname "\$0")"
        cat > "\${FAKEBIN}/python3" << 'PYEOF'
#!/usr/bin/env bash
if [ "\${1:-}" = "--version" ]; then
    echo "Python 3.11.11"
elif [ "\${1:-}" = "-c" ]; then
    eval "\$2"
fi
PYEOF
        chmod +x "\${FAKEBIN}/python3"
        echo "mise: installing python@3.11..."
        ;;
    which)
        FAKEBIN="\$(dirname "\$0")"
        if [ -x "\${FAKEBIN}/python3" ]; then
            echo "\${FAKEBIN}/python3"
        else
            exit 1
        fi
        ;;
esac
MEOF
chmod +x "\${MISE_BIN}"
INSTALLER
CEOF
chmod +x "${TEMP_DIR}/t3/fakebin/curl"

# Simulate user answering "y" twice (install mise, then install python)
OUTPUT=$(printf "y\ny\n" | \
    PATH="${TEMP_DIR}/t3/fakebin" \
    HOME="${TEMP_DIR}/t3/home" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t3/alcatraz" 2>&1) || true

PYTHON_FILE="${TEMP_DIR}/t3/alcatraz/python"
if [ -f "${PYTHON_FILE}" ]; then
    pass "Python resolved after mise bootstrap"
else
    fail "No .alcatraz/python after mise bootstrap. Output: ${OUTPUT}"
fi

if echo "${OUTPUT}" | grep -qi "install mise"; then
    pass "Output mentions mise installation"
else
    fail "Output should mention mise installation, got: ${OUTPUT}"
fi

# =========================================================================
# Test 4: User provides manual path
# =========================================================================

echo ""
echo "--- Test 4: User provides manual Python path ---"

mkdir -p "${TEMP_DIR}/t4/alcatraz" "${TEMP_DIR}/t4/fakebin" "${TEMP_DIR}/t4/custom"

# Create python at a custom path (not on PATH)
cat > "${TEMP_DIR}/t4/custom/my-python" << 'PYEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.13.0"
elif [ "${1:-}" = "-c" ]; then
    eval "$2"
fi
PYEOF
chmod +x "${TEMP_DIR}/t4/custom/my-python"

# Simulate user declining all auto-options, then providing manual path
# "n" to mise install, "n" to mise bootstrap, then the path
OUTPUT=$(printf "n\nn\n${TEMP_DIR}/t4/custom/my-python\n" | \
    PATH="${TEMP_DIR}/t4/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t4/alcatraz" 2>&1) || true

PYTHON_FILE="${TEMP_DIR}/t4/alcatraz/python"
if [ -f "${PYTHON_FILE}" ]; then
    RESOLVED=$(cat "${PYTHON_FILE}")
    if [ "${RESOLVED}" = "${TEMP_DIR}/t4/custom/my-python" ]; then
        pass "Resolved to user-provided path"
    else
        fail "Wrong path: expected ${TEMP_DIR}/t4/custom/my-python, got ${RESOLVED}"
    fi
else
    fail "No .alcatraz/python after manual path. Output: ${OUTPUT}"
fi

# =========================================================================
# Test 5: Reuses previously resolved Python
# =========================================================================

echo ""
echo "--- Test 5: Reuses previously resolved .alcatraz/python ---"

mkdir -p "${TEMP_DIR}/t5/alcatraz" "${TEMP_DIR}/t5/fakebin"

# Create a fake python and pre-write .alcatraz/python
cat > "${TEMP_DIR}/t5/fakebin/mypython" << 'PYEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.11.9"
elif [ "${1:-}" = "-c" ]; then
    eval "$2"
fi
PYEOF
chmod +x "${TEMP_DIR}/t5/fakebin/mypython"
echo "${TEMP_DIR}/t5/fakebin/mypython" > "${TEMP_DIR}/t5/alcatraz/python"

OUTPUT=$(PATH="${TEMP_DIR}/t5/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t5/alcatraz" 2>&1) || true

if echo "${OUTPUT}" | grep -qi "reusing\|already"; then
    pass "Reuses existing .alcatraz/python without re-resolving"
else
    fail "Should reuse existing python, got: ${OUTPUT}"
fi

# =========================================================================
# Test 6: Rejects Python < 3.11
# =========================================================================

echo ""
echo "--- Test 6: Rejects Python < 3.11 ---"

mkdir -p "${TEMP_DIR}/t6/alcatraz" "${TEMP_DIR}/t6/fakebin"

cat > "${TEMP_DIR}/t6/fakebin/python3" << 'PYEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.10.4"
elif [ "${1:-}" = "-c" ]; then
    if echo "$2" | grep -q "tomllib"; then
        echo "ModuleNotFoundError: No module named 'tomllib'" >&2
        exit 1
    fi
    eval "$2"
fi
PYEOF
chmod +x "${TEMP_DIR}/t6/fakebin/python3"

# No mise either, user provides nothing — should fail
OUTPUT=$(printf "n\n\n" | PATH="${TEMP_DIR}/t6/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t6/alcatraz" 2>&1) || true
EXIT_CODE=0
printf "n\n\n" | PATH="${TEMP_DIR}/t6/fakebin" \
    "${RESOLVE_SCRIPT}" --alcatraz-dir "${TEMP_DIR}/t6/alcatraz" >/dev/null 2>&1 || EXIT_CODE=$?

if [ "${EXIT_CODE}" -ne 0 ]; then
    pass "Exits with error when no valid Python available"
else
    fail "Should exit with error when Python < 3.11 and no alternatives"
fi

# --- Summary ---

echo ""
echo "========================================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "========================================="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
