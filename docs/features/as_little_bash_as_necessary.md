# As Little Bash as Necessary

## Status: Complete

## Problem

Over development time, `initialize_alcatraz.sh` (292 lines) and `smoke_test.sh` (369 lines) have grown toward an unmaintainable state. The majority of the repository is Python with easier-to-maintain code and tests. Moreover, one of the very first steps in `initialize_alcatraz.sh` is resolving Python 3.11+ (Step 3) — from that point on, we can switch fully to Python.

Bash is harder to test, harder to refactor, and harder to maintain:
- No type hints, no IDE support, no debugger
- String handling is fragile (quoting, word splitting, globbing)
- Error handling is manual (`set -euo pipefail` + explicit checks)
- Testing requires subprocess invocation — slow, opaque failures
- Complex logic (workspace selection, reset flow) is spread across inline bash with embedded Python calls

## Principle

**Bash is the bootstrap.** It exists to get Python running. Once Python is available, all subsequent logic should be Python. The only bash that remains is:
- Code that runs before Python exists (`resolve_python.sh`)
- Container entrypoint (root privilege dropping via `gosu`)
- The thin bash wrapper in `initialize_alcatraz.sh` that calls Python after resolution

## Analysis: Current Bash Code

**Total: 962 lines across 5 files.**

### src/resolve_python.sh (202 lines) — MUST STAY BASH

The Python bootstrap. Detects or installs Python 3.11+ via four-tier fallback (system python3 → mise → install mise + python → manual path). This is the circular dependency: we need Python to run Python, so the resolver must be bash.

**Verdict:** 100% stays bash. No refactoring possible.

### container/entrypoint.sh (19 lines) — MUST STAY BASH

Runs as root inside the container to fix workspace ownership (`chown`), ensure mise tools are available, then drops to non-root agent user via `gosu exec`. This is a Docker entrypoint — must be a shell script by convention.

**Verdict:** 100% stays bash. Minimal code, no maintenance burden.

### tests/seed_alcatraz.sh (80 lines) — KEEP AS BASH (idiomatic)

Creates realistic git branch/merge history for testing. Fundamentally a git scripting task — `git init`, `git add`, `git commit`, `git branch`, `git merge`. Shell is the natural language for this.

**Verdict:** Keep as bash. Idiomatic, rarely changes, low maintenance burden.

### src/initialize_alcatraz.sh (292 lines) — REFACTOR TO THIN WRAPPER

This is the main target. Current step flow:

| Step | Lines | Before/After Python | Can be Python? |
|------|-------|-------------------|----------------|
| Guard: repo root validation | 17 | Before | No — git command, pre-Python |
| --reset flag handling | 66 | Before (partially) | Mostly yes — unpromoted check already calls Python, docker cleanup could be orchestrated from Python |
| Step 1: .env creation | 10 | Before | Trivial — could be either |
| Step 2: UID/GID detection | 36 | Before | No — `getent passwd` probing is shell-native |
| **Step 3: Resolve Python** | **5** | **THE PIVOT POINT** | **N/A — calls resolve_python.sh** |
| Step 3.5: Workspace dir selection | 43 | After | **Yes** — already calls Python, inline bash is just glue |
| Step 4: Git init + identity | 39 | After | **Yes** — git commands can run from Python subprocess |
| Step 5: Snapshot | 8 | After | **Already Python** (snapshot.py) |
| Step 6: safe.directory | 14 | After | **Yes** — single git config command |
| Summary | 20 | After | **Yes** — just print statements |

**~124 lines (Steps 3.5–Summary) run after Python is available and could be a single Python script call.**

The refactored flow would be:

```bash
# initialize_alcatraz.sh — thin bash wrapper (~80 lines)

# Guard: repo root
# Step 1: .env
# Step 2: UID/GID (getent — must be bash)
# Step 3: resolve_python.sh

# === PIVOT: Python available ===

# Everything else is one Python call:
.alcatrazer/python -m alcatrazer.init "$PROJECT_DIR" "$ALCATRAZ_DIR"
```

A new `src/alcatrazer/init.py` would handle:
- Workspace directory selection (already partly in identity.py)
- Git init + random identity configuration
- Snapshot (already in snapshot.py)
- safe.directory
- Summary output
- Reset flow (after Python check)

### tests/smoke_test.sh (369 lines) — CONVERT TO PYTHON

The smoke test runs a single `docker compose run` command, captures output, then parses 14+ test sections. The parsing logic (section extraction, assertion, pass/fail counting) is where bash gets painful.

**Refactoring approach:**
- Keep the Docker invocation (must be shell/subprocess)
- Move ALL parsing and assertions into Python unittest
- The test becomes a Python class that:
  1. Runs `docker compose run` via subprocess
  2. Parses the delimiter-separated output
  3. Runs standard unittest assertions on each section

