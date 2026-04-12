# Alcatrazer Installation Method

## Status: On Hold — waiting for CI ([prepare_ci.md](prepare_ci.md))

## Goal

A simple one-liner that installs Alcatrazer into any existing git repository. 
The user runs one command, answers a few questions, and gets a working Alcatraz environment — no manual file copying, 
no cloning, no reading setup docs.

## Key Principle: Zero Pollution

Alcatrazer must not pollute the target repository. The only things that touch the repo proper are:

- `alcatrazer.toml` — version controlled, captures project decisions
- `.gitignore` entries for `.alcatrazer/`, `.<workspace>/`, and `.env`
- `.env.example` — template for API keys

Everything else — scripts, Docker files, daemon, promotion logic, tool state — lives inside `.alcatrazer/`. 
The agent workspace lives in a separate randomly named directory (e.g., `.devspace-7f3a/`) to prevent leaking "alcatrazer" 
via Docker's `/proc/self/mountinfo`.

```
target-repo/
├── .git/
├── .gitignore                <-- updated: adds .alcatrazer/, .<workspace>/, .env
├── .env                      <-- API keys + USER_UID + WORKSPACE_DIR
├── .env.example              <-- template for API keys
├── alcatrazer.toml           <-- created from template, user's promotion identity
├── .alcatrazer/              <-- gitignored, tool state (never mounted into Docker)
│   ├── python -> ...         <-- symlink to resolved Python 3.11+
│   ├── uid                   <-- phantom UID
│   ├── agent-identity        <-- randomly generated name + email
│   ├── workspace-dir         <-- name of the workspace directory
│   ├── promote-export-marks
│   ├── promote-import-marks
│   ├── src/alcatrazer/       <-- full package tree copied from PyPI wheel (Python modules, container/, scripts/, templates/, tests/)
│   └── ... (logs, PID, etc.)
└── .<workspace>/             <-- gitignored, randomly named (e.g., .devspace-7f3a/)
    ├── .git/                 <-- inner git (random agent identity, no remote)
    └── ... agent work ...
```

For installation via PyPI, all tool code is inside the `alcatrazer` package — Python modules, 
Docker templates (`container/`), bash scripts (`scripts/`), and config template (`templates/alcatrazer.toml`). 
The `alcatrazer init` command copies what's needed into the target repo.

## Installation Options

### Option A: `pipx run alcatrazer init`

**How it works:**
- Alcatrazer is published to PyPI as a package with a CLI entry point
- `pipx run` downloads the package into a temporary venv, runs it, discards it
- The CLI copies tool files into `.alcatrazer/`, writes config, runs interactive setup

**Requires:** `pipx` installed on the target machine.

**Pros:**
- Clean, Pythonic, idiomatic for Python CLI tools in 2026
- Version pinning: `pipx run alcatrazer==1.2.0 init`
- No residue after installation — pipx discards the temp venv
- Familiar pattern for Python developers

**Cons:**
- Requires pipx. Not everyone has it. It's increasingly common but not universal.
- pipx itself needs Python — but we already require Python 3.11+ anyway.

**Who has pipx:** Developers who actively manage Python CLI tools. Common in Python-heavy teams, 
less common for Node/Go/Rust developers who happen to use AI agents.

### Option A2: `uvx alcatrazer init`

Same as Option A but via `uv` instead of `pipx`.

**Requires:** `uv` installed on the target machine.

**Pros:**
- `uv` is fast (Rust-based), gaining adoption rapidly
- `uvx` is uv's equivalent of `pipx run` — ephemeral execution
- Same PyPI package, same CLI — just a different runner

**Cons:**
- Requires uv. Newer tool, less widespread than pipx (as of 2026).
- Same chicken-and-egg: need a Python tool runner to install a Python tool.

**Who has uv:** Early adopters, teams using modern Python tooling. Growing fast but not yet ubiquitous.

**Note:** Options A and A2 use the same PyPI package. If we publish to PyPI, 
both `pipx run` and `uvx` work automatically — no extra effort.

### Option C: `curl -fsSL https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh | bash`

**How it works:**
- A bash installer script hosted at a stable URL
- Three-stage bootstrap that converges to the same PyPI package as Options A/A2:
  1. **Stage 1 (bash):** Resolves Python 3.11+ using the same four-tier fallback as `resolve_python.sh` (detect system python3 → offer mise install → offer mise bootstrap → ask for manual path). Creates `.alcatrazer/python` symlink.
  2. **Stage 2 (bash → Python stdlib):** Creates a temporary venv: `.alcatrazer/python -m venv .alcatrazer/.venv`. The `venv` module is Python stdlib, and `ensurepip` (also stdlib) provides pip inside the venv.
  3. **Stage 3 (same as pipx/uvx):** `.alcatrazer/.venv/bin/pip install alcatrazer && .alcatrazer/.venv/bin/alcatrazer init`. This is the exact same PyPI package that pipx/uvx would run. After installation completes, `rm -rf .alcatrazer/.venv`.

