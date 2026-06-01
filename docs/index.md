---
title: "Alcatrazer — Documentation Index"
purpose: "Primary entry point for AI-assisted coding sessions. Load this first."
date: 2026-05-11
status: brownfield-indexed
---

# Documentation Index

> **AI agents start here.** Load this file at the beginning of a session;
> it will tell you exactly which other docs to load for the task at hand,
> and which invariants you must hold.

---

## Project at a glance

- **Name:** Alcatrazer — secure AI agent workspace
- **One-liner:** Sandbox for AI coding agents; their code gets out, your secrets don't.
- **Status:** v0.1.0 (alpha, pre-1.0)
- **Language:** Python ≥ 3.11 (stdlib only — see invariants below)
- **Repository type:** Monolith, single Python package
- **Build:** hatchling | **Dev tools:** ruff, build, twine | **Tests:** stdlib `unittest`

## Load order for a fresh AI session

Read these in order. Stop when you have enough for the task.

```
1. THIS FILE (index.md)               — what's where, invariants, conventions
2. docs/design_principles.md          — architectural ethics — load always
3. docs/coding_conventions.md         — implementation rules (regex commenting, …) — load for any code change
4. docs/prds/PRD-001-alcatrazer.md    — vision + requirements — load for product work
5. docs/architecture.md               — system synthesis — load for any non-trivial code task
6. docs/source-tree-analysis.md       — module map — load when navigating unfamiliar code
7. docs/features/<topic>.md           — feature-level design — load for that feature's work
```

**Don't load the whole PRD on a tiny task.** A typo fix or one-line CLI
help-text tweak doesn't need any of these. Reach for the load order when
you're about to make a non-trivial change.

## Curated load lists by task type

| Task type | Load |
|---|---|
| **Promotion-machinery refactor** | this + design_principles + architecture §4 + `features/change_promotion_machinery.md` + `src/alcatrazer/{daemon,daemon_lifecycle,promote,state}.py` |
| **New language in convenience layer** | this + design_principles + `features/more_languages_support.md` + `src/alcatrazer/languages.py` + `docker_prison._render_mise_uses` |
| **Backend abstraction work** (Podman, Sysbox, …) | this + architecture §3 + `src/alcatrazer/{alcatraz,docker_prison}.py` + `features/install_method.md` §Hexagonal sandboxing |
| **Stealth / identity changes** | this + design_principles Principle 2 + `features/no_sign_they_work_in_alcatraz.md` + `src/alcatrazer/{identity,snapshot,selftest}.py` |
| **New CLI command** | this + PRD FR-21 + architecture §9 + `src/alcatrazer/cli.py` + `start.py` (find a sibling `cmd_*`) |
| **CI / release work** | this + `features/install_method.md` + `.github/workflows/*.yml` |
| **PRD / documentation work** | this + `docs/prds/PRD-001-alcatrazer.md` + `docs/prds/prd_changelog.md` + `docs/design_principles.md` |
| **Security-invariant addition** | this + `src/alcatrazer/selftest.py` + design_principles "Security Claims Must Be Verifiable" |

## Invariants — what every coding agent must hold

These are **non-negotiable** for any code change in `src/alcatrazer/**`:

1. **Stdlib only.** No `import` of any third-party package, anywhere in
   `src/alcatrazer/**` — production code or tests. The trust surface is
   the user's audit surface. (Dev tools like ruff/build/twine are
   third-party and live in `[dependency-groups] dev` — that's separate.)
   See [`design_principles.md` §"Stdlib Only — Three trust boundaries"](design_principles.md).

2. **Zero alcatraz/alcatrazer/prison/inmate/warden strings inside the
   sandbox** (Principle 2). Selftest's branding sweep enforces this.
   When naming variables, files, env vars, mount paths, container/image
   names that an agent inside the sandbox could observe: do not include
   those substrings. Use "agent", "workspace", neutral terms.

3. **CLI vocabulary is backend-neutral.** Command names are plain English,
   not docker/compose/k8s jargon (`start` not `up`, `visit` not
   `attach`, `stop` not `suspend`, `clear` not `reset`, `verify` not
   `test`). The shipped CLI predates this rule for most commands; future
   commands follow it.

4. **Never silently rename existing identifiers** when editing PRD or
   docs. The PRD-recreation in v2 hit this — FR-21 had renamed five
   shipped commands without flagging the divergence. If a doc disagrees
   with shipped code, ask before changing either side.

5. **No `sudo`-equivalent inside Alcatraz.** Agent user is non-root, no
   `gosu`/`sudo`/`su` available at runtime. Root operations happen in
   image build only.

6. **Preserve user-declared order** when merging lists (apt packages,
   languages). Never `sorted(set(...))` — that loses user intent and can
   break order-sensitive package managers. Use `_dedupe_preserve_order`
   in `docker_prison.py` as the pattern.

