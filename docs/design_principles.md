# Alcatrazer Design Principles

Consolidated from feature documents in `docs/features/`. 
Each principle links to the source document where it was first established and reasoned about.

---

## The Iceberg Principles

These two principles are the foundation. Every other decision is subordinate to them.

### Principle 1: Fight for Security — Think About Attack Surfaces

Every feature, every configuration option, every file placement is evaluated through the lens of: 
does this create a new attack surface? If yes, is it necessary? Can it be eliminated? 
Security is not a feature — it is the architecture.

*Source: [auto-promotion-daemon.md](features/auto-promotion-daemon.md)*

### Principle 2: Inmates Are Not Aware They Live in Alcatraz

Agents working inside the container must not be able to discover that their environment was created by Alcatrazer. 
Zero footprint inside the workspace. No alcatrazer-specific files, no branded hooks, no recognizable configuration patterns, 
no environment variables that hint at the tool's name. The workspace must look like an ordinary git repository 
in an ordinary container.

If an agent discovers it is inside Alcatraz, it can search GitHub, read source code, 
identify weaknesses, and attempt exploits.

Concrete rules:
1. Nothing from Alcatrazer may exist inside the workspace — no hooks, no config files, no markers
2. Nothing from Alcatrazer may be visible in the container environment — no branded env vars, no identifiable process names
3. The daemon operates entirely from the host side — it observes the workspace from outside, never touches the inside

*Source: [no_sign_they_work_in_alcatraz.md](features/no_sign_they_work_in_alcatraz.md)*

---

## Trust & Verification

### Security Claims Must Be Verifiable

The tool's security claims must be verifiable by the user, not just asserted by the author. 
Open source is necessary but not sufficient. The installed tool must carry its own proof.

Four trust layers:
1. **Open source on GitHub** — anyone can read the code
2. **Installed source is readable** — extracted as plain Python/bash, not bytecode or obfuscated
3. **Bundled verification tests** — user runs `alcatrazer test` to prove security properties
4. **Diff against GitHub** — user can verify installed code matches published source

*Source: [install_method.md](features/install_method.md)*

### Stdlib Only — No Third-Party Dependencies

Alcatrazer uses Python standard library only. No PyPI dependencies.

**Why:** Trust is recursive. If users must audit alcatrazer to trust it, 
and alcatrazer depends on third-party packages, users must audit those too — 
and their transitive dependencies. Stdlib-only means: read alcatrazer code and tests, nothing else. 
Python developers already trust Python and its standard library.

**How to apply:** Before adding any `pip install` dependency, exhaust stdlib alternatives. 
If stdlib truly cannot do the job, this principle must be revisited as an explicit decision, not a quiet drift.

### Tests Bundled with Installation

The test suite ships alongside the tool code. End users run `alcatrazer test` to verify 
the security model on their own machine with their own Docker setup.

Tests verify: phantom UID isolation, no credential leaks, no Docker socket, 
no git remotes, identity rewriting, file ownership.

*Source: [install_method.md](features/install_method.md), [as_little_bash_as_necessary.md](features/as_little_bash_as_necessary.md)*

---

## Zero Pollution

### Target Repo Gets Almost Nothing

Alcatrazer must not pollute the target repository. The only things that touch the repo proper:
- `alcatrazer.toml` — version controlled, captures project decisions
- `.gitignore` entries for `.alcatrazer/`, `.<workspace>/`, and `.env`
- `.env.example` — template for API keys

Everything else lives inside `.alcatrazer/` (gitignored).

*Source: [install_method.md](features/install_method.md)*

### Workspace Is a Sibling, Not a Child

The agent workspace lives in a separate, randomly named top-level directory (e.g., `.devspace-7f3a/`), 
not inside `.alcatrazer/`.

**Why:** Docker bind mounts expose the full host path via `/proc/self/mountinfo`. 
If the workspace lived inside `.alcatrazer/`, agents would see "alcatrazer" in the mount path — 
violating Principle 2.

*Source: [no_sign_they_work_in_alcatraz.md](features/no_sign_they_work_in_alcatraz.md)*

---

## Agent Identity & Isolation