**Key insight:** All three installation paths run the same `alcatrazer init` from the same PyPI package. 
The only difference is who provides the temporary Python environment:

```
pipx run alcatrazer init     →  pipx manages temp venv   →  alcatrazer init
uvx alcatrazer init          →  uv manages temp venv     →  alcatrazer init
curl | bash                  →  we manage temp venv      →  alcatrazer init
                                (resolve python,
                                 venv + pip install,
                                 run, delete venv)
```

One installer codebase. One test surface. Three entry points.

**Requires:** 
`bash` and `curl`. Both are truly universal on Linux/macOS. 
Python is NOT assumed — the script finds or installs it.

**Pros:**
- Maximum reach — the only hard assumptions are bash and curl
- Reuses our proven Python resolution logic (four-tier fallback)
- **Same PyPI package as pipx/uvx** — one codebase, not a separate installer
- No permanent Python tool residue — temp venv is deleted after install
- Single command, familiar pattern (mise, rustup, poetry all install this way)

**Cons:**
- Need to host the bash bootstrap script (GitHub Pages, a domain, or raw GitHub URL)
- No built-in version pinning (though the script can accept `--version` to pin the pip install)
- `curl | bash` pattern makes some security-conscious users nervous (mitigated by HTTPS)
- Slightly slower than pipx/uvx (creates+destroys a venv)

**Who can use it:** Everyone with bash and internet access. The true universal fallback.

### Option D: Hybrid (recommended)

All three entry points run the exact same `alcatrazer` PyPI package. 
The only difference is who provides the temporary Python environment:

```bash
# For pipx users:
pipx run alcatrazer init

# For uv users:
uvx alcatrazer init

# For everyone else (only assumes bash + curl):
curl -fsSL https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh | bash
```

**What we build and maintain:**
1. **One PyPI package** (`alcatrazer`) — contains the installer CLI and all tool files as package data. This is the single source of truth.
2. **One bash bootstrap script** — the `curl | bash` entry point. Thin: resolves Python, creates temp venv, `pip install alcatrazer`, runs it, deletes venv. ~50 lines of bash reusing `resolve_python.sh` logic.

**Pros:**
- Maximum reach — meet users where they are
- One installer codebase (the PyPI package), one test surface
- The bash script is small and stable — the real logic is in Python

