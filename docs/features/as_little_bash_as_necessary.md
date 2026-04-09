# As Little Bash as Necessary

## Status: Planning

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

### Phase 3: Evaluate remaining bash

**Step 3.1** — `Review seed_alcatraz.sh`
> Decide if converting to Python adds value. Likely keep as-is — it's 80 lines of idiomatic git scripting that rarely changes.

**Step 3.2** — `Review resolve_python.sh`
> Cannot be converted (bootstrap). Review for any cleanup opportunities within bash.

## Expected Outcome

| File | Before | After |
|------|--------|-------|
| initialize_alcatraz.sh | 292 lines | ~80 lines (thin wrapper) |
| resolve_python.sh | 202 lines | 202 lines (unchanged) |
| smoke_test.sh | 369 lines | 0 lines (deleted, replaced by test_smoke.py) |
| seed_alcatraz.sh | 80 lines | 80 lines (unchanged) |
| entrypoint.sh | 19 lines | 19 lines (unchanged) |
| **Total bash** | **962 lines** | **~381 lines** |
| **Reduction** | | **~60%** |

New Python code replaces ~581 lines of bash with maintainable, testable Python — integrated into the existing `src/alcatrazer/` package and `tests/` unittest suite.
