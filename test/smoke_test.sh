#!/usr/bin/env bash
#
# Smoke test for the Alcatraz Docker infrastructure.
#
# Verifies that the container is properly isolated from the host:
# - Runs as a phantom UID (no matching host user)
# - No access to host credentials, SSH keys, or signing keys
# - Only explicitly passed environment variables are visible
# - All development tools are available (Python, Node, Bun, Git, Tmux, etc.)
# - Agents can commit and branch inside the Alcatraz workspace
# - Docker socket is not accessible
# - No git remotes are configured
#
# Prerequisites:
#   ./src/initialize_alcatraz.sh
#   alcatrazer.toml must exist in project root
#   docker compose build
#
# Usage:
#   ./test/smoke_test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

cd "${PROJECT_DIR}"

# Load expected ALCATRAZ_UID from .env
EXPECTED_UID=$(grep -oP '^ALCATRAZ_UID=\K.*' .env)

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "========================================="
echo "  Alcatraz Smoke Test"
echo "========================================="

# Run all checks inside a single container invocation and capture output
OUTPUT=$(docker compose -f container/docker-compose.yml run --rm alcatraz bash -c '
# Delimiter-separated sections for reliable parsing
echo "===SECTION:ID==="
id
echo "===SECTION:WHOAMI==="
whoami
echo "===SECTION:SSH==="
ls -d ~/.ssh 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GNUPG==="
ls -d ~/.gnupg 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GITCONFIG==="
cat ~/.gitconfig
echo "===SECTION:SIGNINGKEY==="
git config user.signingkey || echo ""
echo "===SECTION:GPGSIGN==="
git config commit.gpgsign
echo "===SECTION:ENV_SECRETS==="
env | grep -iE "key|token|secret|pass" | sort
echo "===SECTION:PYTHON==="
python --version 2>&1
echo "===SECTION:NODE==="
node --version 2>&1
echo "===SECTION:BUN==="
bun --version 2>&1
echo "===SECTION:GIT==="
git --version 2>&1
echo "===SECTION:MISE==="
mise --version 2>&1
echo "===SECTION:TMUX==="
tmux -V 2>&1
echo "===SECTION:RIPGREP==="
rg --version 2>&1 | head -1
echo "===SECTION:CLAUDE==="
claude --version 2>&1
echo "===SECTION:MISE_LS==="
mise ls 2>&1
echo "===SECTION:WORKSPACE_GIT_CONFIG==="
git -C /workspace config --local --list 2>&1
echo "===SECTION:COMMIT_TEST==="
cd /workspace
echo "print(\"hello from alcatraz\")" > _smoke_test.py
git add _smoke_test.py
git commit -m "smoke test: verify agent can commit" 2>&1
git log -1 --format="%an|%ae|%cn|%ce" 2>&1
echo "===SECTION:BRANCH_TEST==="
git checkout -b smoke-test/feature 2>&1
echo "feature work" > _smoke_feature.txt
git add _smoke_feature.txt
git commit -m "smoke test: feature branch commit" 2>&1
git checkout main 2>&1
git merge smoke-test/feature --no-edit 2>&1
git log --oneline --graph --all 2>&1
echo "===SECTION:PYTHON_EXEC==="
python _smoke_test.py 2>&1
echo "===SECTION:NODE_EXEC==="
node -e "console.log(\"hello from node\")" 2>&1
echo "===SECTION:FILE_OWNERSHIP==="
ls -ln /workspace/_smoke_test.py 2>&1
echo "===SECTION:DOCKER_SOCKET==="
ls -la /var/run/docker.sock 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GIT_REMOTES==="
git remote -v 2>&1 || echo "NONE"
echo "===SECTION:CLEANUP==="
cd /workspace
git checkout main 2>/dev/null
git branch -d smoke-test/feature 2>/dev/null || true
git rm -f _smoke_test.py _smoke_feature.txt 2>/dev/null
git commit -m "smoke test: cleanup" 2>/dev/null
git reset --hard HEAD~3 2>/dev/null
echo "CLEANED"
echo "===SECTION:END==="
' 2>&1)

get_section() {
    echo "${OUTPUT}" | sed -n "/===SECTION:$1===/,/===SECTION:/p" | sed '1d;$d'
}

# --- 1. User identity ---
echo ""
echo "--- 1. Container user identity ---"
SECTION=$(get_section "ID")
if echo "${SECTION}" | grep -q "uid=${EXPECTED_UID}"; then
    pass "Container runs as phantom UID ${EXPECTED_UID}"
else
    fail "Expected UID ${EXPECTED_UID}, got: ${SECTION}"
fi

SECTION=$(get_section "WHOAMI")
if echo "${SECTION}" | grep -q "agent"; then
    pass "Container user is 'agent'"
else
    fail "Expected user 'agent', got: ${SECTION}"
fi

# --- 2. No host credential leaks ---
echo ""
echo "--- 2. Host credential isolation ---"
SECTION=$(get_section "SSH")
if echo "${SECTION}" | grep -q "MISSING"; then
    pass "No ~/.ssh directory"
else
    fail "~/.ssh is accessible inside container"
fi

SECTION=$(get_section "GNUPG")
if echo "${SECTION}" | grep -q "MISSING"; then
    pass "No ~/.gnupg directory"
else
    fail "~/.gnupg is accessible inside container"
fi

SECTION=$(get_section "GITCONFIG")
if echo "${SECTION}" | grep -q "Alcatraz Agent" && ! echo "${SECTION}" | grep -qi "signingkey\s*=\s*/."; then
    pass "Git config has Alcatraz identity, no host signing key paths"
else
    fail "Git config may leak host info: ${SECTION}"
fi

SECTION=$(get_section "SIGNINGKEY")
if [ -z "$(echo "${SECTION}" | tr -d '[:space:]')" ]; then
    pass "Signing key is empty"
else
    fail "Signing key is set: ${SECTION}"
fi

SECTION=$(get_section "GPGSIGN")
if echo "${SECTION}" | grep -q "false"; then
    pass "Commit signing is disabled"
else
    fail "Commit signing is not disabled: ${SECTION}"
fi

# --- 3. Environment variables ---
echo ""
echo "--- 3. Environment variables ---"
SECTION=$(get_section "ENV_SECRETS")
# Only env vars we explicitly pass should appear — no host secrets leaked
LEAKED=$(echo "${SECTION}" | grep -ivE "ANTHROPIC_API_KEY|OPENAI_API_KEY|MINIMAX_API_KEY|ALCATRAZ_UID" || true)
if [ -z "${LEAKED}" ]; then
    pass "No leaked secret-like environment variables from host"
else
    fail "Unexpected secret-like env vars found: ${LEAKED}"
fi

# --- 4. Development tools ---
echo ""
echo "--- 4. Development tools ---"
for TOOL in PYTHON NODE BUN GIT MISE TMUX RIPGREP CLAUDE; do
    SECTION=$(get_section "${TOOL}")
    if [ -n "$(echo "${SECTION}" | tr -d '[:space:]')" ]; then
        pass "${TOOL}: $(echo "${SECTION}" | head -1)"
    else
        fail "${TOOL}: not available"
    fi
done

# --- 5. Mise runtime management ---
echo ""
echo "--- 5. Mise runtime management ---"
SECTION=$(get_section "MISE_LS")
for TOOL in python node bun; do
    if echo "${SECTION}" | grep -q "${TOOL}"; then
        pass "mise manages ${TOOL}"
    else
        fail "mise does not manage ${TOOL}"
    fi
done

# --- 6. Workspace git config ---
echo ""
echo "--- 6. Workspace git config ---"
SECTION=$(get_section "WORKSPACE_GIT_CONFIG")
if echo "${SECTION}" | grep -q "user.name=Alcatraz Agent"; then
    pass "Workspace git user.name is Alcatraz Agent"
else
    fail "Workspace git user.name mismatch: ${SECTION}"
fi
if echo "${SECTION}" | grep -q "user.email=alcatraz@localhost"; then
    pass "Workspace git user.email is alcatraz@localhost"
else
    fail "Workspace git user.email mismatch: ${SECTION}"
fi

# --- 7. Git commit test ---
echo ""
echo "--- 7. Git commit ---"
SECTION=$(get_section "COMMIT_TEST")
if echo "${SECTION}" | grep -q "Alcatraz Agent|alcatraz@localhost|Alcatraz Agent|alcatraz@localhost"; then
    pass "Commits are authored and committed as Alcatraz Agent"
else
    fail "Commit identity mismatch: $(echo "${SECTION}" | tail -1)"
fi

# --- 8. Branch and merge ---
echo ""
echo "--- 8. Branch and merge ---"
SECTION=$(get_section "BRANCH_TEST")
if echo "${SECTION}" | grep -q "smoke-test/feature" || echo "${SECTION}" | grep -q "feature branch"; then
    pass "Branching and merging works"
else
    fail "Branch/merge test failed: ${SECTION}"
fi

# --- 9. Code execution ---
echo ""
echo "--- 9. Code execution ---"
SECTION=$(get_section "PYTHON_EXEC")
if echo "${SECTION}" | grep -q "hello from alcatraz"; then
    pass "Python execution works"
else
    fail "Python execution failed: ${SECTION}"
fi

SECTION=$(get_section "NODE_EXEC")
if echo "${SECTION}" | grep -q "hello from node"; then
    pass "Node execution works"
else
    fail "Node execution failed: ${SECTION}"
fi

# --- 10. File ownership ---
echo ""
echo "--- 10. File ownership ---"
SECTION=$(get_section "FILE_OWNERSHIP")
if echo "${SECTION}" | grep -q "${EXPECTED_UID}.*${EXPECTED_UID}"; then
    pass "Files owned by phantom UID ${EXPECTED_UID}"
else
    fail "File ownership mismatch: ${SECTION}"
fi

# --- 11. Docker socket ---
echo ""
echo "--- 11. Docker socket ---"
SECTION=$(get_section "DOCKER_SOCKET")
if echo "${SECTION}" | grep -q "MISSING"; then
    pass "Docker socket is not mounted"
else
    fail "Docker socket is accessible"
fi

# --- 12. No git remotes ---
echo ""
echo "--- 12. Git remotes ---"
SECTION=$(get_section "GIT_REMOTES")
if [ -z "$(echo "${SECTION}" | grep -v "^NONE$" | tr -d '[:space:]')" ]; then
    pass "No git remotes configured"
else
    fail "Git remotes found: ${SECTION}"
fi

# --- Cleanup confirmation ---
echo ""
echo "--- Cleanup ---"
SECTION=$(get_section "CLEANUP")
if echo "${SECTION}" | grep -q "CLEANED"; then
    pass "Container cleaned up test artifacts"
else
    fail "Cleanup may have failed: ${SECTION}"
fi

# --- 13. Dockerfile rejects build without ALCATRAZ_UID ---
echo ""
echo "--- 13. Dockerfile requires ALCATRAZ_UID ---"
BUILD_OUTPUT=$(docker build --build-arg ALCATRAZ_UID="" -f "${PROJECT_DIR}/container/Dockerfile" "${PROJECT_DIR}" 2>&1 || true)
if echo "${BUILD_OUTPUT}" | grep -q "ALCATRAZ_UID build arg is required"; then
    pass "Dockerfile rejects build without ALCATRAZ_UID"
else
    fail "Dockerfile should reject build without ALCATRAZ_UID"
fi

# --- Summary ---
echo ""
echo "========================================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "========================================="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi