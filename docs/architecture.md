---
title: "Alcatrazer — System Architecture"
purpose: "System-level synthesis of how Alcatrazer is built. Brownfield documentation of v0.1.0."
date: 2026-05-11
status: brownfield-documented
generated_by: bmad-document-project (deep scan)
inputs:
  - 'docs/prds/PRD-001-alcatrazer.md (vision + FRs)'
  - 'docs/design_principles.md (architectural principles)'
  - 'docs/features/*.md (feature-level designs)'
  - 'src/alcatrazer/**/*.py (shipped code)'
---

# Alcatrazer — System Architecture

> This is the **system-level view**. For the *why* see
> [`docs/prds/PRD-001-alcatrazer.md`](prds/PRD-001-alcatrazer.md); for the
> *invariants* see [`docs/design_principles.md`](design_principles.md); for
> *feature-level decisions* see `docs/features/*.md`. This document explains
> the *how* — how the modules fit together and where the seams are.

---

## 1. One-paragraph summary

Alcatrazer is a Python-stdlib-only CLI that turns the developer's project
directory into a sandbox host. A user invokes one of seven commands
(currently six shipped: `init`, `start`, `visit`, `stop`, `clear`, `test`;
`status` designed in [`change_promotion_machinery.md`](features/change_promotion_machinery.md)
but not yet implemented). The CLI delegates to a lifecycle orchestrator
(`start.py`) that drives a **sandboxing port** (`Alcatraz` abstract base) via
its concrete adapter (`DockerPrison`). A separate **promotion daemon** runs
on the host outside the sandbox, polling the workspace's `.git/` and
streaming new commits back to the developer's outer repository under the
developer's identity. Cooperation between CLI and daemon uses on-disk
artifacts under `.alcatrazer/` — no IPC, no sockets, no in-memory shared
state.

## 2. Layered view

```
┌──────────────────────────────────────────────────────────────────┐
│ CLI (cli.py)                                                     │
│ argparse → subcommands → delegates to start.cmd_*                │
└─────────────────────┬────────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────────┐
│ Lifecycle orchestrator (start.py)                                │
│ cmd_init / cmd_start / cmd_visit / cmd_stop / cmd_clear /        │
│ cmd_selftest. Wizards, file generators, state-change detection.  │
│ Maps each CLI verb to a state-machine transition.                │
└──────┬─────────────────────────┬──────────────────────┬──────────┘
       │                         │                      │
┌──────▼─────────────┐  ┌────────▼─────────────┐  ┌─────▼──────────┐
│ Sandboxing port    │  │ Promotion machinery  │  │ Workspace prep │
│ alcatraz.py (ABC)  │  │ daemon.py +          │  │ snapshot.py +  │
│                    │  │ daemon_lifecycle.py  │  │ identity.py    │
│ + adapter:         │  │ + promote.py         │  │                │
│ docker_prison.py   │  │                      │  │                │
└──────┬─────────────┘  └────────┬─────────────┘  └─────┬──────────┘
       │                         │                      │
┌──────▼─────────────────────────▼──────────────────────▼──────────┐
│ Shared on-disk state (.alcatrazer/)                              │
│ state.json · Dockerfile · entrypoint.sh · config.toml ·          │
│ workspace-dir · agent-identity · uid · promotion-daemon.{pid,log}│
│ paused-branches.json · promoted-tips.json · promote-*-marks      │
└──────────────────────────────────────────────────────────────────┘
       │                                                  │
┌──────▼─────────────┐                              ┌─────▼────────┐
│ Sandbox runtime    │                              │ Sibling      │
│ (Docker today)     │  ── bind mount /workspace ──▶│ workspace    │
│ phantom UID,       │                              │ .{word}-XXXX │
│ ai-base layer      │                              │ .git/        │
└────────────────────┘                              └──────────────┘
```

## 3. Hexagonal sandboxing — the backend abstraction (FR-6, NFR-7)

### The port: `alcatraz.py`

`Alcatraz` is an `abc.ABC` with **16 abstract methods** grouped into five
concept boundaries (the per-concept interfaces required by FR-6):

