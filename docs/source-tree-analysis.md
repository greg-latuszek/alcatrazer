---
title: "Alcatrazer — Annotated Source Tree"
purpose: "Module-by-module map of the codebase. Companion to architecture.md."
date: 2026-05-11
status: brownfield-documented
generated_by: bmad-document-project (deep scan)
---

# Annotated Source Tree

For the *what each module does in the larger system* see
[`architecture.md`](architecture.md). This file is the **map**: every
file in the runtime trust surface (`src/alcatrazer/**`) plus the
top-level project artifacts, with one-line roles.

---

## Top-level

```
agents_in_sandbox/                      # repo root
├── pyproject.toml                      # Python project metadata; build = hatchling,
│                                       # dev-deps (ruff, build, twine), ruff config
├── uv.lock                             # uv-managed dev-dependency lockfile
├── mise.toml                           # Tool versioning (Python, ruff, …)
├── alcatrazer.toml                     # Project-level Alcatrazer config (legacy/dev?)
├── README.md                           # User-facing entry point (39 KB)
├── CHANGELOG.md                        # Release history
├── CLAUDE.md                           # Project rules for AI agent contributors
├── LICENSE                             # Apache-2.0
├── .env / .env.example                 # Developer secrets + template (gitignored)
├── .gitignore                          # Tracked ignore rules
├── images/                             # README/marketing imagery
├── dist/                               # Build artifacts (gitignored)
├── .github/workflows/
│   ├── ci.yml                          # PR + push CI — lint + unit tests
│   ├── smoke.yml                       # Docker integration tests
│   └── release.yml                     # PyPI release (twine) + SHA256SUMS to GitHub Release
├── src/alcatrazer/                     # The Python package — runtime trust surface
└── docs/                               # The project's own docs
```

## `src/alcatrazer/` — runtime modules

All files here are part of the **runtime trust surface** (stdlib only,
audited by users).

```
src/alcatrazer/
├── __init__.py                  # Version string only ("0.1.0")
├── cli.py                       # argparse entry point; 6 shipped subcommands
│                                # → delegates to start.cmd_*
├── start.py            (1492L)  # LIFECYCLE HUB — every cmd_*, every wizard,
│                                # every file generator, every state-change
│                                # detector. The "spine" of the CLI side.
├── alcatraz.py          (182L)  # SANDBOX PORT — Alcatraz(ABC) with 16
│                                # abstract methods; PrisonError hierarchy.
│                                # Backend-neutral by design.
├── docker_prison.py     (626L)  # DOCKER ADAPTER — the only Alcatraz impl.
│                                # Shells out to `docker`. Owns three-stage
│                                # Dockerfile generation + alcatrazer.config_hash
│                                # LABEL + per-repo identity (image tag,
│                                # container name) from path hash.
├── daemon.py            (309L)  # PROMOTION DAEMON main loop. Standalone
│                                # process spawned by `start`. Polls
│                                # workspace every {interval}s, runs
│                                # mirror or alcatraz-tree mode, logs to
│                                # rotating file, cooperative shutdown via
│                                # state.json + SIGTERM.
├── daemon_lifecycle.py  (394L)  # CLI-side daemon orchestration:
│                                # launch_sync_daemon (+ self-heal),
│                                # shutdown_sync_daemon (4-step protocol),
│                                # print_launch_info / print_shutdown_result
│                                # for user-facing transparency blocks.
├── promote.py           (506L)  # PROMOTION ALGORITHM. fast-export |
│                                # rewrite_identity | rewrite_refs (optional)
│                                # | fast-import. Conflict detection via
│                                # promoted-tips.json. Mirror-mode conflict
│                                # branch creation. Three-layer identity
│                                # resolution (git config < toml < CLI flags).
├── identity.py          (332L)  # Random agent identity (FR-10) — 50 first
│                                # names × 50 last names × 20 domains × 3
│                                # local-part patterns. Workspace dir name
│                                # generator (.{word}-{4hex}). Phantom-UID
│                                # detection via getent passwd/group.
├── snapshot.py          (236L)  # FR-7 starting-branch snapshot. Detects
│                                # default branch (origin/HEAD →
│                                # main/master), `git archive | tar`, filters
│                                # `.alcatrazer/` out of snapshotted
│                                # .gitignore, creates initial commit.
├── selftest.py          (254L)  # FR-25 security invariants. Mixin (NOT a
│                                # TestCase subclass — discovery-safety).
│                                # 13 read-only `prison.query` probes:
│                                # phantom UID, no .ssh/.gnupg, no signing
│                                # keys, no docker socket, workspace
│                                # identity, branding sweep.
├── languages.py         (167L)  # SUPPORTED_LANGUAGES whitelist (python,
│                                # node, rust, go, dotnet, java) — for the
│                                # wizard and the Dockerfile generator.
│                                # AQUA_ATTESTATION_MISALIGNED frozenset
│                                # (the uv workaround).
├── state.py              (70L)  # Shared CLI ↔ daemon state.json
│                                # (atomic write via tmp + os.replace).
│                                # Currently 1 flag (daemon_shutdown).
│                                # Seed of future "infocenter" layer.
├── inspect.py            (69L)  # Standalone log viewer for the daemon
│                                # log. NOT the designed `status` command —
│                                # just a tail -f. Run via
│                                # `python -m alcatrazer.inspect`.
├── container/
│   └── entrypoint.sh            # Fixed shell script: root → chown
│                                # /workspace to phantom UID → exec gosu
│                                # agent. Copied verbatim into
│                                # .alcatrazer/ by DockerPrison.generate_prison.
├── scripts/                     # Bash bootstrap (resolve_python.sh,
│                                # initialize_alcatraz.sh) per
│                                # `as_little_bash_as_necessary.md`.
├── templates/                   # Coding-environment.toml + .env.example
│                                # templates (consumed by start.py wizards).
├── tests/                       # Unit tests — stdlib unittest, no Docker.
│   └── test_*.py                # Discovered by `alcatrazer test`.
└── integration_tests/           # Integration tests — requires Docker.
    ├── test_smoke.py            # End-to-end Alcatraz lifecycle +
    │                            # _AlcatrazSecurityInvariants mixin.
    └── test_*.py                # Other docker-requiring scenarios.
                                 # Run via `alcatrazer test --smoke`.
```