This gives us:
- Proper test reporting (`unittest` output, IDE integration)
- Easy to add/modify test sections
- Assertion messages that are actually readable
- Runs alongside the rest of the test suite

## Refactoring Plan

### Phase 0: Consolidate Python code into `src/alcatrazer/` package

Prerequisite for everything else. Move all standalone Python modules into the `alcatrazer` package so we have a single importable, pip-installable package.

**Current layout:**
```
src/
├── promote.py              <-- standalone
├── snapshot.py             <-- standalone
├── watch_alcatraz.py       <-- standalone
├── inspect_promotion.py    <-- standalone
└── alcatrazer/
    ├── __init__.py
    └── identity.py         <-- already in the package
```

**Target layout:**
```
src/alcatrazer/
├── __init__.py
├── identity.py             <-- already here
├── promote.py              <-- from src/promote.py
├── snapshot.py             <-- from src/snapshot.py
├── daemon.py               <-- from src/watch_alcatraz.py (renamed)
├── inspect.py              <-- from src/inspect_promotion.py (renamed)
├── init.py                 <-- new (Phase 1)
├── container/              <-- Docker templates (from container/)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
└── scripts/                <-- bash bootstrap templates (from src/)
    ├── initialize_alcatraz.sh
    └── resolve_python.sh
```

**Why:**
- Single importable package: `from alcatrazer import promote, snapshot, identity`
- Clean PyPI distribution: `pip install alcatrazer` gives everything — Python code, Docker templates, bash bootstrap
- CLI entry points via `pyproject.toml`: `python -m alcatrazer.promote`, `python -m alcatrazer.daemon`, etc.
- `alcatrazer init` copies templates (container/, scripts/) into the user's repo — no separate download
- Tests drop `sys.path.insert(0, ...)` hacks, use `from alcatrazer import ...`
- Module names improve: `watch_alcatraz.py` → `daemon.py`, `inspect_promotion.py` → `inspect.py`

**Nothing stays outside the package** — everything is inside `src/alcatrazer/`. The package is fully self-contained.

**Step 0.1** — `Move promote.py into alcatrazer package`
> `git mv src/promote.py src/alcatrazer/promote.py`. Update all imports in tests, daemon, init script. Existing `__main__` block keeps working via `python -m alcatrazer.promote`. Update `initialize_alcatraz.sh` and `mise.toml` references.

**Step 0.2** — `Move snapshot.py into alcatrazer package`
> `git mv src/snapshot.py src/alcatrazer/snapshot.py`. Update imports in tests and init script. The snapshot CLI (`python src/snapshot.py`) becomes `python -m alcatrazer.snapshot`.

**Step 0.3** — `Move watch_alcatraz.py → alcatrazer/daemon.py`
> `git mv src/watch_alcatraz.py src/alcatrazer/daemon.py`. Rename reflects purpose (it's the promotion daemon, not a watcher). Update imports in tests, mise.toml, README. CLI becomes `python -m alcatrazer.daemon`.

**Step 0.4** — `Move inspect_promotion.py → alcatrazer/inspect.py`
> `git mv src/inspect_promotion.py src/alcatrazer/inspect.py`. Update imports in tests, mise.toml, README. CLI becomes `python -m alcatrazer.inspect`.

**Step 0.5** — `Remove sys.path hacks from all tests`
> All test files currently do `sys.path.insert(0, str(Path(...) / "src"))`. With the package under `src/`, tests can use `from alcatrazer import promote` directly (with `PYTHONPATH=src` or a proper `pyproject.toml` config). Clean up all test imports.

**Step 0.6** — `Move container/ and bash scripts into package as templates`
> `git mv container/ src/alcatrazer/container/` and `git mv src/initialize_alcatraz.sh src/alcatrazer/scripts/initialize_alcatraz.sh`, same for `resolve_python.sh`. These are not importable Python — they're templates that `alcatrazer init` copies into the user's repo. Using `importlib.resources` (stdlib, Python 3.9+) to locate them at runtime.
>
> Target layout:
> ```
> src/alcatrazer/
> ├── __init__.py
> ├── identity.py
> ├── promote.py
> ├── snapshot.py
> ├── daemon.py
> ├── inspect.py
> ├── init.py                 <-- new (Phase 1)
> ├── container/              <-- Docker templates
> │   ├── Dockerfile
> │   ├── docker-compose.yml
> │   └── entrypoint.sh
> ├── scripts/                <-- bash bootstrap templates
> │   ├── initialize_alcatraz.sh
> │   └── resolve_python.sh
> └── tests/                  <-- bundled test suite (trust & verification)
>     ├── __init__.py
>     ├── test_promote.py
>     ├── test_snapshot.py
>     ├── test_identity.py
>     ├── test_initialize.py
>     ├── test_watch.py
>     ├── test_python_resolution.py
>     ├── test_smoke.py        <-- Phase 2: converted from smoke_test.sh
>     └── seed_alcatraz.sh     <-- test fixture
> ```
>
> **Why inside the package:** `pip install alcatrazer` becomes self-contained. The `alcatrazer init` command copies templates into the user's repo — no separate download step. Same pattern as `cookiecutter`, `django-admin startproject`, etc. Non-Python files are fully supported via `package_data` in `pyproject.toml`.

**Step 0.7** — `Move tests/ into alcatrazer package`
> `git mv tests/ src/alcatrazer/tests/`, add `__init__.py`. Tests are bundled with the installation so end users can:
> 1. Run all tests on installed code: `alcatrazer test` — proves installed sources match what was tested on GitHub
> 2. Read the tests — target audience is developers who want to understand and verify the security model
>
> Same pattern as `numpy.test()`, `scipy.test()`, Django's `django.test`. Established practice for trust-critical packages.
>
> Update all test discovery commands (mise.toml, README, CI).

**Step 0.8** — `Add alcatrazer test command`
> Entry point in `pyproject.toml` that runs the bundled test suite. Implementation: `alcatrazer test` invokes `unittest.discover` on the `alcatrazer.tests` package. Optionally accepts `--smoke` flag to also run Docker-based smoke tests (requires Docker).
>
> This is a trust feature — the user installs alcatrazer and immediately verifies it works:
> ```bash
> pip install alcatrazer
> alcatrazer test          # run unit tests — verify installed code
> alcatrazer test --smoke  # also run Docker smoke tests
> ```

**Step 0.9** — `Add pyproject.toml package configuration`
> Configure `pyproject.toml` with package metadata, entry points (`alcatrazer` CLI with `init`, `test`, `promote`, `daemon`, `inspect` subcommands), `package_data` (to include container/, scripts/, and tests/ content), and the `src` layout. This enables `pip install -e .` for development and future PyPI publishing.

### Phase 1: Extract post-Python init logic to Python

**Step 1.1** — `Create src/alcatrazer/init.py`
> A Python module that handles everything after Python resolution: workspace directory selection, git init + identity, snapshot, safe.directory, summary. Called by `initialize_alcatraz.sh` as a single Python invocation after Step 3.

**Step 1.2** — `Slim down initialize_alcatraz.sh to thin wrapper`
> Remove all post-Python logic from the bash script. It becomes: guard → .env → UID → resolve_python → `python -m alcatrazer.init`. Target: ~80 lines of bash.

**Step 1.3** — `Move reset logic to Python`
> The `--reset` flow (unpromoted check, docker cleanup, re-init) moves to `alcatrazer.init` or a separate `alcatrazer.reset` module. The bash wrapper just detects `--reset` and passes it to Python (after ensuring Python is available — for reset, Python should exist from previous init; if not, skip the unpromoted check as we already do).

### Phase 2: Convert smoke test to Python

**Step 2.1** — `Create tests/test_smoke.py`
> A Python unittest class that runs `docker compose run` via subprocess, captures the delimiter-separated output, and runs assertions on each section. Same test coverage as the current bash smoke test.

**Step 2.2** — `Remove tests/smoke_test.sh`
> Once the Python smoke test passes, remove the bash version.

### Phase 3: Evaluate remaining bash ✅

**Step 3.1** — `Review seed_alcatraz.sh` ✅
> **Keep as-is.** 80 lines of idiomatic git scripting (checkout, merge, commit). Converting to Python would wrap the same commands in `subprocess.run()` — more verbose, no benefit. Rarely changes.

**Step 3.2** — `Review resolve_python.sh` ✅
> **Keep as-is.** 202 lines, must stay bash (bootstrap — runs before Python exists). Fixed one outdated path reference in error message.

## Outcome

| File | Before | After |
|------|--------|-------|
| initialize_alcatraz.sh | 294 lines | 102 lines (thin wrapper) |
| resolve_python.sh | 202 lines | 202 lines (unchanged, bootstrap) |
| smoke_test.sh | 370 lines | 0 lines (deleted → test_smoke.py) |
| seed_alcatraz.sh | 80 lines | 80 lines (unchanged, idiomatic) |
| entrypoint.sh | 19 lines | 19 lines (unchanged, Docker) |
| **Total bash** | **965 lines** | **403 lines** |
| **Reduction** | | **58%** |

All Python code consolidated into `src/alcatrazer/` package. Tests bundled. `alcatrazer test` command available. 195 tests (160 unit + 35 smoke, smoke skipped without Docker).