| Concept boundary (FR-6 AC) | Port methods |
|---|---|
| Recipe management | `generate_prison(coding_environment)`, `needs_rebuild(coding_environment)`, `recipe_hash(coding_environment)`, `image_matches(expected_hash)` |
| Build | `build()`, `image_exists()` |
| Lifecycle | `start()`, `resume()`, `stop()`, `is_running()`, `exists()`, `remove()` |
| Command execution | `exec(command)` (streams output), `query(command)` (captures), `shell()` (interactive `execvp`) |
| (Identity / network / credentials) | Carried by the backend through the build args + mount config — see DockerPrison below |

Exception hierarchy: `PrisonError` → `PrisonBuildError`, `PrisonStartError`.
All carry `stdout` and `stderr` strings per the
[install_method.md](features/install_method.md) "Build & Startup Error
Handling" contract — callers surface raw subprocess output to the user.

### Distinguishing operations — why two execution methods

`exec(command)` and `query(command)` look similar but have different
contracts:

- `exec` does NOT capture output — it streams to the caller's terminal. Use
  for human-facing work (long builds with progress, interactive flows).
- `query` captures `stdout`, `stderr`, `returncode` and returns a
  `subprocess.CompletedProcess`-shaped object. Use for programmatic checks
  (selftest invariants, health probes).

Neither raises on non-zero exit — both return the exit code so the caller
decides. This contract is observable at the port level, so an alternative
backend (PodmanPrison, SysboxPrison, future) must preserve it.

### The adapter: `docker_prison.py`

`DockerPrison(Alcatraz)` is currently the only adapter. It shells out to
`docker build / run / start / stop / ps / exec / rm` via `subprocess`. Key
implementation details that constrain future adapter swaps:

**Per-repo identity** (Phase 1.2.5 — NFR-3 concurrency invariant):
`_identity_for_project(project_dir)` produces `<sanitized-basename>-<12hex>`
where the hex is the first 12 chars of SHA-256 of the canonical absolute
path. Image tag becomes `alcatraz-workspace:<id>`; container name becomes
`workspace-<id>`. Two Alcatrazers on different repos coexist with zero
collision; same repo always produces same identity (stable mapping).

**Dockerfile generation pipeline** (FR-30, FR-32, FR-34):

```
coding-environment.toml
  ├── _render_dockerfile_body(data)
  │     ├── _DOCKERFILE_DEV_BASE     (stage 1 — security + infra, always identical)
  │     ├── _DOCKERFILE_AI_BASE      (stage 2 — Claude Code CLI, hardcoded for MVP)
  │     ├── dev stage                (stage 3 — generated from [languages] + [os])
  │     │   ├── _render_apt_install(packages)
  │     │   ├── _render_mise_uses(languages)
  │     │   │   (splits AQUA_ATTESTATION_MISALIGNED managers
  │     │   │    into their own RUN with MISE_AQUA_GITHUB_ATTESTATIONS=false)
  │     │   └── _render_verify_block(languages)
  │     └── _DOCKERFILE_ENTRYPOINT_TAIL  (USER root + COPY entrypoint.sh + ENTRYPOINT)
  ├── _compute_config_hash(data)         (SHA-256 of body, 16 hex chars)
  └── _render_dockerfile(data)           (body + alcatrazer.config_hash LABEL)
```

The `alcatrazer.config_hash` LABEL is baked into the image. `image_matches`
reads it back via `docker inspect` to detect drift (e.g. user wiped
`.alcatrazer/` and re-ran `init`) without comparing on-disk Dockerfile
text — that's the staleness detection from Phase 1.2.6.

**`entrypoint.sh` is copied, not generated.** Sits at
`src/alcatrazer/container/entrypoint.sh`. Per [memory](../README.md) and
this session's correction: code generates Dockerfiles, but entrypoint.sh is
fixed/constant and lives as a readable artifact rather than being
generated or embedded in Python (visibility win).

### Where the backend abstraction is solid vs. where it leaks

**Solid:** `alcatraz.py` is genuinely backend-neutral — methods take no
docker-specific arguments, return no docker-specific types. The exception
hierarchy uses `Prison*` not `Container*`. Callers (lifecycle, selftest,
daemon — indirectly) never construct docker commands themselves.

**Leak points to watch when adding a non-Docker backend:**

- `docker_prison.py` uses `os.execvp("docker", ...)` in `shell()`. The
  port's contract says "replaces the calling process via execvp or its
  backend equivalent" — but a non-process-replacing backend (e.g. a VM
  agent) would have to redesign signal-flow semantics.