## `docs/` — project documentation

```
docs/
├── architecture.md              # System-level synthesis (this session)
├── source-tree-analysis.md      # ← you are here
├── index.md                     # Master entry point for AI sessions
├── design_principles.md         # The architectural ethics — Principle 1
│                                # (security first), Principle 2 (zero
│                                # alcatraz footprint inside), trust
│                                # boundaries, no-pollution, identity,
│                                # snapshot rules, promotion, code
│                                # organization. Authoritative.
├── licence_change_reasoning.md  # Why Apache-2.0 (post v0.0.x license
│                                # journey)
├── license_dependencies_and_usage.md
│
├── prds/
│   ├── PRD-001-alcatrazer.md    # Main PRD (v2, 14 sections, FR-1..FR-34
│   │                            # + NFR-1..NFR-9). The vision-level doc.
│   ├── PRD-001-alcatrazer-validation-report.md
│   │                            # Party-mode roundtable findings on the
│   │                            # main PRD (Mary/John/Winston/Amelia).
│   │                            # Effectively discharged by v2.
│   ├── PRD-001-alcatrazer-threat-model-research.md
│   │                            # Verifiable incidents + CVEs supporting
│   │                            # PRD §2 (A: exfiltration, B: supply
│   │                            # chain, C: distribution channels).
│   ├── PRD-001-alcatrazer-docker-escape-research.md
│   │                            # Docker escape research note.
│   ├── prd_changelog.md         # PRD revision log.
│   └── _template.md             # PRD template.
│
├── features/                    # Feature-level designs. Each is the
│                                # design + decisions + state machine for
│                                # one feature. Read alongside the PRD
│                                # when working on that feature.
│   ├── alcatraz_how_and_what_for.md
│   │                            # What .alcatrazer/ contains and why;
│   │                            # config split rationale.
│   ├── as_little_bash_as_necessary.md
│   │                            # Bash-vs-Python boundary; what stays
│   │                            # bash (resolve_python, entrypoint).
│   ├── auto-promotion-daemon.md # Original promotion daemon design
│   │                            # (superseded for some parts by
│   │                            # change_promotion_machinery.md).
│   ├── change_promotion_machinery.md
│   │                            # ★ THE REFACTOR TARGET. New design
│   │                            # using format-patch | am instead of
│   │                            # fast-export | fast-import; adds
│   │                            # `status` command; schema_version=2
│   │                            # gate; alcatraz-tree mode removal.
│   ├── install_method.md        # Install architecture — pipx/uvx/curl,
│   │                            # SHA256SUMS, per-repo install, hexagonal
│   │                            # sandboxing.
│   ├── more_languages_support.md
│   │                            # Convenience-layer expansion (.NET,
│   │                            # Java) + the aqua-attestation
│   │                            # workaround.
│   ├── no_sign_they_work_in_alcatraz.md
│   │                            # Stealth design — what must NOT be
│   │                            # visible inside the workspace.
│   ├── prepare_ci.md            # CI workflow design.
│   ├── refactor_for_infocenter.md
│   │                            # Future "infocenter" layer for the
│   │                            # `status` command — state.json is the
│   │                            # seed.
│   └── start_from_existing_repo.md
│                                # Snapshot extraction design (FR-7).
│
├── research/                    # (empty)
│
└── tests/
    └── test_more_languages_support.md
                                 # Test plan for the more_languages_support
                                 # feature.
```