### Random Human Identity

Agents get a randomly generated human-looking name and email for their git commits. 
Generated once per initialization, stored in `.alcatrazer/agent-identity`.

**Why:** If agents see "Alcatraz Agent" or "alcatraz@localhost" in `git log`, 
they discover the tool. The identity must be indistinguishable from a real developer.

*Source: [no_sign_they_work_in_alcatraz.md](features/no_sign_they_work_in_alcatraz.md)*

### Phantom UID

The container runs as a UID that doesn't match any host user (found via `getent passwd`). 
Files in the workspace are owned by this phantom UID.

**Why:** Prevents the agent from mapping container user to a real host account.

*Source: [install_method.md](features/install_method.md), [no_sign_they_work_in_alcatraz.md](features/no_sign_they_work_in_alcatraz.md)*

### Container Names Hide the Tool

Docker service: `workspace` (not `alcatraz`). Container: `workspace` (not `agent-alcatraz`). 
Dockerfile stages: `dev-base`/`dev` (not `alcatraz-base`/`alcatraz-dev`). 
Env vars: `USER_UID` (not `ALCATRAZ_UID`).

We don't hide Docker — agents can trivially detect containers and that's fine. 
The threat is agents discovering **Alcatrazer**, not Docker.

*Source: [no_sign_they_work_in_alcatraz.md](features/no_sign_they_work_in_alcatraz.md)*

---

## Snapshot & Starting Point

### Snapshot, Not Clone

The workspace gets a flat snapshot of the outer repo's main branch — files only, no git history. 
One initial commit with a generic message ("Initial commit").

**Why:** History increases attack surface — agents could `git log` to discover developer identities, 
commit patterns, internal references. Agents don't need history to write code — they need the current codebase.

*Source: [start_from_existing_repo.md](features/start_from_existing_repo.md)*

### Main Branch Only

Hard rule: always start from main/master. No feature branches, no arbitrary refs.

**Why:** The user always knows what happened — "agents started from my main branch." 
No accidental cross-branch contamination. Clear mental model: main → workspace → main.

*Source: [start_from_existing_repo.md](features/start_from_existing_repo.md)*

---

## Promotion

### Unidirectional: Inner → Outer Only

Commits flow from workspace to outer repo only. Identity is rewritten to match `alcatrazer.toml`.

### Promotion is Idempotent

Running promotion when there's nothing new is a no-op. Safe to call repeatedly.

### Daemon Watches from Outside

The promotion daemon runs on the host, polling the workspace `.git/` directory. 
It never writes into the workspace — zero footprint inside (Principle 2).

*Source: [auto-promotion-daemon.md](features/auto-promotion-daemon.md)*

---

## Code Organization

### Bash is the Bootstrap, Python is Everything Else

Bash exists to get Python running. Once Python is available, all subsequent logic is Python. 
Remaining bash: `resolve_python.sh` (runs before Python exists), container `entrypoint.sh` 
(root privilege dropping via `gosu`), and the thin wrapper in `initialize_alcatraz.sh`.

*Source: [as_little_bash_as_necessary.md](features/as_little_bash_as_necessary.md)*

### Single Self-Contained Package

All code lives inside `src/alcatrazer/` — Python modules, Docker templates (`container/`), 
bash bootstrap (`scripts/`), config template (`templates/`), test suite (`tests/`). 
The PyPI package is the single source of truth. One codebase, one test surface.

*Source: [as_little_bash_as_necessary.md](features/as_little_bash_as_necessary.md), [install_method.md](features/install_method.md)*

### Three Entry Points, One Package

```
pipx run alcatrazer init     →  pipx manages temp venv   →  alcatrazer init
uvx alcatrazer init          →  uv manages temp venv     →  alcatrazer init
curl | bash                  →  we manage temp venv      →  alcatrazer init
```

All paths converge to the same PyPI package. The only difference is who provides the Python environment.

*Source: [install_method.md](features/install_method.md)*

---

## Platform & Compatibility

- **Python 3.11+** required (for `tomllib`)
- **Linux** primary, **macOS** secondary
- **No root access** required on the host
- **Docker** required for container isolation