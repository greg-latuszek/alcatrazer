#!/usr/bin/env bash
#
# TDD test for scripts/promote.sh
#
# This test:
# 1. Creates a temporary alcatraz git repo (simulating the inner repo)
# 2. Seeds it with realistic branch/merge history
# 3. Creates a temporary target git repo (simulating the outer repo)
# 4. Runs promote.sh to transfer commits from alcatraz to target
# 5. Compares both repos: same topology, same content, different author identity
#
# All temporary repos are created under test/promotion_temp_output/ (gitignored).
#
# Usage:
#   ./test/test_promote.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
PROMOTE_SCRIPT="${PROJECT_DIR}/src/promote.sh"
SEED_SCRIPT="${SCRIPT_DIR}/seed_alcatraz.sh"
TEMP_DIR="${SCRIPT_DIR}/promotion_temp_output"

# Expected identities
ALCATRAZ_NAME="Alcatraz Agent"
ALCATRAZ_EMAIL="alcatraz@localhost"
PROMOTED_NAME="Test User"
PROMOTED_EMAIL="test@example.com"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# --- Setup ---

echo "========================================="
echo "  Promotion Script Test"
echo "========================================="

# Clean up previous test runs
rm -rf "${TEMP_DIR}"
mkdir -p "${TEMP_DIR}"

SOURCE_REPO="${TEMP_DIR}/source"
TARGET_REPO="${TEMP_DIR}/target"

# Create and seed the source repo (simulates inner/alcatraz repo)
echo ""
echo "--- Setup: creating and seeding source repo ---"
mkdir -p "${SOURCE_REPO}"
git init "${SOURCE_REPO}"
git -C "${SOURCE_REPO}" config user.name "${ALCATRAZ_NAME}"
git -C "${SOURCE_REPO}" config user.email "${ALCATRAZ_EMAIL}"
git -C "${SOURCE_REPO}" config commit.gpgsign false

"${SEED_SCRIPT}" "${SOURCE_REPO}"

# Create the target repo (simulates outer repo)
echo ""
echo "--- Setup: creating target repo ---"
mkdir -p "${TARGET_REPO}"
git init "${TARGET_REPO}"
git -C "${TARGET_REPO}" config user.name "${PROMOTED_NAME}"
git -C "${TARGET_REPO}" config user.email "${PROMOTED_EMAIL}"
git -C "${TARGET_REPO}" config commit.gpgsign false

# --- Run promotion ---

echo ""
echo "--- Running promote.sh ---"
if ! "${PROMOTE_SCRIPT}" \
    --source "${SOURCE_REPO}" \
    --target "${TARGET_REPO}" \
    --author-name "${PROMOTED_NAME}" \
    --author-email "${PROMOTED_EMAIL}"; then
    echo ""
    echo "ERROR: promote.sh failed"
    rm -rf "${TEMP_DIR}"
    exit 1
fi

# --- Test 1: Same number of commits ---

echo ""
echo "--- Test 1: Commit count ---"
SOURCE_COUNT=$(git -C "${SOURCE_REPO}" rev-list --all --count)
TARGET_COUNT=$(git -C "${TARGET_REPO}" rev-list --all --count)
if [ "${SOURCE_COUNT}" = "${TARGET_COUNT}" ]; then
    pass "Same number of commits: ${SOURCE_COUNT}"
else
    fail "Commit count mismatch: source=${SOURCE_COUNT}, target=${TARGET_COUNT}"
fi

# --- Test 2: Same branches ---

echo ""
echo "--- Test 2: Branch names ---"
SOURCE_BRANCHES=$(git -C "${SOURCE_REPO}" branch --format='%(refname:short)' | sort)
TARGET_BRANCHES=$(git -C "${TARGET_REPO}" branch --format='%(refname:short)' | sort)
if [ "${SOURCE_BRANCHES}" = "${TARGET_BRANCHES}" ]; then
    pass "Same branches: $(echo "${SOURCE_BRANCHES}" | tr '\n' ' ')"
else
    fail "Branch mismatch: source=[${SOURCE_BRANCHES}] target=[${TARGET_BRANCHES}]"
fi

# --- Test 3: Same commit messages (in topological order) ---

echo ""
echo "--- Test 3: Commit messages ---"
SOURCE_MESSAGES=$(git -C "${SOURCE_REPO}" log --all --topo-order --format="%s" | sort)
TARGET_MESSAGES=$(git -C "${TARGET_REPO}" log --all --topo-order --format="%s" | sort)
if [ "${SOURCE_MESSAGES}" = "${TARGET_MESSAGES}" ]; then
    pass "Same commit messages"