## Module dependency graph

ASCII summary of who imports whom inside `src/alcatrazer/`:

```
cli.py
  └─▶ start.py (everything)

start.py
  ├─▶ alcatraz.py            (type hints, prison protocol)
  ├─▶ docker_prison.py       (DockerPrison default)
  ├─▶ daemon_lifecycle.py    (launch / shutdown)
  ├─▶ snapshot.py            (create_workspace)
  ├─▶ identity.py            (ensure_identity, workspace_dir)
  ├─▶ selftest.py            (cmd_selftest)
  ├─▶ languages.py           (wizards + Dockerfile gen)
  └─▶ state.py               (daemon_shutdown flag, env_file change detect)

daemon.py
  ├─▶ identity.py            (workspace dir lookup)
  ├─▶ promote (as promote_mod)
  └─▶ state.py               (read daemon_shutdown intent in finally)

daemon_lifecycle.py
  ├─▶ daemon.py              (DEFAULTS, load_config)
  ├─▶ identity.py            (workspace dir display)
  └─▶ state.py               (update_state on shutdown protocol)

docker_prison.py
  ├─▶ alcatraz.py            (Alcatraz base, exceptions)
  ├─▶ identity.py            (phantom UID, workspace dir)
  └─▶ languages.py           (SUPPORTED_LANGUAGES, AQUA_ATTESTATION_MISALIGNED)

promote.py                   (standalone — no alcatrazer imports;
                              just stdlib + subprocess git)

selftest.py
  ├─▶ alcatraz.py            (Alcatraz type)
  └─▶ docker_prison.py       (in factory, lazy import)

snapshot.py                  (standalone — no alcatrazer imports)
identity.py                  (standalone — no alcatrazer imports)
state.py                     (standalone — no alcatrazer imports)
inspect.py                   (standalone — no alcatrazer imports)
languages.py                 (standalone — data only, no imports)
alcatraz.py                  (standalone — abstract base only)
```

**Observations:**

- `start.py` is the **only** module that imports broadly across the
  package — it's the orchestration hub.
- The promotion subsystem (`promote.py`, `daemon.py`, `daemon_lifecycle.py`)
  has well-defined seams: `promote.py` is pure algorithm (no alcatrazer
  imports), `daemon.py` is the polling-loop host, `daemon_lifecycle.py`
  is the CLI-side bridge.
- `alcatraz.py` and `docker_prison.py` form the hexagonal seam — only
  `selftest.py` and `start.py` import the concrete `DockerPrison`; other
  consumers take the abstract `Alcatraz` type.
- `languages.py`, `identity.py`, `snapshot.py`, `state.py`, `inspect.py`
  are leaf modules (pure functions / data). Easy to test in isolation;
  unlikely to need refactoring.

## Where each FR / NFR is implemented