**Cons:**
- Need PyPI account and a hosted URL for the bash script
- Three entry points in the docs (but they're one-liners, not separate codebases)

## Docker Template Machinery

The files in `src/alcatrazer/container/` (docker-compose.yml, Dockerfile, entrypoint.sh) are **templates**, 
not ready-to-use files. Currently `docker-compose.yml` has hardcoded `../../../` relative paths that assume 
it stays nested 3 levels deep inside the package. This causes problems:

- Docker Compose resolves `${VAR}` substitution from `.env` in the **project directory** 
  (directory containing the compose file), not the CWD. Since `.env` lives at the project root 
  but the compose file is in `src/alcatrazer/container/`, variables like `USER_UID` aren't found.
- The `--env-file .env` workaround is fragile and easy to forget.
- Volume paths like `../../../${WORKSPACE_DIR}` are brittle.

**Solution:** During `alcatrazer init` (Step 2), generate `docker-compose.yml` at the project root 
from the template, rewriting paths:
- `context: ../../..` → `context: .`
- `../../../.env` → `./.env`  
- `../../../${WORKSPACE_DIR}` → `./${WORKSPACE_DIR}`

The Dockerfile stays in `src/alcatrazer/container/` — its `COPY` paths are relative to the build context 
(project root), not to the Dockerfile location, so they work without changes.

This is the same approach as `alcatrazer.toml` — template lives in the package, 
generated file lives at the project root. The installer for end user repos will do the same: 
take templates from the installed package, place them at the correct location.

**Current CI workaround:** `smoke.yml` uses a `sed` hack to copy and fix paths at build time. 
This will be replaced by the proper init-time generation.

## Open Questions

1. **Tool files as package data:** The PyPI package bundles Dockerfile, scripts, etc. as package data. The `alcatrazer init` command extracts them into `.alcatrazer/`. This means the PyPI package IS the release — no separate tarball or GitHub Releases needed. Is this sufficient or do we also want standalone tarballs?

2. **Versioning and updates:** How does a user update Alcatrazer in an existing project? Options:
   a. `pipx run alcatrazer update` (or `curl | bash` again) — re-extracts tool files, preserves config
   b. Re-run `init` with `--upgrade` flag — same effect, explicit intent
   c. Manual: user downloads new version and replaces `.alcatrazer/src/`

3. **Domain / URL for curl installer:**
   **Decision:** Start with raw GitHub URL (free, zero setup):
   `https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh`

   Future options for a nicer URL:
   - GitHub Pages on the repo → `https://greg-latuszek.github.io/alcatrazer/install.sh`
   - Custom domain on GitHub Pages → `https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh` (**costs money**)
   
   Note: `https://github.com/<user>/<repo>/install` is NOT a valid GitHub URL pattern. 
   GitHub only serves raw files via `raw.githubusercontent.com`, release assets, or GitHub Pages.

4. **Should the installer also run initialization?** 
The init command could offer to run the full initialization (UID, workspace, safe.directory) as the final step, 
or leave it as a separate command. Running it immediately gives a better "one command to set up" experience.

5. **The `.alcatrazer/python` symlink:** 
The curl|bash path creates it during Python resolution. 
But pipx/uvx users also need it for the daemon. 
Should `alcatrazer init` create the symlink too (detecting the Python that's running it via `sys.executable`)?

## Current State

**Dev tooling set up:**
- `mise.toml` — manages python 3.12 + uv, defines tasks (test, build, format, lint, docker-build, etc.)
- `pyproject.toml` — package config with hatchling build, ruff linting, dev dependencies
- `src/alcatrazer/` — PyPI package skeleton with placeholder CLI (`init`, `update`, `version` commands)
- `LICENSE` — MIT
- Package builds successfully (`mise run build` → `dist/alcatrazer-0.0.1-py3-none-any.whl`)
- Version single-sourced from `src/alcatrazer/__init__.py` (hatch reads it dynamically)

**PyPI account:** Recovery in progress (may take a few days). Name `alcatrazer` is available.

## Implementation Plan

Build order for the real installer (before first PyPI publish):

### Step 1: Bundle tool files as package data ✅
All tool files are now inside `src/alcatrazer/` — Python modules, Docker templates (`container/`), 
bash scripts (`scripts/`), tests (`tests/`), and config template (`templates/alcatrazer.toml`). 
Hatch auto-includes everything under `src/alcatrazer/` in the wheel.

### Step 2: Implement `alcatrazer init`
Interactive CLI that:

1. **Verify git repo** — confirm we're inside a git repo, at the repo root
2. **Detect user identity from git config**
— read `user.name` and `user.email` from local git config (repo-specific) first, fall back to global. Present to user:
   ```
   Detected git identity: Grzegorz Latuszek <latuszek.grzegorz@gmail.com>
   Use this for promoted commits? [Y/n]
   ```
   If user declines, prompt for name and email.
3. **Write `alcatrazer.toml`** 
— copy from `src/alcatrazer/templates/alcatrazer.toml`, fill in the confirmed name/email in the `[promotion]` section. 
Optionally ask about tool versions and daemon settings (or accept defaults).
4. **Write `.env.example`** — template for API keys
5. **Extract tool files** 
— copy the full `src/alcatrazer/` tree from the PyPI package into `.alcatrazer/src/alcatrazer/` 
(Python modules, container/, scripts/, templates/, tests/ — same layout as the dev repo)
6. **Update `.gitignore`** — add `.alcatrazer/` and `.env` entries
7. **Create `.alcatrazer/python` symlink** — from `sys.executable` (the Python that's running `alcatrazer init`)
8. **Run initialization** 
— optionally run the full init flow: UID detection, workspace directory selection (3 random choices), 
random agent identity, git init + snapshot, safe.directory

### Step 3: Implement `alcatrazer update`
Re-extracts `src/alcatrazer/` from package data into `.alcatrazer/src/alcatrazer/`, preserving `alcatrazer.toml` 
and all state (workspace, marks, UID, logs, agent identity, workspace-dir selection).

### Step 4: Write `install.sh` (curl|bash bootstrap)
Thin bash script: resolve Python 3.11+ (four-tier), create temp venv, `pip install alcatrazer`, 
run `alcatrazer init`, delete temp venv. ~50 lines.

### Step 5: Tests (development)
- Unit tests for init (mock filesystem, verify files created)
- Integration test: run `alcatrazer init` in a temp git repo, verify layout
- Test `install.sh` with faked PATH (same approach as resolve_python tests)

### Step 6: Post-installation verification tests ✅
Tests are bundled inside the package in two directories:
- `src/alcatrazer/tests/` — unit and integration tests (no Docker required)
- `src/alcatrazer/integration_tests/` — Docker smoke tests (verify container isolation)

End users run `alcatrazer test` (unit tests) or `alcatrazer test --smoke` (includes Docker tests).
See "Trust and Verification" section below.

### Step 7: Publish to PyPI
`uvx twine upload dist/*` — first real release (0.1.0).

### Step 8: Publish `SHA256SUMS` on GitHub Releases
Generate checksums of all source files in `src/alcatrazer/` at the tagged commit, 
upload as a GitHub Releases asset alongside the release notes. 
This is the independent trust anchor — see "Trust and Verification" section below.

```bash
# At release time, from the tagged commit:
cd src/alcatrazer && find . -type f | sort | xargs sha256sum > SHA256SUMS
# Then attach SHA256SUMS to the GitHub Release via gh CLI or web UI
```

---

## Trust and Verification

### The trust problem

Alcatrazer is a security tool. It asks users to run AI agents inside Docker containers with the promise 
that their host is protected. This creates a fundamental trust question:

> "Isn't Alcatrazer a wise social engineer that claims to protect my laptop but instead is a thief itself?"

If the tool claims to isolate agents from host secrets, the user must be able to verify that claim — not just by reading marketing copy, 
but by reading code and running tests.

### Trust layers

**Layer 1: Open source on GitHub.** 
The entire codebase is public. Anyone can read the Dockerfile, the entrypoint, the docker-compose volumes, the promotion scripts. 
This is the foundation — but it's not sufficient, because the user has no guarantee that what's installed on their machine 
matches what's on GitHub.

**Layer 2: Installed source is readable.** 
The installation extracts tool files into `.alcatrazer/` — not compiled bytecode, not obfuscated, 
not downloaded from a different source. The user can read every script that runs on their machine:

```bash
# "What exactly is this tool doing on my host?"
cat .alcatrazer/src/alcatrazer/...<source-file>.py
cat .alcatrazer/src/alcatrazer/container/Dockerfile
cat .alcatrazer/src/alcatrazer/container/docker-compose.yml
```

**Layer 3: Post-installation verification tests.** 
The test suite is bundled alongside the tool files:
- `.alcatrazer/src/alcatrazer/tests/` — unit and integration tests
- `.alcatrazer/src/alcatrazer/integration_tests/` — Docker smoke tests (container isolation)

The user can run the same tests that developers run to verify the security model:

```bash
# "Prove to me this tool does what it claims"
# Unit tests (no Docker required):
.alcatrazer/python -m unittest discover -s .alcatrazer/src/alcatrazer/tests -v
# Docker isolation tests (requires running Docker):
.alcatrazer/python -m unittest discover -s .alcatrazer/src/alcatrazer/integration_tests -v
```

These tests verify:
- Container runs as phantom UID (no matching host user)
- No host credentials leak into the container (SSH keys, GPG keys, git config)
- Only explicitly passed environment variables are visible
- Docker socket is not mounted
- No git remotes configured inside the container
- Promotion rewrites identity correctly (no alcatraz identity leaks to outer repo)
- Files inside workspace are owned by phantom UID

**Layer 4: Checksum verification via independent channel.** 
Each release publishes a `SHA256SUMS` file as a GitHub Releases asset — one hash per source file. 
This is an independent channel from PyPI: a compromised PyPI account can push a modified package, 
but the attacker would also need to compromise GitHub to fake the checksums.

The real proof is manual — no alcatrazer code involved:

```bash
# "Is what I installed the same as what's on GitHub?"
# 1. Download checksums from GitHub (independent of PyPI)
curl -sL https://github.com/greg-latuszek/alcatrazer/releases/download/v0.3.0/SHA256SUMS -o /tmp/SHA256SUMS

# 2. Verify with standard Unix tools
cd .alcatrazer/src/alcatrazer/
sha256sum -c /tmp/SHA256SUMS
```

`alcatrazer verify` automates this as a convenience — but since the command is part of the package 
it claims to verify, a compromised version could fake the result. The command is transparent about this: 
it shows its own source code, explains every step, and prints the equivalent manual commands 
so the user can copy-paste and replicate independently.

### What the user gets

The `alcatrazer` PyPI package is self-contained. After `pip install alcatrazer`, the user has:
- Python modules (promote, snapshot, daemon, inspect, identity) — readable source
- Docker templates (Dockerfile, docker-compose.yml, entrypoint.sh) — readable
- Bash bootstrap scripts (initialize_alcatraz.sh, resolve_python.sh) — readable
- Config template (alcatrazer.toml with defaults) — readable
- Bundled test suite — runnable via `alcatrazer test`

The user can inspect any of these before or after installation. `alcatrazer test` runs the same tests developers run.

### Target audience

The target user is a developer. They can read Python and bash. They understand Docker volumes and git. 
They may not read the source before first use — but knowing they *can* is itself a trust signal. 
And when a security-conscious team lead asks "how do we know this is safe?", 
the answer is: "read the source, run the tests, diff against GitHub."

### Design principle

This is an extension of Iceberg Principle 1 (fight for security): 
**the tool's security claims must be verifiable by the user, not just asserted by the author.** 
Open source is necessary but not sufficient. The installed tool must carry its own proof.