- `query()` returns `subprocess.CompletedProcess`. That's a Python stdlib
  type — fine for any subprocess-shelling backend. A network-attached
  backend would need to build the same shape.
- The Dockerfile generator (`_render_dockerfile_body`) is docker-specific
  by design — that's stage 1 of the adapter's `generate_prison()`. A
  non-Docker backend would emit different artifacts (Containerfile,
  cloud-init, VM image config) from the same `coding_environment` dict.
- `safe.directory` env-var injection (`daemon.py`) is a phantom-UID
  workaround specific to bind-mount semantics. A backend that maps UIDs
  differently (e.g. user namespaces with subuid) might not need it.

## 4. Promotion machinery — the host-side flow (FR-13 to FR-20)

### Why it lives outside the sandbox

[Design Principle 2](design_principles.md) and FR-12: the operational
machinery never writes into the workspace — agents inside the sandbox must
see zero footprint of the surrounding tool. So the promotion daemon runs
**on the host**, polls the workspace `.git/` directory from outside, and
streams commits out via `git fast-export | rewrite-identity | git
fast-import`.

### Process model

The daemon is a separate Python process spawned by `alcatrazer start`:

```
alcatrazer start
  └─ daemon_lifecycle.launch_sync_daemon(project_dir)
       └─ subprocess.Popen([python, "-m", "alcatrazer.daemon", "--project-dir", ...],
                           start_new_session=True,
                           stdout/stderr/stdin=DEVNULL)
              └─ alcatrazer.daemon.main()
                   ├─ resolve workspace + phantom-UID + safe.directory env-vars
                   ├─ check_pid + write_pid       (single-instance guard)
                   ├─ load [promotion-daemon] config from .alcatrazer/config.toml
                   ├─ resolve_identity            (3-layer chain — see below)
                   ├─ set up RotatingFileHandler (.alcatrazer/promotion-daemon.log)
                   └─ polling loop:
                        ├─ run_cycle()
                        │    ├─ mirror mode:
                        │    │    ├─ check_resolved_conflicts → unpause branches
                        │    │    └─ promote_with_conflict_handling(...)
                        │    │         ├─ detect_diverged_branches
                        │    │         ├─ for diverged → create conflict/resolve-<b>-<ts>
                        │    │         ├─ for clean    → promote(...) [fast-export pipeline]
                        │    │         └─ save_paused_branches
                        │    └─ alcatraz-tree mode:
                        │         └─ promote(...) with namespace="alcatraz"
                        └─ shutdown_event.wait(timeout=interval)
```

`start_new_session=True` puts the daemon in its own process group so
Ctrl+C against the CLI doesn't propagate. The daemon outlives the CLI
process; on `alcatrazer stop`/`clear` it's signaled via a four-step
cooperative protocol (see §4.5).

### The fast-export pipeline (`promote.py`)

```
git -C <workspace> fast-export <refs> [--import-marks=<marks>] --export-marks=<marks>
  │
  ▼
rewrite_identity(stream, name, email)
  │  byte-mode regex on ^author/^committer with trailing "<ts> <tz>"
  │  anchor (so it won't match text inside data sections)
  ▼
[optional] rewrite_refs(stream, namespace)
  │  prefixes refs/heads/<b> → refs/heads/<namespace>/<b>
  │  (alcatraz-tree mode only)
  ▼
git -C <outer-repo> fast-import --force --quiet
   [--import-marks=<marks>] --export-marks=<marks>
```

