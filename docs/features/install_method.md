# Alcatrazer Installation Method

## Status: Planning

## Goal

A simple one-liner that installs Alcatrazer into any existing git repository. The user runs one command, answers a few questions, and gets a working Alcatraz environment — no manual file copying, no cloning, no reading setup docs.

## Key Principle: Zero Pollution

Alcatrazer must not pollute the target repository. All tool files live inside `.alcatraz/` (already gitignored). The only things that touch the repo proper are:

- `alcatrazer.toml` — version controlled, captures project decisions
- `.gitignore` entry for `.alcatraz/`
- `.env.example` — template for API keys

Everything else — scripts, Docker files, daemon, promotion logic — lives inside `.alcatraz/`:

```
target-repo/
├── .git/
├── .gitignore              <-- updated: adds .alcatraz/ and .env
├── .env.example            <-- created
├── alcatrazer.toml         <-- created (version controlled)
└── .alcatraz/              <-- gitignored, everything else lives here
    ├── container/
    │   ├── Dockerfile
    │   ├── docker-compose.yml
    │   └── entrypoint.sh
    ├── src/
    │   ├── initialize_alcatraz.sh
    │   ├── resolve_python.sh
    │   ├── promote.py
    │   ├── watch_alcatraz.py
    │   └── inspect_promotion.py
    ├── workspace/           <-- mounted into Docker
    │   └── .git/
    ├── python -> /usr/bin/python3
    ├── uid
    ├── uid.env
    └── ... (marks, logs, PID, etc.)
```

This is a layout change from development (where `src/` and `container/` are at repo root). In a deployed installation, they move inside `.alcatraz/`. The development repo is Alcatrazer's own source code; an installed repo is someone else's project using Alcatrazer as a tool.

## Installation Options

### Option A: `pipx run alcatrazer init`

**How it works:**
- Alcatrazer is published to PyPI as a package with a CLI entry point
- `pipx run` downloads the package into a temporary venv, runs it, discards it
- The CLI copies tool files into `.alcatraz/`, writes config, runs interactive setup

**Requires:** `pipx` installed on the target machine.

**Pros:**
- Clean, Pythonic, idiomatic for Python CLI tools in 2026
- Version pinning: `pipx run alcatrazer==1.2.0 init`
- No residue after installation — pipx discards the temp venv
- Familiar pattern for Python developers

**Cons:**
- Requires pipx. Not everyone has it. It's increasingly common but not universal.
- pipx itself needs Python — but we already require Python 3.11+ anyway.

**Who has pipx:** Developers who actively manage Python CLI tools. Common in Python-heavy teams, less common for Node/Go/Rust developers who happen to use AI agents.

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

**Note:** Options A and A2 use the same PyPI package. If we publish to PyPI, both `pipx run` and `uvx` work automatically — no extra effort.

### Option C: `curl -fsSL https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh | bash`

**How it works:**
- A bash installer script hosted at a stable URL
- Three-stage bootstrap that converges to the same PyPI package as Options A/A2:
  1. **Stage 1 (bash):** Resolves Python 3.11+ using the same four-tier fallback as `resolve_python.sh` (detect system python3 → offer mise install → offer mise bootstrap → ask for manual path). Creates `.alcatraz/python` symlink.
  2. **Stage 2 (bash → Python stdlib):** Creates a temporary venv: `.alcatraz/python -m venv .alcatraz/.venv`. The `venv` module is Python stdlib, and `ensurepip` (also stdlib) provides pip inside the venv.
  3. **Stage 3 (same as pipx/uvx):** `.alcatraz/.venv/bin/pip install alcatrazer && .alcatraz/.venv/bin/alcatrazer init`. This is the exact same PyPI package that pipx/uvx would run. After installation completes, `rm -rf .alcatraz/.venv`.

**Key insight:** All three installation paths run the same `alcatrazer init` from the same PyPI package. The only difference is who provides the temporary Python environment:

```
pipx run alcatrazer init     →  pipx manages temp venv   →  alcatrazer init
uvx alcatrazer init          →  uv manages temp venv     →  alcatrazer init
curl | bash                  →  we manage temp venv      →  alcatrazer init
                                (resolve python,
                                 venv + pip install,
                                 run, delete venv)
```

One installer codebase. One test surface. Three entry points.

**Requires:** `bash` and `curl`. Both are truly universal on Linux/macOS. Python is NOT assumed — the script finds or installs it.

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

All three entry points run the exact same `alcatrazer` PyPI package. The only difference is who provides the temporary Python environment:

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

## Open Questions

1. **Tool files as package data:** The PyPI package bundles Dockerfile, scripts, etc. as package data. The `alcatrazer init` command extracts them into `.alcatraz/`. This means the PyPI package IS the release — no separate tarball or GitHub Releases needed. Is this sufficient or do we also want standalone tarballs?

2. **Versioning and updates:** How does a user update Alcatrazer in an existing project? Options:
   a. `pipx run alcatrazer update` (or `curl | bash` again) — re-extracts tool files, preserves config
   b. Re-run `init` with `--upgrade` flag — same effect, explicit intent
   c. Manual: user downloads new version and replaces `.alcatraz/src/` and `.alcatraz/container/`

3. **Development vs. deployment layout:** Our dev repo has `src/` and `container/` at repo root. Deployed installations have them inside `.alcatraz/`. The scripts need to auto-detect their location — probably via `Path(__file__).resolve().parent` which already works regardless of where the file sits. Need to verify all scripts use relative path resolution.

4. **Domain / URL for curl installer:**
   **Decision:** Start with raw GitHub URL (free, zero setup):
   `https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh`

   Future options for a nicer URL:
   - GitHub Pages on the repo → `https://greg-latuszek.github.io/alcatrazer/install.sh`
   - Custom domain on GitHub Pages → `https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/install.sh` (**costs money**)
   
   Note: `https://github.com/<user>/<repo>/install` is NOT a valid GitHub URL pattern. GitHub only serves raw files via `raw.githubusercontent.com`, release assets, or GitHub Pages.

5. **Should the installer also run `initialize_alcatraz.sh`?** The init command could offer to run the full initialization (UID, workspace, safe.directory) as the final step, or leave it as a separate command. Running it immediately gives a better "one command to set up" experience.

6. **The `.alcatraz/python` symlink:** The curl|bash path creates it during Python resolution. But pipx/uvx users also need it for the daemon. Should `alcatrazer init` create the symlink too (detecting the Python that's running it via `sys.executable`)?

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

### Step 1: Bundle tool files as package data
Copy Dockerfile, entrypoint, scripts (promote.py, watch_alcatraz.py, etc.) into `src/alcatrazer/data/`. Hatch auto-includes everything under `src/alcatrazer/` in the wheel.

### Step 2: Implement `alcatrazer init`
Interactive CLI that:
- Detects git repo, reads git config for default identity
- Asks questions (name, email, tool versions, promotion mode)
- Writes `alcatrazer.toml` and `.env.example` to repo root
- Extracts tool files from package data into `.alcatraz/`
- Updates `.gitignore` (adds `.alcatraz/` and `.env`)
- Creates `.alcatraz/python` symlink (from `sys.executable`)
- Optionally runs full initialization (UID, workspace, safe.directory)

### Step 3: Implement `alcatrazer update`
Re-extracts tool files from package data into `.alcatraz/`, preserving `alcatrazer.toml` and all state (workspace, marks, UID, logs).

### Step 4: Write `install.sh` (curl|bash bootstrap)
Thin bash script: resolve Python 3.11+ (four-tier), create temp venv, `pip install alcatrazer`, run `alcatrazer init`, delete temp venv. ~50 lines.

### Step 5: Tests (development)
- Unit tests for init (mock filesystem, verify files created)
- Integration test: run `alcatrazer init` in a temp git repo, verify layout
- Test `install.sh` with faked PATH (same approach as resolve_python tests)

### Step 6: Post-installation verification tests
See "Trust and Verification" section below — bundle tests and source into `.alcatraz/` so end users can verify the installed tool.

### Step 7: Publish to PyPI
`uvx twine upload dist/*` — first real release (0.1.0).

---

## Trust and Verification

### The trust problem

Alcatrazer is a security tool. It asks users to run AI agents inside Docker containers with the promise that their host is protected. This creates a fundamental trust question:

> "Isn't Alcatrazer a wise social engineer that claims to protect my laptop but instead is a thief itself?"

If the tool claims to isolate agents from host secrets, the user must be able to verify that claim — not just by reading marketing copy, but by reading code and running tests.

### Trust layers

**Layer 1: Open source on GitHub.** The entire codebase is public. Anyone can read the Dockerfile, the entrypoint, the docker-compose volumes, the promotion scripts. This is the foundation — but it's not sufficient, because the user has no guarantee that what's installed on their machine matches what's on GitHub.

**Layer 2: Installed source is readable.** The installation extracts tool files into `.alcatraz/` — not compiled bytecode, not obfuscated, not downloaded from a different source. The user can read every script that runs on their machine:

```bash
# "What exactly is this tool doing on my host?"
cat .alcatraz/src/watch_alcatraz.py
cat .alcatraz/container/Dockerfile
cat .alcatraz/container/docker-compose.yml
```

**Layer 3: Post-installation verification tests.** The test suite is bundled alongside the tool files in `.alcatraz/tests/`. The user can run the same tests that developers run to verify the security model:

```bash
# "Prove to me this tool does what it claims"
.alcatraz/python -m unittest discover -s .alcatraz/tests -v
```

These tests verify:
- Container runs as phantom UID (no matching host user)
- No host credentials leak into the container (SSH keys, GPG keys, git config)
- Only explicitly passed environment variables are visible
- Docker socket is not mounted
- No git remotes configured inside the container
- Promotion rewrites identity correctly (no alcatraz identity leaks to outer repo)
- Files inside workspace are owned by phantom UID

**Layer 4: Diff against GitHub.** The user can verify that what's installed matches the published source:

```bash
# "Is what I installed the same as what's on GitHub?"
diff -r .alcatraz/src/ <(curl -sL https://github.com/.../archive/v1.0.tar.gz | tar xzf - --strip=1 -C /tmp/alcatrazer-check && echo /tmp/alcatrazer-check/src/)
```

The `alcatrazer verify` command (future) could automate this — download the release tarball, compare checksums file-by-file, report any differences.

### What gets bundled in `.alcatraz/`

The installation includes both tool files AND their tests, so the user has the complete picture:

```
.alcatraz/
├── src/                    <-- the tool (readable source)
│   ├── promote.py
│   ├── watch_alcatraz.py
│   ├── inspect_promotion.py
│   ├── initialize_alcatraz.sh
│   └── resolve_python.sh
├── container/              <-- Docker infrastructure (readable)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── tests/                  <-- verification tests (runnable)
│   ├── test_promote.py
│   ├── test_watch_alcatraz.py
│   ├── test_python_resolution.py
│   ├── seed_alcatraz.sh
│   └── smoke_test.sh       <-- Docker integration test
├── workspace/              <-- agent workspace
└── ... (state files)
```

### Target audience

The target user is a developer. They can read Python and bash. They understand Docker volumes and git. They may not read the source before first use — but knowing they *can* is itself a trust signal. And when a security-conscious team lead asks "how do we know this is safe?", the answer is: "read the source, run the tests, diff against GitHub."

### Design principle

This is an extension of Iceberg Principle 1 (fight for security): **the tool's security claims must be verifiable by the user, not just asserted by the author.** Open source is necessary but not sufficient. The installed tool must carry its own proof.
