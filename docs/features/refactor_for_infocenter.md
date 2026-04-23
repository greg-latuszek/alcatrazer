# Refactor — Alcatrazer filesystem + infocenter abstractions

## Status: Deferred (post-v1). Captured now so the idea doesn't evaporate.

## Why this doc exists

The `alcatrazer` command stack (`init` / `start` / `stop` / `clear`) and the
promotion daemon (`daemon.py`, `promote.py`) diverged silently during the
install_method.md refactor. Nothing anchored them to a shared notion of
"where things live under `.alcatrazer/`", so:

- `daemon.py:62` still expects the workspace at `alcatraz_dir / "workspace"`,
  but the command stack now puts it at `project_dir / <generated-name>`
  (e.g. `.devspace-7f3a`), with the name stored in `.alcatrazer/workspace-dir`.
- `daemon.py:116` and `promote.py:447` still read `project_dir / "alcatrazer.toml"`,
  but the config split moved that file to `.alcatrazer/config.toml` — and the
  public `coding-environment.toml` at the root has a different schema.
- `default_project_dir` in both daemon and promote assumes the script lives
  next to the project root, but `extract_package_source` installs the tree at
  `.alcatrazer/src/alcatrazer/`, making the default path math wrong.

Every single one of these bugs traces to the same root cause: **knowledge of
the on-disk layout was duplicated at each caller, so when the layout changed
in one caller, the other silently drifted**.

## The proposed abstraction — two layers

**1. `alcatrazer.paths` / `alcatrazer.filesystem` — the thin layer.**

One module that owns every path under `.alcatrazer/` and every pointer it
knows how to resolve. Callers stop concatenating `alcatraz_dir / "config.toml"`
inline; they call `paths.config_toml(project_dir)` (or similar).

Candidates for residency, by topic:

| Question | Current location | Storage today |
|---|---|---|
| Where is `.alcatrazer/`? | hardcoded everywhere | `project_dir / ".alcatrazer"` |
| What's the workspace directory name? | `identity.load_workspace_dir()` ✅ | `.alcatrazer/workspace-dir` |
| Where is the workspace? | inline `alcatraz_dir / "workspace"` ❌ | `project_dir / <workspace-dir>` |
| Where is the agent identity? | `identity.load_identity()` ✅ | `.alcatrazer/agent-identity` |
| Where is the phantom UID? | `identity.ensure_phantom_uid()` ✅ | `.alcatrazer/uid` |
| Where is per-developer config? | hardcoded `project_dir / "alcatrazer.toml"` ❌ | `.alcatrazer/config.toml` |
| Where is the coding-environment file? | hardcoded in `_load_coding_environment` | `project_dir / <name from config>` |
| Where is the `.env` hash snapshot? | hardcoded in `env_file_changed` | `.alcatrazer/env.hash.last` |
| Where is the `.last` coding-env snapshot? | hardcoded in `coding_environment_changed` | `.alcatrazer/coding-environment.toml.last` |
| Where are the promotion marks? | hardcoded in `daemon.py`/`promote.py` | `.alcatrazer/promote-*-marks` |
| Where is the daemon PID? | hardcoded in `daemon.py` | `.alcatrazer/promotion-daemon.pid` |
| Where is the daemon log? | hardcoded in `daemon.py`, `inspect.py` | `.alcatrazer/promotion-daemon.log` |

The ✅ rows already have accessors in `identity.py` — that's the seed pattern
to generalize.

**2. `alcatrazer.infocenter` — the composite query layer.**

One level up. Hides the *storage mechanism*, not just the paths. Callers ask:

- `infocenter.daemon_config(project_dir) -> DaemonConfig` — composite of
  `[promotion-daemon]` fields merged with defaults, plus promotion identity,
  plus resolved source/target/marks paths. Daemon stops knowing about
  `config.toml` entirely.
- `infocenter.promotion_identity(project_dir) -> (name, email)` — runs the
  three-layer priority chain (git config → `[promotion]` → CLI override).
  `promote.py` stops opening TOMLs.
- `infocenter.workspace_paths(project_dir) -> WorkspacePaths` — `{outer,
  inner, marks_dir, agent_identity, ...}`. Callers stop composing paths
  from two different sources.
- `infocenter.drift_signals(project_dir, prison) -> DriftSignals` — the
  five booleans `_subsequent_run` already computes. Composite query, lets
  `cmd_clear` / `cmd_stop` / future `cmd_status` all share the same truth.