The marks files (`promote-export-marks`, `promote-import-marks`) live
under `.alcatrazer/` and make subsequent runs incremental — only commits
new since the last successful pipeline are exported. This is the
mechanism behind FR-16 idempotence ("running it when nothing is new is a
no-op") and FR-20 commit-count-pending observability (via `snapshot.
count_unpromoted_commits`).

The stream is processed in **bytes mode**, not text — `git fast-export`
embeds blob content inline and real git histories contain non-UTF-8 bytes
(images, archives). UTF-8 decoding would corrupt them.

### Identity rewriting — the three-layer chain (`resolve_identity`)

Priority (lowest → highest):

1. `git config user.name/user.email` in the **target** outer repo
2. `.alcatrazer/config.toml [promotion]` section (`name = "…"`, `email = "…"`)
3. `--author-name` / `--author-email` CLI flags

A `[promotion]` section in `.alcatrazer/config.toml` overrides git config —
this is how the developer keeps work-vs-personal identity separation
without leaking to inside the workspace. The result is what the rewriter
substitutes into both `author` and `committer` lines for every promoted
commit.

### Two modes

**`mirror` (default):** commits land on the **same-named branch** in the
outer repo. If the outer branch has diverged from what was last promoted,
the agent's work is redirected to a `conflict/resolve-<branch>-<timestamp>`
branch and the daemon pauses promotion of that branch. When the user
deletes or merges the conflict branch, `check_resolved_conflicts` detects
this on the next cycle and unpauses.

**`alcatraz-tree`:** all promoted commits land under
`refs/heads/alcatraz/<branch>`. No conflicts possible (different
namespace), but the agent's work is isolated from the developer's branch
namespace. Useful for review-before-merge workflows. (Designated for
deprecation per `change_promotion_machinery.md` — the refactor target.)

### Cooperative shutdown protocol (`daemon_lifecycle.shutdown_sync_daemon`)

A four-step dance between CLI (`cmd_stop`/`cmd_clear`) and daemon:

```
CLI                                       Daemon
───────────────────────────────────────   ────────────────────────────
1. state.update_state(                    (polling loop running)
     daemon_shutdown="requested")
2. os.kill(pid, SIGTERM) →              ▶ SIGTERM handler sets
                                          shutdown_event
3. wait up to 10s for process exit       (drops out of polling loop)
   (SIGKILL fallback on timeout)         └─ finally block:
                                               read state.json
                                               (prefix = "graceful"
                                                if "requested", else
                                                "unexpected")
                                               run_cycle()  (final sync)
                                               log + exit
4. parse log tail for outcome             (process exited)
5. state.update_state(
     daemon_shutdown="done")
```

The state.json field is **cooperation hint, not behavior gate** — the
daemon's final-sync logic is the same regardless. Only the log prefix
("graceful shutdown" vs "unexpected shutdown") differs. This keeps the
unexpected-crash path well-tested: it always runs.

`ShutdownResult.outcome` is one of: `no_daemon`, `synced`, `conflict`,
`failed`, `timeout`. Parsed from the daemon's log tail via regex
(`_SYNCED_RE`, `_CONFLICT_RE`, `_FAILED_RE`). The regex/log coupling is
explicit and tight — the docstring calls out that both modules must
update in lockstep if log format changes.

### Eventual consistency guarantee

If the daemon crashes between `run_cycle()` polls, no commit is lost: the
marks files persist on disk; the next daemon start resumes from the last
exported mark. The shutdown final-sync closes the smaller race between
the last poll and SIGTERM. Both mechanisms together cover FR-24
("suspend and reset are idempotent — agent commits eligible for
promotion are flushed before runtime tear-down").

## 5. Stealth & identity layer (FR-9, FR-10, FR-11, US-11)

### Phantom UID (`identity.py`)

`detect_phantom_uid(start=1001)` walks UIDs upward calling
`getent passwd <n>` and `getent group <n>`. The first UID that fails both
lookups is the phantom — no host user matches, so a container escape has
nothing to chown to. `ensure_phantom_uid` persists the result at
`.alcatrazer/uid` and reuses on rebuilds (image rebuilds must keep UID
consistent or cached volumes would have stale ownership).

The UID is passed as a Docker build arg (`USER_UID`) into stage 1
(`dev-base`), which `useradd`s an `agent` user with that exact UID. The
container runs as `agent` (via the entrypoint's `gosu` drop after root
initialization), so the bind-mounted `/workspace` ends up with files
owned by phantom-UID on both sides of the mount.

### Random agent identity (`identity.py`)

`generate_identity(seed=None)` samples (first × last × digit-count ×
pattern × domain) from 50 × 50 × 3 × 3 × 20 corpora = ~450 000
combinations. RFC 5322 mailbox shape; the email local-part is one of
three patterns (`full`, `initial`, `initial_underscore`) with optional
trailing digits.

The identity is generated **once** at `init` (or first `start` after
init), persisted to `.alcatrazer/agent-identity`, and reused for the
workspace's git config + every workspace commit. The promotion daemon
uses the **developer's** identity (from `[promotion]` or git config) when
rewriting — so the agent identity never leaks to the outer repo.

### Workspace directory (`identity.py`)

`generate_workspace_dir_name()` produces `.{word}-{4hex}` from a 30-word
generic corpus (`devspace`, `codework`, `project`, `sandbox`,
`buildenv`, …). The leading `.` makes it hidden on Linux/macOS. The
4-hex suffix gives 65 536 namespace per word — collision-resistant on a
single laptop.

It's a **sibling** of the project directory, not a child of
`.alcatrazer/`. Per [design_principles](design_principles.md) "Workspace
is a Sibling, Not a Child": Docker bind mounts expose the full host path
via `/proc/self/mountinfo`; if the workspace were inside `.alcatrazer/`,
agents would see "alcatrazer" in the mount path — violating Principle 2.

### Snapshot extraction (`snapshot.py`)

`snapshot_workspace(outer_repo, workspace)` is the FR-7 implementation:

```
require_git_repo                  → reject non-git project dirs
detect_default_branch             → origin/HEAD → existence check → AmbiguousBranchError
_warn_if_on_non_default_branch    → advisory: snapshot is always from default
extract_snapshot                  → git archive <branch> | tar -x  --exclude=.alcatrazer --exclude=.env
filter_gitignore                  → remove any '.alcatrazer/?' line from snapshotted .gitignore
create_initial_commit             → git add -A && git commit --allow-empty -m "Initial commit"
```

No git history travels — only the file contents of the default branch.
The single "Initial commit" is authored by the agent identity (workspace
git config is set before commit). Per FR-13, this is the "starting
branch" — the daemon promotes back to this branch in the outer repo.

### Selftest invariants (`selftest.py`) — FR-25

`_AlcatrazSecurityInvariants` is a mixin holding 13 read-only assertions
that any running Alcatraz must pass. Two callers compose it with
`unittest.TestCase`:

1. **`integration_tests/test_smoke.py`** — CI-mode end-to-end test against
   a fresh tempdir project.
2. **`alcatrazer.start.cmd_selftest`** (via
   `make_alcatraz_selftest_testcase(project_dir)`) — runs against the
   user's live project after `alcatrazer start --run-selftest`.

The mixin pattern (deliberately NOT a TestCase subclass) prevents
`unittest discover` from running the mixin standalone without
`setUpClass` populating `cls.prison` and `cls.expected`.

Assertions cluster:

| Cluster | Examples |
|---|---|
| **User identity** | runs as phantom UID, user is `agent` |
| **Host credential isolation** | no `.ssh/`, no `.gnupg/`, no `alcatraz` in global git config, no host signing-key paths |
| **Environment discipline** | only whitelisted secret-like env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MINIMAX_API_KEY`) |
| **Workspace git identity** | local user.name/email matches agent identity; no `alcatraz` in workspace git config; initial commit authored by agent |
| **Filesystem ownership** | `/workspace` owned by phantom UID |
| **Attack surface** | no `/var/run/docker.sock`, no git remotes in workspace |
| **Branding sweep** | no `alcatraz` string anywhere in env / git config / hostname / mountinfo (the last skipped in CI where the runner's host path itself contains "alcatrazer") |

All probes are `self.prison.query([...])` — read-only, no state change in
the workspace. Per [install_method.md](features/install_method.md) §
"Non-intrusiveness discipline for selftest".

## 6. State management — what's on disk

All Alcatrazer state lives under `<project>/.alcatrazer/` (gitignored). The
**workspace** lives in a sibling directory `<project>/.{word}-{XXXX}/`
(also gitignored via `.git/info/exclude`).

| Artifact | Lifetime | Writer | Reader |
|---|---|---|---|
| `Dockerfile` | regenerated on coding-env change | `DockerPrison.generate_prison` | `docker build`, `image_matches` |
| `entrypoint.sh` | copied from package | `DockerPrison.generate_prison` | docker container at startup |
| `config.toml` | per-developer, lifecycle = repo | `start.write_alcatrazer_config` (init) | daemon (`load_config`), `promote.resolve_identity` |
| `state.json` | shared CLI ↔ daemon hint | `state.update_state` (atomic via tmp + os.replace) | `daemon.main` (read once in finally) |
| `workspace-dir` | written at init | `identity.store_workspace_dir` | every subsequent command |
| `agent-identity` | written once, reused | `identity.ensure_identity` | workspace git config, selftest |
| `uid` | persisted phantom UID | `identity.ensure_phantom_uid` | `DockerPrison.build` |
| `promotion-daemon.pid` | daemon process lifetime | daemon at startup, removed at shutdown | `daemon_lifecycle._existing_daemon_pid` |
| `promotion-daemon.log` | persistent, rotated | daemon (`RotatingFileHandler`, 512 KB × 1 backup) | `shutdown_sync_daemon._parse_shutdown_log`; users via `tail -f` (hint surfaced by `alcatrazer status`) |
| `paused-branches.json` | conflict state | `promote.save_paused_branches` | daemon polling loop |
| `promoted-tips.json` | divergence detection | `promote.save_promoted_tips` | `detect_diverged_branches` |
| `promote-export-marks` | git mark file | `git fast-export --export-marks` | next `fast-export --import-marks` |
| `promote-import-marks` | git mark file | `git fast-import --export-marks` | next `fast-import --import-marks` |

**Schema versioning** (`state.py`): every `update_state` stamps
`schema_version: 1`. Per memory [Pre-1.0.0 breaking changes](../README.md),
incompatible schema bumps must refuse old workspaces with a helpful upgrade
message (and `[promotion-daemon].mode` legacy-key removal in
`change_promotion_machinery.md` Phase 2 is one such case).

## 7. Configuration — three TOML files, three jurisdictions

| File | Location | Version-controlled? | Schema |
|---|---|---|---|
| `coding-environment.toml` | repo root | yes | `[languages]`, `[os]`, `[startup]` — the agent's coding environment declaration (FR-30) |
| `.alcatrazer/config.toml` | `.alcatrazer/` | no (gitignored) | `[promotion]` (name/email override), `[promotion-daemon]` (interval, branches, mode, verbosity, max_log_size) |
| `pyproject.toml` | repo root | yes | Python project metadata (not Alcatrazer-runtime — this is the *developer*'s pyproject for *their* project; the Alcatrazer repo's own pyproject lives one level up in development) |

Three jurisdictions:

- **User-facing public** (`coding-environment.toml`) — what the agent's
  workspace needs. Zero Alcatrazer branding (Principle 2). Reviewable
  inside the workspace; agents see and can improve it.
- **Per-developer private** (`.alcatrazer/config.toml`) — developer
  identity for promotion, daemon tuning. Never enters version control.
- **Project's own** (`pyproject.toml`) — orthogonal to Alcatrazer.

## 8. Process model

At steady state with `alcatrazer start` running:

```
┌────────────────────────────────┐
│ CLI process (alcatrazer)       │   exits after `start` completes
│   ↳ argparse → cmd_start       │   (or stays around for `visit` —
│                                 │    `os.execvp("docker exec -it")`
└──────────┬─────────────────────┘    replaces this process)
           │ subprocess.Popen
           │ start_new_session=True
           ▼
┌────────────────────────────────┐   long-lived
│ Promotion daemon process       │   polls every {interval}s
│   ↳ alcatrazer.daemon.main     │   logs to .alcatrazer/promotion-daemon.log
└──────────┬─────────────────────┘
           │ git fast-export / fast-import (subprocess per cycle)
           │ docker (subprocess for adapter ops — invoked from cmd_*)
           ▼
┌────────────────────────────────┐   long-lived
│ Sandbox container              │   `docker run -d ... sleep infinity`
│   ↳ /workspace bind-mounted    │   exits on `docker stop` (CLI invokes
│   ↳ agent user (phantom UID)   │    via `alcatraz.stop()`)
└────────────────────────────────┘
```

The user can launch additional CLI processes (`alcatrazer visit`,
`alcatrazer status` once shipped) against the same daemon + container.
The PID-file guard ensures only one daemon per project; identity-hash on
container name ensures different projects don't collide.

## 9. CLI surface — current vs. designed

Per the PRD edit that just landed, FR-21 enumerates **seven** top-level
commands. Six are shipped today; one (`status`) is designed in
`change_promotion_machinery.md` but not yet implemented.

| Command | Status | Function (in `start.py`) | What it does |
|---|---|---|---|
| `init` | shipped | `cmd_init` | One-time interactive wizard — writes `coding-environment.toml`, `.alcatrazer/config.toml`, `.env.example`, `.git/info/exclude`, generates agent identity + workspace dir name |
| `start` | shipped | `cmd_start` | Build (if needed) → run container → snapshot workspace (if absent) → launch daemon. `--run-selftest` runs FR-25 invariants against the live sandbox before returning |
| `visit` | shipped | `cmd_visit` | `os.execvp("docker exec -it ... bash")` — replaces the CLI process |
| `stop` | shipped | `cmd_stop` | Cooperative daemon shutdown (4-step) → `docker stop` |
| `clear` | shipped | `cmd_clear` | Cooperative daemon shutdown → `docker rm -f` (image kept) |
| `test` | shipped | `run_tests` (in `cli.py`) | `unittest.TestLoader().discover()` against `tests/` (+`integration_tests/` when `--smoke`) |
| `status` | **designed, not implemented** | (planned) | Per `change_promotion_machinery.md`: read `.alcatrazer/state.json` + daemon PID + pending-commit count, render three steady states (active/held/blocked) |

The `test → verify` rename agreed in the PRD edit is **not yet
propagated to code**. Code still ships `test`. Per
[memory](../README.md) `[Doc-first workflow]` — PRD is updated first;
code follows as a separate TDD cycle.

## 10. The "infocenter" trajectory — where `state.json` is heading

[`state.py`](../src/alcatrazer/state.py) module docstring explicitly calls
out: today's one-flag `state.json` is the **seed of the future infocenter
layer** ([refactor_for_infocenter.md](features/refactor_for_infocenter.md)).
The current API is intentionally minimal (`load_state`, `update_state`) so
that adding fields is purely additive — new fields merge in without
touching file lifecycle logic.

The `status` command's data model will likely flow through this layer:
last-promoted timestamp, last-promoted commit hash, `paused` reason,
pending-commit count. The state.json design is forward-compatible by
construction; adding a JSON field never breaks readers.

## 11. Test taxonomy

```
src/alcatrazer/
├── tests/                  # Unit tests — stdlib unittest, no external deps
│   └── test_*.py           # Per-module; collocated with code
├── integration_tests/      # Integration tests — require Docker
│   ├── test_smoke.py       # End-to-end Alcatraz lifecycle + selftest mixin
│   └── test_*.py           # Other docker-requiring scenarios
└── selftest.py             # FR-25 read-only invariants, runnable inside
                            # the user's live Alcatraz via `start --run-selftest`
                            # — shared with integration test_smoke
```

Three execution modes:

- `alcatrazer test` → discovers and runs `tests/` only (fast, no Docker)
- `alcatrazer test --smoke` → adds `integration_tests/` (requires Docker)
- `alcatrazer start --run-selftest` → builds a live-Alcatraz-bound
  TestCase from `selftest.py` and runs it against the user's actual
  running sandbox (the FR-25 user-machine verification)

All three use **stdlib `unittest`** — runtime trust surface constraint
(see [design_principles.md](design_principles.md) §"Stdlib Only —
Three trust boundaries").

## 12. Trust surface & dependency posture

Per FR-28 / NFR-6 / [design_principles.md §"Stdlib Only"](design_principles.md):

- **Runtime** (`src/alcatrazer/**`): stdlib only. No `import` of any
  third-party package, anywhere — production or tests. Verifiable via
  `grep -r "^import\|^from" src/alcatrazer/ | grep -v "from alcatrazer\|^.*: import \(abc\|argparse\|contextlib\|dataclasses\|fnmatch\|hashlib\|json\|logging\|os\|random\|re\|shutil\|signal\|subprocess\|sys\|threading\|time\|tomllib\|unittest\)"`.
- **Dev tooling** (in `[dependency-groups] dev` of pyproject.toml): `ruff`,
  `build`, `twine`. Third-party, acceptable — never runs on user
  machine.
- **Host OS** (assumed ambient per NFR-9): `bash`, `git`, Docker,
  `getent`, `tar`, `gosu` (installed in container build, ambient via
  apt).

The audit surface a user must read to trust Alcatrazer is therefore:
Python stdlib + everything under `src/alcatrazer/**`. The
README's "Trust & Verification" section walks through how a user runs
`sha256sum -c` against a GitHub-hosted `SHA256SUMS` to verify their
installed source against an independent channel.

## 13. Known seams and refactor targets

These are **the system-level seams** an agent picking up future work
should know about:

### 13.1 `start.py` is a 1,492-line hub

It owns: every CLI command (`cmd_init`, `cmd_start`, …), the interactive
wizards (`ask_languages`, `ask_os_packages`, `ask_startup_commands`,
`ask_promotion_identity`), the TOML renderers (`_render_coding_
environment`), the workspace-prep flow (`create_workspace`), and the
state-change detectors (`coding_environment_changed`, `env_file_changed`).
That's a lot for one module. A future refactor could split into
`commands.py` (cmd_*), `wizards.py` (ask_*), `toml_io.py` (read/write
TOML), `workspace_lifecycle.py` (create_workspace + extract_package_source
+ write_python_symlink). Not blocking; just a smell.

### 13.2 Promotion daemon's `change_promotion_machinery.md` refactor

Three planned changes (per the feature doc):

- **`alcatraz-tree` mode goes away.** The new design relies on mirror
  mode only; the namespace-prefix path was a workaround for a deeper
  apply-patch design that's being replaced (the next refactor changes
  the daemon's transport from `fast-export | fast-import` to `format-
  patch | am`).
- **`schema_version` becomes a real gate.** Today it's stamped but never
  read; the refactor adds `init`/`start`/`status` refusing on
  `schema_version < 2` (or absent, or with legacy `[promotion-daemon].
  mode` present).
- **`alcatrazer status`** ships, reading state.json + daemon liveness +
  promote.count_unpromoted_commits.

The seams the refactor will touch:
- `promote.py` (transport change from fast-export → format-patch/am)
- `daemon.py` (mode handling simplified to one path)
- `state.py` (infocenter expansion: `last_promoted_at`, `paused` reason,
  `commit_count_pending`)
- `start.py` (cmd_status added; init/start schema_version gate)
- `cli.py` (status subcommand registered)

### 13.3 Backend abstraction — alcatraz.py ↔ docker_prison.py

Solid for swapping to another subprocess-based backend (Podman, Sysbox).
For a fundamentally different model (FirecrackerVM, Kata, gVisor
sandboxes managed via API rather than CLI subprocess), the `query` /
`exec` contract and the Dockerfile generator would need rethinking. Not
on the immediate roadmap (M5 in PRD §10, post-1.0).

### 13.4 Multiple-AI-CLI support

The `_DOCKERFILE_AI_BASE` stage 2 is hardcoded for Claude Code. The
docstring says "A future step will generate this stage from an [ai]
section of coding-environment.toml so a repo can pick Claude, another
agent, or none." When that ships, `languages.py` will likely grow an
`SUPPORTED_AI_AGENTS` dict mirroring `SUPPORTED_LANGUAGES`.

## 14. Quick reference — where to read what

| Concern | Read |
|---|---|
| Why Alcatrazer exists | [PRD §2](prds/PRD-001-alcatrazer.md) + [threat-model research](prds/PRD-001-alcatrazer-threat-model-research.md) |
| Architectural principles | [design_principles.md](design_principles.md) |
| Sandbox port contract | `src/alcatrazer/alcatraz.py` |
| Docker adapter mechanics | `src/alcatrazer/docker_prison.py` |
| Promotion algorithm | `src/alcatrazer/promote.py` |
| Daemon orchestration | `src/alcatrazer/daemon.py` + `daemon_lifecycle.py` |
| Stealth + identity | `src/alcatrazer/identity.py` + `snapshot.py` |
| Security invariants | `src/alcatrazer/selftest.py` |
| CLI ↔ daemon shared state | `src/alcatrazer/state.py` |
| Promotion machinery refactor design | [`features/change_promotion_machinery.md`](features/change_promotion_machinery.md) |
| Language convenience layer | `src/alcatrazer/languages.py` |
| User-facing flow | [`README.md`](../README.md) §Getting Started |
| Three-stage Dockerfile design | [`features/install_method.md`](features/install_method.md) §Hexagonal sandboxing architecture |
| No-pollution rules | [`features/alcatraz_how_and_what_for.md`](features/alcatraz_how_and_what_for.md) |
| Stealth invariants | [`features/no_sign_they_work_in_alcatraz.md`](features/no_sign_they_work_in_alcatraz.md) |

---

*Generated 2026-05-11 by `bmad-document-project` (deep scan). The shipped
code is the source of truth — when this document and `src/alcatrazer/` say
different things, the code is right. Update this document as the system
evolves; treat divergence as a code-review smell.*