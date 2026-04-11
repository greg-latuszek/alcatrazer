# CI/CD Pipeline

## Status: Planning

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

## Workflow 1: `ci.yml` — Every Push and PR

Triggers: push to any branch, pull requests targeting `main`. 
Lightweight (no Docker), so cheap to run on every push — 
catches issues early while developing on feature branches, not just at merge time.

### Jobs

**`lint`**
- Single Python version (3.12)
- `ruff check` — code quality
- `ruff format --check` — formatting verification (fail if unformatted)
- Fast, runs first

**`test`**
- Matrix: Python 3.11, 3.12, 3.13 (we claim `>=3.11` in pyproject.toml)
- `python -m unittest discover -s src/alcatrazer/tests -v -k 'not smoke'`
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

## Workflow 2: `release.yml` — On Version Tag

Triggers: push of tags matching `v*` (e.g., `v0.1.0`, `v0.3.0`).

### Jobs

**`verify`**
- Same as ci.yml: lint + test matrix + build
- Must pass before publishing anything

**`smoke`** (optional, separate job)
- Docker smoke tests: `python -m unittest discover -s src/alcatrazer/tests -v -k 'smoke'`
- Requires: Docker (available on GitHub Actions Linux runners)
- Requires: `initialize_alcatraz.sh` to set up the environment first
- May need: `alcatrazer.toml`, `.env`, Docker image build
- This is the heaviest job — runs only on release, not every PR
- Depends on: `verify`

**`publish`**
- Verify tag version matches `__version__` in `src/alcatrazer/__init__.py`
- Build wheel: `uv build`
- Generate `SHA256SUMS`: hash every file in `src/alcatrazer/`
- ~~Publish to PyPI: `twine upload dist/*` (needs `PYPI_TOKEN` secret)~~ — **commented out until PyPI access is recovered**
- Create GitHub Release with:
  - `SHA256SUMS` file attached
  - Auto-generated release notes (or changelog)
- Depends on: `verify` (and `smoke` if enabled)

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

### Step 1: Fix stale reference in pyproject.toml

`pyproject.toml` line 48 includes `smoke_test.sh` in force-include — 
that file no longer exists (refactored to `test_smoke.py`). Remove it.

### Step 2: Make codebase pass lint and format checks

The codebase currently has 47 lint errors and 13 files need reformatting. 
CI can't enforce lint until the baseline is clean. Issues to address:

- **Formatting**: `ruff format` on all files (mechanical, safe)
- **E501**: Lines over 100 chars — mostly in tests and help strings
- **I001**: Import sorting — auto-fixable
- **SIM105/SIM108/SIM115**: Style suggestions — review case by case
- **UP036**: Version guard blocks (`if sys.version_info < (3, 11)`) — 
  ruff flags these because `requires-python = ">=3.11"` in pyproject.toml. 
  But these guards give a friendly error when someone runs a script directly 
  with an older Python, bypassing pip's version check. 
  **Decision:** suppress UP036 globally — the guards protect the deployed-from-source path

### Step 3: Create `.github/workflows/ci.yml`

- Lint job (ruff check + ruff format --check)
- Test matrix (Python 3.11, 3.12, 3.13)
- Build job (uv build + upload artifact)
- Verify it passes on a real push

### Step 4: Create `.github/workflows/release.yml`

- Version tag verification
- Build wheel
- SHA256SUMS generation
- GitHub Release creation with SHA256SUMS attached
- PyPI publish step present but **commented out** (no PyPI access yet)
- Test with a dry run on a test tag first

### Step 5: Enable branch protection on `main`

- Development happens on feature branches, only merges to main allowed
- Require CI to pass before merge
- Configure via GitHub repo settings → Branches → Branch protection rules

### Step 6: Smoke tests in CI (stretch goal)

- Docker-based smoke tests on release tags
- Requires: initializing the full environment in CI
- May need its own setup step or a dedicated test that bootstraps minimally
- Can defer to after first PyPI publish if too complex initially

## Open Questions

1. **Smoke tests in CI:** The smoke tests require a fully initialized environment 
(Docker image built, workspace set up, phantom UID). 
How much setup is needed in CI? Can we simplify the prerequisites, 
or do we need a dedicated "CI bootstrap" that runs `initialize_alcatraz.sh` in the runner?

2. **PyPI trusted publishers:** GitHub Actions supports PyPI trusted publishers 
(OIDC-based, no API token needed). More secure than a stored secret. 
Worth setting up when PyPI access is recovered?

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