| Requirement | Implementation |
|---|---|
| FR-1 (sealed sandbox) | `docker_prison.start()` — phantom UID, no host mounts beyond `/workspace` and read-only Claude creds |
| FR-2 (narrow credential channel) | `docker_prison.start()` mounts `~/.claude/.credentials.json:ro` only |
| FR-3 (outbound network) | Docker default network — no explicit restriction |
| FR-4 (no host privileges on escape) | Phantom UID (`identity.detect_phantom_uid`) |
| FR-5 (declarative isolation) | `_render_dockerfile_body(coding_environment)` |
| FR-6 (per-concept backend contract) | `Alcatraz(ABC)` in `alcatraz.py` — 16 abstract methods |
| FR-7 (snapshot, no history) | `snapshot.snapshot_workspace` |
| FR-8 (workspace sealed, no remotes) | `snapshot.create_initial_commit` + selftest invariant `test_workspace_has_no_git_remotes` |
| FR-9 (no alcatraz strings observable) | Workspace dir generator + selftest branding sweep |
| FR-10 (agent identity) | `identity.generate_identity` |
| FR-11 (zero footprint) | `selftest._AlcatrazSecurityInvariants` |
| FR-12 (machinery outside) | `daemon.py` runs on host; never writes into workspace |
| FR-13 (starting branch binding) | `snapshot.detect_default_branch` |
| FR-14 (append, never rewrite) | `promote.promote` uses fast-import on top of current tip |
| FR-15 (developer authorship) | `promote.rewrite_identity` |
| FR-16 (bounded latency) | `daemon.main()` polling loop, default interval 5s |
| FR-17 (hold on conflict) | `promote.promote_with_conflict_handling` |
| FR-18 (auto-resume) | `promote.check_resolved_conflicts` |
| FR-19 (explicit decision on pending) | `start.cmd_clear` (lines 1278+) handles held work |
| FR-20 (inspect at any moment) | `state.py` + `inspect.py` + future `status` command |
| FR-21 (CLI surface, 7 commands) | `cli.py` subparsers (6 shipped + `status` designed) |
| FR-22 (interactive setup) | `start.cmd_init` + `ask_*` wizard functions |
| FR-23 (self-correcting bring-up) | `start.cmd_start` + `DockerPrison.needs_rebuild` + `image_matches` |
| FR-24 (idempotent suspend/reset) | `daemon_lifecycle.shutdown_sync_daemon` + final sync |
| FR-25 (verification suite) | `selftest._AlcatrazSecurityInvariants` |
| FR-26 (readable source) | Per-repo install model; no obfuscation |
| FR-27 (independent checksums) | `.github/workflows/release.yml` SHA256SUMS upload |
| FR-28 (no third-party runtime deps) | Audit: `grep -r "^import\|^from" src/alcatrazer/` |
| FR-29 (toolchain-agnostic core) | Sandbox is general — convenience layer is additive |
| FR-30 (declaration file) | `coding-environment.toml` + `start.write_coding_environment_toml` |
| FR-31 (schema_version refuse) | Designed in `change_promotion_machinery.md` Phase 2 (`state.SCHEMA_VERSION` ready) |
| FR-32 (built-in templates) | `languages.SUPPORTED_LANGUAGES` |
| FR-33 (extensible without redesign) | Data-driven `SUPPORTED_LANGUAGES` dict |
| FR-34 (deterministic package merge) | `docker_prison._dedupe_preserve_order` |
| NFR-1 (2-artifact footprint) | `coding-environment.toml` + `.env.example` are the only committed artifacts |
| NFR-2 (no system install) | Per-repo `.alcatrazer/src/alcatrazer/` |
| NFR-3 (concurrent coexistence) | `_identity_for_project` path-hashed image tag + container name |
| NFR-4 (latency target) | `daemon.main()` 5s default × ≤ 2 cycles = ≤ 10s |
| NFR-5 (stealth) | `selftest` branding sweep |
| NFR-6 (auditable surface) | Stdlib-only + per-repo install |
| NFR-7 (backend-agnostic) | `alcatraz.py` ABC contract |
| NFR-8 (universality) | Sandbox is language-neutral; convenience layer additive |
| NFR-9 (Linux + macOS) | `.github/workflows/ci.yml` matrix; phantom-UID logic POSIX-portable |

---

*Generated 2026-05-11 by `bmad-document-project` (deep scan). Line
counts as of this generation; will drift as code evolves. When the
docstrings in `src/alcatrazer/**` and this map disagree, the docstrings
win.*