7. **Bundled tests use `unittest`, not pytest.** They ship to the user
   and run via `alcatrazer verify` (currently `test`) — runtime trust
   surface.

## Working with the codebase

### Repo layout

```
agents_in_sandbox/
├── src/alcatrazer/      ← runtime code (stdlib only)
├── docs/                ← this folder
├── pyproject.toml       ← build config, dev-deps, ruff config
├── mise.toml            ← tool versioning
├── .github/workflows/   ← CI / release
└── README.md            ← user-facing
```

### Local dev loop (what's missing from the README, captured for AI sessions)

The README is user-facing — it explains `alcatrazer init` etc. for
end-users. For a *contributor* dev loop:

```bash
# Set up tooling
mise install                          # Python + tooling at pinned versions
mise env-create

# Lint
mise format

# Run tests
mise test
mise test-fast

# With Docker integration tests:
mise test-smoke

# Build a wheel
mise build
```

### CI gates

`.github/workflows/ci.yml` runs on push to branch: lint + unit tests.
`.github/workflows/smoke.yml` runs integration tests under Docker, runs on PR merge to main.
`.github/workflows/release.yml` runs on tag push: PyPI upload via twine +
`SHA256SUMS` published as a GitHub Release asset (per FR-27).

### Where to put new tests

- **Pure logic, no Docker needed** → `src/alcatrazer/tests/test_<module>.py`.
  Will be run by `alcatrazer test` and by CI.
- **Requires a running Alcatraz** → `src/alcatrazer/integration_tests/`.
  Run by `alcatrazer test --smoke` and by the smoke workflow.
- **A new security invariant the user runs against their own machine** →
  add to `_AlcatrazSecurityInvariants` in `src/alcatrazer/selftest.py`.
  This is what `alcatrazer verify` (currently `test`) executes; also
  picked up by `start --run-selftest`.

## Conventions captured from prior sessions

These are codified preferences from the project's collaboration history.
When in doubt, follow them; if you want to deviate, ask.

- **Doc-first workflow.** Update design doc (PRD, feature doc, this
  doc) FIRST. User commits the doc change. THEN start TDD on the code.
  Don't bundle doc + code in one commit.
- **No `git push` from AI agents.** Commit locally; ask the user to
  push manually.
- **`git mv` for renames.** Preserves history.
- **TDD commit prefixes.** `[RED]` (failing test added), `[GREEN]` (code
  makes test pass), `[BLUE]` (refactor). Each is its own commit.
- **Run `mise format` after editing Python.** CI enforces.
- **Review changes via Edit tool, file by file.** No blind `sed`.
- **Pre-1.0 breaking changes** bump `schema_version` and refuse old
  workspaces with a helpful message. Don't build migration tooling
  pre-release.

## Open architectural threads

These are the known seams an agent picking up the codebase should
understand. Detail in [`architecture.md` §13](architecture.md):

- **`start.py` is 1,492 lines** — split candidate (commands /
  wizards / TOML I/O / workspace_lifecycle).
- **Promotion machinery refactor** — per
  [`features/change_promotion_machinery.md`](features/change_promotion_machinery.md):
  fast-export → format-patch/am; alcatraz-tree mode removed;
  schema_version=2 gate; `status` command ships.
- **Backend abstraction stability** for a non-Docker backend (Podman
  trivial; VM-based requires rethinking `query`/`exec` contract).
- **Multiple-AI-CLI support** — `_DOCKERFILE_AI_BASE` is currently
  hardcoded for Claude Code. Future `[ai]` section in
  `coding-environment.toml`.
- **`status` command** is designed but not implemented (priority).
- **`test → verify` CLI rename** is in PRD-001 v2 but not yet in code.

## What's deliberately NOT in this folder

The BMad `bmad-document-project` workflow normally generates a fuller
documentation set. This project intentionally generated only the three
artifacts that fill genuine gaps:

| BMad-suggested artifact | Why not generated |
|---|---|
| `project-overview.md` | Already covered by [README.md](../README.md) + [PRD-001 §1](prds/PRD-001-alcatrazer.md) |
| `development-guide.md` | Partial coverage in README + [CLAUDE.md](../CLAUDE.md) + `mise.toml` + `pyproject.toml`. Contributor count is small; not yet worth a separate doc. Captured inline in this index under "Local dev loop" instead. |
| `deployment-guide.md` | Covered in [`features/install_method.md`](features/install_method.md) + `.github/workflows/release.yml` |
| `api-contracts.md`, `data-models.md`, `component-inventory.md` | Not applicable (CLI project type) |

If a future contributor needs a standalone development-guide, regenerate
via `bmad-document-project` with `development-guide.md` enabled.

---

*Generated 2026-05-11 by `bmad-document-project` (deep scan, surgical
artifact set). When this index disagrees with the code, the code wins —
update this file rather than the code.*