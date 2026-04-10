#!/usr/bin/env bash
#
# Seed a git repository with a realistic branch/merge history that mimics
# what an agent swarm would produce:
#
#   1. Initial commit on main
#   2. Feature branch with multiple commits, merged to main
#   3. Two parallel branches from main, both merged to main
#
# Resulting graph:
#
#   * merge branch-b into main
#   |\
#   | * branch-b: commit 2
#   | * branch-b: commit 1
#   * \   merge branch-a into main
#   |\ \
#   | * | branch-a: commit 2
#   | * | branch-a: commit 1
#   |/ /
#   * / merge feature into main
#   |\|
#   | * feature: commit 2
#   | * feature: commit 1
#   |/
#   * initial commit
#
# Usage:
#   ./tests/seed_alcatraz.sh <path-to-git-repo>

set -euo pipefail

REPO_DIR="${1:?Usage: seed_alcatraz.sh <path-to-git-repo>}"

if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "ERROR: ${REPO_DIR} is not a git repository"
    exit 1
fi

cd "${REPO_DIR}"

# Helper to create a file and commit it
commit_file() {
    local filename="$1"
    local content="$2"
    local message="$3"
    echo "${content}" > "${filename}"
    git add "${filename}"
    git commit -m "${message}"
}

# --- Phase 1: Initial commit on main ---
commit_file "README.md" "# Seeded Project" "initial commit"

# --- Phase 2: Feature branch with 2 commits, merged to main ---
git checkout -b feature/auth
commit_file "auth.py" "def login(): pass" "feature: add login stub"
commit_file "auth.py" "def login(): return True\ndef logout(): pass" "feature: implement login, add logout stub"

git checkout main
git merge feature/auth --no-ff -m "merge feature/auth into main"

# --- Phase 3: Two parallel branches from main ---
git checkout -b agent/backend
commit_file "api.py" "from fastapi import FastAPI\napp = FastAPI()" "agent/backend: scaffold FastAPI app"
commit_file "api.py" "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/')\ndef root(): return {'status': 'ok'}" "agent/backend: add root endpoint"

# Start second branch from main (not from backend)
git checkout main
git checkout -b agent/frontend
commit_file "app.tsx" "export default function App() { return <h1>Hello</h1> }" "agent/frontend: scaffold Next.js app"
commit_file "app.tsx" "export default function App() { return <main><h1>Hello</h1><nav/></main> }" "agent/frontend: add navigation"

# --- Phase 4: Merge both parallel branches to main ---
git checkout main
git merge agent/backend --no-ff -m "merge agent/backend into main"
git merge agent/frontend --no-ff -m "merge agent/frontend into main"

echo ""
echo "Seed complete. Graph:"
git log --oneline --graph --all