else
    fail "Commit messages differ"
    diff <(echo "${SOURCE_MESSAGES}") <(echo "${TARGET_MESSAGES}") || true
fi

# --- Test 4: Same tree structure (merge topology) ---

echo ""
echo "--- Test 4: Merge topology ---"
# Compare parent counts per commit message to verify merge structure is preserved
SOURCE_TOPOLOGY=$(git -C "${SOURCE_REPO}" log --all --topo-order --format="%s|%P" | while IFS='|' read -r msg parents; do
    pcount=$(echo "${parents}" | wc -w)
    echo "${msg}|${pcount}"
done | sort)
TARGET_TOPOLOGY=$(git -C "${TARGET_REPO}" log --all --topo-order --format="%s|%P" | while IFS='|' read -r msg parents; do
    pcount=$(echo "${parents}" | wc -w)
    echo "${msg}|${pcount}"
done | sort)
if [ "${SOURCE_TOPOLOGY}" = "${TARGET_TOPOLOGY}" ]; then
    pass "Same merge topology (parent counts match per commit)"
else
    fail "Merge topology differs"
    diff <(echo "${SOURCE_TOPOLOGY}") <(echo "${TARGET_TOPOLOGY}") || true
fi

# --- Test 5: Same file content on main ---

echo ""
echo "--- Test 5: File content on main ---"
SOURCE_TREE=$(git -C "${SOURCE_REPO}" ls-tree -r --name-only main | sort)
TARGET_TREE=$(git -C "${TARGET_REPO}" ls-tree -r --name-only main | sort)
if [ "${SOURCE_TREE}" = "${TARGET_TREE}" ]; then
    pass "Same files on main: $(echo "${SOURCE_TREE}" | tr '\n' ' ')"
else
    fail "File list differs on main"
    diff <(echo "${SOURCE_TREE}") <(echo "${TARGET_TREE}") || true
fi

# Compare actual content
CONTENT_MATCH=true
for file in $(git -C "${SOURCE_REPO}" ls-tree -r --name-only main); do
    SOURCE_HASH=$(git -C "${SOURCE_REPO}" show "main:${file}" | sha256sum)
    TARGET_HASH=$(git -C "${TARGET_REPO}" show "main:${file}" | sha256sum)
    if [ "${SOURCE_HASH}" != "${TARGET_HASH}" ]; then
        fail "Content differs: ${file}"
        CONTENT_MATCH=false
    fi
done
if [ "${CONTENT_MATCH}" = true ]; then
    pass "File contents match on main"
fi

# --- Test 6: Author identity rewritten ---

echo ""
echo "--- Test 6: Author identity ---"

# Source should have alcatraz identity
SOURCE_AUTHORS=$(git -C "${SOURCE_REPO}" log --all --format="%an <%ae>" | sort -u)
if [ "${SOURCE_AUTHORS}" = "${ALCATRAZ_NAME} <${ALCATRAZ_EMAIL}>" ]; then
    pass "Source commits have alcatraz identity"
else
    fail "Source has unexpected authors: ${SOURCE_AUTHORS}"
fi

# Target should have promoted identity
TARGET_AUTHORS=$(git -C "${TARGET_REPO}" log --all --format="%an <%ae>" | sort -u)
if [ "${TARGET_AUTHORS}" = "${PROMOTED_NAME} <${PROMOTED_EMAIL}>" ]; then
    pass "Target commits have promoted identity"
else
    fail "Target has unexpected authors: ${TARGET_AUTHORS}"
fi

# Same for committer
TARGET_COMMITTERS=$(git -C "${TARGET_REPO}" log --all --format="%cn <%ce>" | sort -u)
if [ "${TARGET_COMMITTERS}" = "${PROMOTED_NAME} <${PROMOTED_EMAIL}>" ]; then
    pass "Target committer identity is rewritten"
else
    fail "Target has unexpected committers: ${TARGET_COMMITTERS}"
fi

# --- Test 7: Incremental promotion ---

echo ""
echo "--- Test 7: Incremental promotion ---"

# Add a new commit to source
git -C "${SOURCE_REPO}" checkout main
echo "new feature" > "${SOURCE_REPO}/new_feature.py"
git -C "${SOURCE_REPO}" add new_feature.py
git -C "${SOURCE_REPO}" commit -m "add new feature after first promotion"

# Run promotion again
"${PROMOTE_SCRIPT}" \
    --source "${SOURCE_REPO}" \
    --target "${TARGET_REPO}" \
    --author-name "${PROMOTED_NAME}" \
    --author-email "${PROMOTED_EMAIL}"

