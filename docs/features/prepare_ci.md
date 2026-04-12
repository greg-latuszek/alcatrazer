# CI/CD Pipeline

## Status: Complete (all 6 steps done, two TODOs remain)

### TODO

1. **Simplify `smoke.yml` after docker templating machinery.** Currently smoke.yml has a 
   `sed` hack to generate a project-root `docker-compose.yml` from the template. Once 
   `install_method` Step 2 implements proper docker template generation during init 
   (see `docs/features/install_method.md`), smoke.yml can drop the sed step — 
   `initialize_alcatraz.sh --non-interactive` will produce the working compose file directly.

2. **Test `release.yml` with a real version tag.** The release workflow (version check, 
   build, SHA256SUMS, GitHub Release) has never been triggered. It needs a `v*` tag push 
   to test. Defer until first release or a dry-run tag (e.g., `v0.0.1-rc1`).

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

### What this proves

- Code lints on a clean machine
- Tests pass on Python 3.11, 3.12, 3.13
- No dependency on local mise/uv/venv setup — CI installs everything from scratch

## Workflow 2: `smoke.yml` — Merge to Main

Triggers: push to `main` only (i.e., after a PR merge). 
Runs Docker smoke tests to verify container isolation — heavier, so only on main, not every branch.

### Jobs

**`smoke`**
- Runs in parallel with ci.yml (both trigger on the same push to main)
- Docker smoke tests: verify phantom UID, no credential leaks, no Docker socket, 
  no git remotes, zero alcatraz footprint
- Uses `initialize_alcatraz.sh --non-interactive` for environment setup
- Builds image with `docker build --build-arg` (bypasses compose for build)
- Generates project-root `docker-compose.yml` from template for `docker compose run`
- Mount point footprint test skipped in CI (host path contains repo name)

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
- Triggers on push to any branch + PRs to main

### Step 4: Create `.github/workflows/smoke.yml` ✅

- Docker smoke tests — triggers on push to `main` only (after merge)
- `initialize_alcatraz.sh --non-interactive` for environment setup
- `docker build --build-arg` for image build (bypasses compose)
- `sed` generates project-root `docker-compose.yml` for `docker compose run`
- Mount point footprint test skipped in CI (`CI=true`)

### Step 5: Create `.github/workflows/release.yml` ✅

- Triggers on version tag (`v*`)
- Verifies tag matches `__version__`
- Builds wheel, generates SHA256SUMS from `src/alcatrazer/`
- Creates GitHub Release with wheel, sdist, and SHA256SUMS attached
- PyPI publish step present but **commented out** (no PyPI access yet)

### Step 6: Enable branch protection on `main` ✅

- Development happens on feature branches, only merges to main allowed
- Require ci.yml status checks (lint, test matrix) to pass before merge
- Configured via GitHub repo settings → Branches → Branch protection rules

## Open Questions

1. **PyPI trusted publishers (after PyPI recovery):** GitHub Actions supports PyPI trusted publishers 
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

### Docker Compose template paths break in CI

`docker-compose.yml` lives in `src/alcatrazer/container/` with hardcoded `../../../` paths. 
This breaks both `.env` resolution and the `docker compose run` used by smoke tests. 
Even `--env-file .env` doesn't fully solve it — the test code (`test_smoke.py`) calls 
`docker compose -f <path> run` directly. CI workaround: `sed` the template into a 
working `docker-compose.yml` at the project root, fixing context and volume paths. 
The smoke test code looks for a project-root `docker-compose.yml` first, falls back to 
the template. Proper fix: docker template machinery in the init script 
(see `docs/features/install_method.md`).

### Docker build vs Docker Compose build in CI

`docker compose build` requires all compose-file variables (`USER_UID`, `WORKSPACE_DIR`) 
to be resolvable, which depends on `.env` parsing working correctly. For CI, 
`docker build --build-arg USER_UID=$(cat .alcatrazer/uid)` is simpler and more reliable — 
it bypasses compose entirely and reads the value directly from the init output. 
Use compose only where volume mounts and env_file injection are needed (i.e., `run`).

### Missing trailing newline in `.env.example`

`.env.example` did not end with a newline. When `initialize_alcatraz.sh` copied it to `.env` 
and appended `USER_UID=1002`, the result was `# MINIMAX_API_KEY=USER_UID=1002` — 
a mangled comment line, invisible to docker compose variable resolution. 
Fix: ensure `.env.example` ends with a newline, and defensively check for trailing newline 
in both `initialize_alcatraz.sh` and `init.py` `_set_env_var()` before appending.

### CI debugging principle: visibility first

CI runs on remote machines with no interactive access. When troubleshooting failures, 
**always add debug steps that dump state to logs before the failing step**, not after. 
Effective debug dumps include:

- **Generated files:** `cat docker-compose.yml`, `cat .env` — verify content, not just existence
- **Directory layout:** `find .alcatrazer -type f -o -type l | sort`, `ls -la $WORKSPACE_DIR`
- **Container internals:** `docker run --rm <image> bash -c 'id; pwd; ls -la /workspace; ls -la ~'`
- **Command output isolation:** run the failing command separately with `|| true` to capture 
  its error without blocking subsequent debug steps
- **Full script output:** run the same script the tests would run (e.g., `CONTAINER_SCRIPT`) 
  via a standalone `docker run` to see raw output before the test framework parses it

Without this visibility, CI debugging becomes guess-push-wait cycles. 
Each cycle costs 2-5 minutes. A few well-placed dumps save hours.

### Docker Compose project name leaks "alcatrazer" into volume paths

Docker Compose prefixes named volumes with the **project name** (defaults to directory name). 
When the repo is named `alcatrazer`, volumes become `alcatrazer_npm-cache` etc., visible 
inside the container via `/proc/self/mountinfo`. Fix: set `name: devenv` in 
`docker-compose.yml` to override the default project name.

### CI host path contains repo name — unavoidable in self-testing

GitHub Actions checks out to `/home/runner/work/{repo}/{repo}`. When the repo is `alcatrazer`, 
bind mount host paths in `/proc/self/mountinfo` contain `alcatrazer`. This is unavoidable 
when testing alcatrazer inside its own repo — it won't happen in end-user repos. 
Fix: split the mountinfo footprint check into a separate test 
(`test_no_alcatraz_in_container_mount_points`) and skip it in CI via `CI=true` env var.

## Current State

**All workflows operational:**
- `.github/workflows/ci.yml` — lint + test matrix on every push/PR
- `.github/workflows/smoke.yml` — Docker smoke tests on merge to main
- `.github/workflows/release.yml` — version check, build, SHA256SUMS, GitHub Release on tag
- Branch protection on `main` — requires PR with passing status checks