The payoff is directional: a future migration ("let's move daemon config into
a SQLite row", "let's memoize the workspace path in-memory") touches
`infocenter` only — every caller is shielded.

## Why deferred (not "never")

1. **PyPI is the concrete next goal.** Daemon refresh → daemon/lifecycle
   wiring → `install.sh` → PyPI publish. None of those strictly require the
   abstraction; all of them ship without it. The refactor is a side-quest
   relative to v0.1.0.

2. **Size estimate — two-to-four focused days.** `paths` module ~100 LoC,
   `infocenter` ~150 LoC, ~50 call-site edits across `start.py`,
   `docker_prison.py`, `daemon.py`, `promote.py`, `selftest.py`, `cli.py`,
   plus a test-migration wave. Not a one-commit job.

3. **Premature abstraction risk.** The daemon isn't even wired into the
   `init` / `start` / `stop` / `clear` lifecycle yet. That wiring will teach
   us what the daemon actually needs to ask about ("should I be running right
   now?", "what branches are paused?", "did the user run `alcatrazer clear`
   since my last poll?"). Designing `infocenter` *before* seeing those
   callers risks shipping an API that misses the real shape. Defer → design
   with evidence → land a sharper v1.

4. **Prerequisite: the callers must first be correct.** You can't cleanly
   extract an abstraction from code that's silently wrong (the daemon's
   `alcatraz_dir / "workspace"` would get extracted as an accessor and
   propagated as a fact). The daemon refresh (install_method.md Step 6a-6e)
   must land first — that's what stabilizes the real layout the abstraction
   will describe.

## Preconditions for coming back to this

Don't open this doc again until all four of these hold:

1. Daemon refresh (install_method.md Step 6a-6e) landed — daemon and
   promote.py read from the correct paths, tests migrated to new layout.
2. Daemon lifecycle integration landed — daemon starts/stops with
   `alcatrazer start` / `stop`, is cleaned up by `alcatrazer clear`.
   This is what surfaces the real composite queries.
3. PyPI v0.1.0 published — the work that was blocked by not having this
   abstraction is now out the door.
4. At least one real pain point from a user or maintainer pointing at path
   duplication — e.g. "I tried to rename `.alcatrazer/` to `.dev-sandbox/`
   and had to touch seven files". Without that, you're abstracting ahead of
   demand.

## Shape sketch (for when we come back)

```python
# alcatrazer/paths.py — cheap, mechanical
ALCATRAZ_DIR = ".alcatrazer"
CONFIG_TOML = "config.toml"
WORKSPACE_DIR_POINTER = "workspace-dir"
ENV_HASH_LAST = "env.hash.last"
CODING_ENV_LAST = "coding-environment.toml.last"
# ... rest of the file-name constants

def alcatraz_dir(project_dir: Path) -> Path: ...
def config_toml(project_dir: Path) -> Path: ...
def workspace_dir(project_dir: Path) -> Path: ...   # resolves via pointer file
def coding_env_toml(project_dir: Path) -> Path: ... # resolves via config pointer
def env_file(project_dir: Path) -> Path: ...
def env_hash_last(project_dir: Path) -> Path: ...
# ... etc.
```

```python
# alcatrazer/infocenter.py — composite queries; hides storage
@dataclass(frozen=True)
class DaemonConfig:
    interval: int
    branches: str | list[str]
    mode: str
    verbosity: str
    max_log_size_kb: int
    promotion_name: str
    promotion_email: str
    source_repo: Path
    target_repo: Path
    marks_dir: Path
    pid_file: Path
    log_file: Path

def daemon_config(project_dir: Path) -> DaemonConfig: ...
def promotion_identity(project_dir: Path, overrides: tuple[str, str] = ("", "")) -> tuple[str, str]: ...
def drift_signals(project_dir: Path, prison: Alcatraz) -> DriftSignals: ...
```

Callers become call-sites with zero path literals. Acceptance criterion:
`grep -r '".alcatrazer"' src/alcatrazer/ | wc -l` returns a single-digit
number (ideally 1, inside `paths.py`).

## Decision log

- **2026-04-23** — Surface captured while fixing the daemon drift
  (`docs/features/install_method.md` Step 6a-6e planning). Deferral decided
  for the reasons above. Revisit after daemon lifecycle wiring lands.