# Check the new commit appeared
TARGET_COUNT_AFTER=$(git -C "${TARGET_REPO}" rev-list --all --count)
SOURCE_COUNT_AFTER=$(git -C "${SOURCE_REPO}" rev-list --all --count)
if [ "${SOURCE_COUNT_AFTER}" = "${TARGET_COUNT_AFTER}" ]; then
    pass "Incremental promotion added new commit (${TARGET_COUNT_AFTER} total)"
else
    fail "Incremental promotion count mismatch: source=${SOURCE_COUNT_AFTER}, target=${TARGET_COUNT_AFTER}"
fi

# Verify the new commit message is there
if git -C "${TARGET_REPO}" log --all --format="%s" | grep -q "add new feature after first promotion"; then
    pass "New commit message present in target"
else
    fail "New commit message missing from target"
fi

# Verify new commit also has rewritten identity
NEW_AUTHOR=$(git -C "${TARGET_REPO}" log -1 --format="%an <%ae>" main)
if [ "${NEW_AUTHOR}" = "${PROMOTED_NAME} <${PROMOTED_EMAIL}>" ]; then
    pass "Incremental commit has promoted identity"
else
    fail "Incremental commit has wrong identity: ${NEW_AUTHOR}"
fi

# --- Test 8: Dry run — nothing to promote (target is up to date) ---

echo ""
echo "--- Test 8: Dry run (up to date) ---"
DRY_OUTPUT=$("${PROMOTE_SCRIPT}" \
    --source "${SOURCE_REPO}" \
    --target "${TARGET_REPO}" \
    --author-name "${PROMOTED_NAME}" \
    --author-email "${PROMOTED_EMAIL}" \
    --dry-run)

if echo "${DRY_OUTPUT}" | grep -q "Nothing to promote"; then
    pass "Dry run reports nothing to promote when up to date"
else
    fail "Dry run should report nothing to promote, got: ${DRY_OUTPUT}"
fi

# Verify dry run didn't modify the target
TARGET_COUNT_BEFORE_DRY=${TARGET_COUNT_AFTER}
TARGET_COUNT_AFTER_DRY=$(git -C "${TARGET_REPO}" rev-list --all --count)
if [ "${TARGET_COUNT_BEFORE_DRY}" = "${TARGET_COUNT_AFTER_DRY}" ]; then
    pass "Dry run did not modify target repo"
else
    fail "Dry run modified target repo: before=${TARGET_COUNT_BEFORE_DRY}, after=${TARGET_COUNT_AFTER_DRY}"
fi

# --- Test 9: Dry run — with pending commits ---

echo ""
echo "--- Test 9: Dry run (with pending commits) ---"

# Add new commits to source
git -C "${SOURCE_REPO}" checkout main
echo "pending 1" > "${SOURCE_REPO}/pending1.py"
git -C "${SOURCE_REPO}" add pending1.py
git -C "${SOURCE_REPO}" commit -m "pending commit 1"

git -C "${SOURCE_REPO}" checkout -b dry-run-test-branch
echo "pending 2" > "${SOURCE_REPO}/pending2.py"
git -C "${SOURCE_REPO}" add pending2.py
git -C "${SOURCE_REPO}" commit -m "pending commit 2 on branch"
git -C "${SOURCE_REPO}" checkout main

DRY_OUTPUT=$("${PROMOTE_SCRIPT}" \
    --source "${SOURCE_REPO}" \
    --target "${TARGET_REPO}" \
    --author-name "${PROMOTED_NAME}" \
    --author-email "${PROMOTED_EMAIL}" \
    --dry-run)

if echo "${DRY_OUTPUT}" | grep -q "2 commit(s) would be promoted"; then
    pass "Dry run reports correct pending commit count"
else
    fail "Dry run commit count wrong, got: ${DRY_OUTPUT}"
fi

if echo "${DRY_OUTPUT}" | grep -q "${PROMOTED_NAME} <${PROMOTED_EMAIL}>"; then
    pass "Dry run shows target identity"
else
    fail "Dry run doesn't show target identity, got: ${DRY_OUTPUT}"
fi

# Verify dry run still didn't modify the target
TARGET_COUNT_STILL=$(git -C "${TARGET_REPO}" rev-list --all --count)
if [ "${TARGET_COUNT_STILL}" = "${TARGET_COUNT_AFTER_DRY}" ]; then
    pass "Dry run with pending commits did not modify target repo"
else
    fail "Dry run modified target repo: before=${TARGET_COUNT_AFTER_DRY}, after=${TARGET_COUNT_STILL}"
fi

# --- Cleanup ---

rm -rf "${TEMP_DIR}"

# --- Summary ---

echo ""
echo "========================================="
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "========================================="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi