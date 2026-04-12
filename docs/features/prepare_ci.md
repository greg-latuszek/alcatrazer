# CI/CD Pipeline

## Status: Steps 1-5 done, Step 6 requires manual GitHub config

## Goal

Automated testing on every push/PR and automated releases on version tags. 
This is Phase 0 — before implementing `alcatrazer init` (install_method Step 2), 
we need confidence that the existing test suite passes on clean machines, not just the developer's laptop.

## Why Phase 0

- Test suite is already comprehensive (3600+ lines, 45+ test cases)
- Proves operability on machines other than the developer's
- Catches "works on my machine" issues early, before adding more code
- Easier to set up CI with the current codebase than after adding more complexity
- Release automation (PyPI publish, SHA256SUMS) is needed before first public release

## Platform: GitHub Actions

**Why:** The repo is on GitHub. Public repos get unlimited free CI minutes on Linux runners. 
Docker is pre-installed on Linux runners. GitHub Releases integration is native. 
No reason to look elsewhere.

**Limits (free tier, public repo):**
- Minutes: unlimited (Linux)
- Concurrent jobs: 20
- Job timeout: 6 hours
- Artifact storage: 10 GB

## Workflow 1: `ci.yml` — Every Push (any branch)

Triggers: push to any branch, pull requests targeting `main`. 
Lightweight (no Docker), cheap to run on every push — 
catches issues early while developing on feature branches.

### Jobs

**`lint`**
- Single Python version (3.12)
- `ruff check` — code quality
- `ruff format --check` — formatting verification (fail if unformatted)
- Fast, runs first

**`test`**
- Matrix: Python 3.11, 3.12, 3.13 (we claim `>=3.11` in pyproject.toml)
- `python -m unittest discover -s src/alcatrazer/tests -v` (excluding smoke tests)
- Runs unit tests and integration tests (daemon, promotion, snapshot, identity, etc.)
- No Docker required — smoke tests are excluded
- Depends on: `lint` (don't waste CI minutes testing code that doesn't lint)

**`build`**
- Single Python version (3.12)
- `uv build` — verify wheel and sdist build cleanly
- Upload `dist/` as GitHub Actions artifact (wheel + sdist)
- Downloadable from the workflow run page for manual life-testing: 
  `pip install alcatrazer-0.0.1-py3-none-any.whl`
- Artifact retention: 90 days (GitHub default)
- Depends on: `test`

### What this proves

- Code lints on a clean machine
- Tests pass on Python 3.11, 3.12, 3.13
- Package builds correctly
- No dependency on local mise/uv/venv setup — CI installs everything from scratch
- Built wheel is available for manual testing before PyPI access is recovered

## Workflow 2: `smoke.yml` — Merge to Main

Triggers: push to `main` only (i.e., after a PR merge). 
Runs Docker smoke tests to verify container isolation — heavier, so only on main, not every branch.

### Jobs

**`smoke`**
- Runs after ci.yml passes (ci.yml triggers on the same push to main)
- Docker smoke tests: verify phantom UID, no credential leaks, no Docker socket, 
  no git remotes, zero alcatraz footprint
- Requires: Docker (available on GitHub Actions Linux runners)
- Requires: `initialize_alcatraz.sh` to set up the environment, Docker image build
- May need: `alcatrazer.toml`, `.env`

### What this proves

- Container isolation works on a clean machine, not just the developer's laptop
- Docker-level security properties hold after the merged changes

## Workflow 3: `release.yml` — On Version Tag

Triggers: push of tags matching `v*` (e.g., `v0.1.0`, `v0.3.0`). 
The developer decides when to release — may accumulate multiple merges to main before tagging.

### Jobs

**`publish`**
- Verify tag version matches `__version__` in `src/alcatrazer/__init__.py`
- Build wheel: `uv build`
- Generate `SHA256SUMS`: hash every file in `src/alcatrazer/`
- ~~Publish to PyPI: `twine upload dist/*` (needs `PYPI_TOKEN` secret)~~ — **commented out until PyPI access is recovered**
- Create GitHub Release with:
  - `SHA256SUMS` file attached
  - Auto-generated release notes (or changelog)

### Secrets required

- `PYPI_TOKEN` — PyPI API token, stored in GitHub repo settings → Secrets. 
  **Not available yet** — PyPI account recovery in progress. 
  The publish step will be present in the workflow but commented out. 
  Everything else (version check, build, SHA256SUMS, GitHub Release) works without it.

## Version Management

**Approach: manual bump, CI verifies.**

The developer bumps `__version__` in `src/alcatrazer/__init__.py`, commits, tags:

```bash
# Developer workflow:
# 1. Edit src/alcatrazer/__init__.py → __version__ = "0.3.0"
# 2. Commit: "Release 0.3.0"
# 3. Tag:   git tag v0.3.0
# 4. Push:  git push && git push --tags
```

CI verifies the tag name matches the package version:

```bash
# In release.yml:
TAG_VERSION="${GITHUB_REF#refs/tags/v}"
PKG_VERSION=$(python -c "from alcatrazer import __version__; print(__version__)")
if [ "$TAG_VERSION" != "$PKG_VERSION" ]; then
    echo "ERROR: Tag v${TAG_VERSION} does not match __version__=${PKG_VERSION}"
    exit 1
fi
```

**Why manual, not automated:** Explicit is better than implicit. 
The developer decides when to release and what version number to use. 
CI automates the boring parts (build, publish, checksums) but doesn't make versioning decisions.

## SHA256SUMS Generation

Generated at release time from the tagged source:

```bash
cd src/alcatrazer
find . -type f | sort | xargs sha256sum > /tmp/SHA256SUMS
```

Uploaded as a GitHub Release asset. Users verify manually:

```bash
curl -sL https://github.com/greg-latuszek/alcatrazer/releases/download/v0.3.0/SHA256SUMS -o /tmp/SHA256SUMS
cd .alcatrazer/src/alcatrazer/
sha256sum -c /tmp/SHA256SUMS
```

See [design_principles.md](../design_principles.md) — "Checksum Verification via Independent Channel".

## Implementation Plan

### Step 1: Fix stale reference in pyproject.toml ✅

Removed `smoke_test.sh` force-include and all redundant force-include entries 
(hatch auto-discovers everything under `src/alcatrazer/`). Also moved `test_smoke.py` 
to `src/alcatrazer/integration_tests/` for clean separation from unit tests.

### Step 2: Make codebase pass lint and format checks ✅

Fixed 47 lint errors across 13 files. Suppressed UP036 globally — 
version guard blocks give friendly errors when scripts are run directly with old Python.

### Step 3: Create `.github/workflows/ci.yml` ✅

- Lint job (ruff check + ruff format --check)
- Test matrix (Python 3.11, 3.12, 3.13)
- Build job (uv build + upload artifact, 90-day retention)
- Triggers on push to any branch + PRs to main

### Step 4: Create `.github/workflows/smoke.yml` ✅

- Docker smoke tests — triggers on push to `main` only (after merge)
- Non-interactive bootstrap: UID, identity, workspace, snapshot, Docker image build
- Draft — may need iteration for CI-specific edge cases

### Step 5: Create `.github/workflows/release.yml` ✅

- Triggers on version tag (`v*`)
- Verifies tag matches `__version__`
- Builds wheel, generates SHA256SUMS from `src/alcatrazer/`
- Creates GitHub Release with wheel, sdist, and SHA256SUMS attached
- PyPI publish step present but **commented out** (no PyPI access yet)

### Step 6: Enable branch protection on `main`

- Development happens on feature branches, only merges to main allowed
- Require ci.yml to pass before merge
- Configure via GitHub repo settings → Branches → Branch protection rules

## Open Questions (deferred — not blocking Steps 1-3)

1. **Smoke tests in CI (Step 4):** The smoke tests require a fully initialized environment: 
`.alcatrazer/` state (uid, agent-identity), built Docker image, `docker compose`, 
`alcatrazer.toml` + `.env` at project root. That's essentially the full `initialize_alcatraz.sh` 
flow plus `docker compose build` inside the CI runner. Solve when we get to Step 4.

2. **PyPI trusted publishers (after PyPI recovery):** GitHub Actions supports PyPI trusted publishers 
(OIDC-based, no API token needed). More secure than a stored secret. 
Evaluate when PyPI access is recovered.

## Lessons Learned (CI Debugging)

### Git default branch on CI runners

GitHub Actions `ubuntu-latest` (Ubuntu 24.04) ships with git that defaults to `master` 
as the initial branch name. The test suite's `seed_alcatraz.sh` assumes `main`. 
Fix: add `git config --global init.defaultBranch main` to the CI git configuration step. 
This is needed in **every workflow that runs `git init`** (ci.yml for tests, smoke.yml for 
workspace init). Not needed in release.yml (no `git init`).

### Node.js 20 deprecation in GitHub Actions

`actions/checkout@v4`, `actions/setup-python@v5`, and `actions/upload-artifact@v4` run 
on Node.js 20, which GitHub is deprecating (forced to Node.js 24 on 2026-06-02, 
removed on 2026-09-16). Each job emits a warning annotation. Fix: bump to 
`checkout@v6`, `setup-python@v6`, `upload-artifact@v7`.

### Docker Compose `.env` resolution with `-f`

When running `docker compose -f src/alcatrazer/container/docker-compose.yml build`, 
Compose resolves `${VAR}` substitution in the compose file from `.env` in the 
**project directory** (the directory containing the compose file — `src/alcatrazer/container/`), 
NOT the current working directory. Since our `.env` lives at the project root, 
Compose can't find `USER_UID` and `WORKSPACE_DIR`. Fix: always pass 
`--env-file .env` explicitly. Note: the `env_file:` directive inside `docker-compose.yml` 
only injects variables into the **container runtime**, not into compose file interpolation.

### Build job removed from ci.yml

Originally ci.yml had a `build` job (uv build + upload-artifact). Removed because 
build verification and artifact upload only matter at release time — ci.yml should stay 
lightweight (lint + test). The build job lives in release.yml where it creates a GitHub 
Release with wheel, sdist, and SHA256SUMS attached.

### Branch protection blocks direct push to main

Step 6 (branch protection) was already configured. Direct pushes to `main` are rejected — 
changes must go through a PR with passing status checks. If a required status check is 
removed from the workflow (e.g. the `build` job), it must also be removed from the 
branch protection required checks list, otherwise PRs will hang on 
"Waiting for status to be reported" forever.

### Non-interactive init for CI

`init.py` has an interactive `input()` prompt for workspace directory selection that blocks 
in CI (no TTY). Fix: added `--non-interactive` flag that auto-picks the first choice. 
`initialize_alcatraz.sh` already passes through all CLI args, so 
`./src/alcatrazer/scripts/initialize_alcatraz.sh --non-interactive` works. This replaced 
a 30+ line hand-crafted bootstrap in smoke.yml with a single line.

## Current State

**What exists:**
- `mise.toml` — local dev tasks (test, test-fast, lint, format, build)
- `pyproject.toml` — package config, ruff config, test paths
- Test suite — comprehensive, already split into fast (no Docker) and smoke (Docker)

**What's missing:**
- `.github/workflows/` — no CI configuration yet
- No GitHub Releases automation
- No SHA256SUMS generation
- `pyproject.toml` has a stale `smoke_test.sh` reference