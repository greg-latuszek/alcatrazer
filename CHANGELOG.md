# Changelog

All notable changes to **Alcatrazer** are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(currently in the `0.y.z` development phase, where breaking changes can land in any release).

---

## [0.0.4] — 2026-04-29

> ## ⚠️ DO NOT USE THIS RELEASE FOR REAL WORK
>
> **A serious bug in the promotion machinery was uncovered during release-readiness testing for 0.0.4.** It is **not fixed in this release**.
>
> When the promotion daemon syncs commits from inside Alcatraz back to your outer repo, the default `mirror` mode **rewrites your outer branch's history** and **leaves your working tree out of sync with `HEAD`**. Your original commits on `main` may stop being ancestors of the new tip; `git status` will show phantom "deleted" entries; and there is no automatic recovery path inside the tool.
>
> The problem is structural — it stems from using `git fast-export | git fast-import` for a job that should be `git format-patch | git am`. A full design and fix is documented in [`docs/features/change_promotion_machinery.md`](docs/features/change_promotion_machinery.md) and will ship as **0.0.5**.
>
> **What to do:**
>
> - **End users:** wait for **0.0.5**. Do not run `alcatrazer start` against any repository whose history you care about with this version.
> - **Contributors / reviewers:** 0.0.4 is published to surface the language-onboarding work for review and to keep that change cleanly separated from the promotion rewrite. The published wheel is intentionally limited in usefulness until 0.0.5 lands.

The 0.0.4 release otherwise lands **Phase 1 of the broaden-language-support effort** —
the wizard, image, and language whitelist all became more capable, and several latent
bugs in image management surfaced and were fixed along the way. Full design rationale
in [`docs/features/more_languages_support.md`](docs/features/more_languages_support.md).

### Added

- **Schema versioning for `coding-environment.toml`.** New top-level
  `schema_version = 1` field. Loader accepts missing field as version 1 for
  backwards compatibility; refuses unknown versions with a clean
  `ERROR:` message instead of a Python traceback.
- **C# / .NET support.** New `dotnet` entry in `SUPPORTED_LANGUAGES`
  with `default_manager = "dotnet"`. Includes the new
  `required_os_packages = ("libicu74",)` field, baked into the
  Dockerfile's apt-install at build time so `dotnet --version` works
  out of the box without runtime privilege escalation. Wizard suggests
  `10.0.100` (.NET 10 LTS).
- **Java support.** New `java` entry with
  `default_manager = "maven"`, `managers = ("maven", "gradle")`,
  `version_check = "java -version 2>&1"`, and
  `required_os_packages = ()` (JDK is self-contained on Ubuntu 24.04
  base). Wizard suggests `21` (Java 21 LTS) and surfaces the
  Temurin / Corretto / Zulu / GraalVM distribution-prefix syntax in
  the new "Java distributions" README subsection.
- **Wizard self-explanation.** `alcatrazer init` now prints an intro
  panel with an ASCII diagram of the promotion model, section
  banners (`=== Promotion identity ===`, `=== Languages ===`, …)
  with 2–4 lines of context before each prompt, and closing messages
  rewritten in Alcatraz vocabulary instead of generic container
  terminology.
- **`version_tip` for every language.** Free-form per-language
  guidance string ("Version examples: 3.11, 3.12, 3.13. Pin a
  concrete release; \"latest\" is rejected.") shown in the wizard
  before the version prompt **and** rendered as a `# `-prefixed
  comment block above each `version =` line in the generated TOML.
  The same string serves both surfaces — DRY by design.
- **`manager_tip` field, parallel to `version_tip`.** Same pattern:
  printed before the manager prompt when multiple managers are
  available, rendered as a TOML comment above `manager =` either
  way.
- **`bundled_managers` field on `SUPPORTED_LANGUAGES` entries.**
  Separates "default for the wizard" from "ships with the runtime."
  pip stays bundled with Python; npm with Node; cargo with Rust; go
  is the runtime; dotnet is the runtime. Java's tuple is empty —
  Maven is the wizard default but **must** be installed via mise.
  The Dockerfile generator now installs the manager iff it's not
  bundled, fixing a latent bug where accepting the Java default
  left Maven unavailable inside Alcatraz.
- **Reuse-prompt for existing alcatrazer-generated configs.**
  Re-running `alcatrazer init` against a repo with a previously
  generated `coding-environment.toml` now offers
  `Detected existing coding-environment.toml from a previous
  alcatrazer install. Reuse it as-is? [Y/n]`. Detection is
  schema-version-aware (header markers tabulated by
  `schema_version`), so future schema bumps can extend it without
  refactoring detection logic.
- **Per-repo deterministic naming.** Image tags and container names
  are now derived from the repo's canonical absolute path:
  `alcatraz-workspace:<basename>-<hash12>` and
  `workspace-<basename>-<hash12>`, where `<hash12>` is
  SHA-256(canonical_path)[:12]. Multiple alcatrazers can coexist on
  one machine without container-name collisions.
- **`alcatrazer visit` subcommand.** Opens an interactive bash as
  the `agent` user inside the running Alcatraz via a new
  `Alcatraz.shell()` port method (DockerPrison implements via
  `os.execvp` on `docker exec -it -u agent`). Replaces the old
  `docker exec` instructions in the README. Errors cleanly when
  Alcatraz isn't running or no `.alcatrazer/` directory exists; no
  auto-start.
- **Stale-image self-healing.** The Dockerfile generator now emits
  a `LABEL alcatrazer.config_hash="<16-hex>"` baked into the image
  at build time, derived from a hash of the rendered Dockerfile
  itself. New `Alcatraz.image_matches(expected_hash) -> bool` port
  method lets `cmd_start` and `_first_run_after_init` detect when
  the on-disk image is stale relative to the current
  `coding-environment.toml` — closes the "rm -rf .alcatrazer/ +
  re-init silently reuses old image" bug.

### Changed

- **`manager =` is always emitted in generated TOML**, even when
  the user accepts the language's default. Symmetric with
  `version =`. Self-documenting at the line a user would later
  edit; no need to re-run init or read README.
- **Apt-install ordering preserves user-declared order**, deduped
  by first occurrence, instead of `sorted(set(...))`. Visible in
  the rendered Dockerfile when `[os].packages` and per-language
  `required_os_packages` are merged.
- **`cmd_start` post-success message** now ends with
  `Ready. To enter the Alcatraz: alcatrazer visit` (was a bare
  `Ready.`).
- **`cmd_init` closing messages** end with
  `then \`alcatrazer visit\` to step inside.` on both the
  has-Claude-creds and API-key paths.
- **Tip layout in the wizard.** Blank line moved to BEFORE the
  `Tip:` line (was after) so the tip visually groups with the
  upcoming prompt instead of trailing the previous answer.

### Fixed

- **Java + accepted-default Maven.** Accepting Maven as the wizard
  default no longer leaves the image without Maven. Caught by the
  `bundled_managers` redesign described above.
- **Stale Docker image after `rm -rf .alcatrazer/`.** Previously
  `image_exists()` would return True for an out-of-date image and
  `_first_run_after_init` would silently reuse it. Now the
  `alcatrazer.config_hash` LABEL forces a rebuild whenever the
  current configuration's hash doesn't match the image's baked-in
  hash.
- **`coding-environment.toml` edits between starts now actually
  rebuild.** A regression introduced by the `image_matches`-based
  routing widening: when the image's `config_hash` LABEL didn't
  match the recipe hash from the freshly-loaded `coding_env`,
  `_first_run_after_init` rebuilt from the on-disk
  `.alcatrazer/Dockerfile` — which was the **stale** Dockerfile that
  had built the stale image in the first place. The rebuild
  produced an identical stale image with the same stale label, and
  the next start re-entered the same branch: an infinite no-op
  rebuild loop, masked by `save_coding_environment_snapshot`
  refreshing `.last` to match the current toml. Fix:
  `_first_run_after_init` now calls `prison.generate_prison(coding_env)`
  before `prison.build()` on the rebuild path, so a TOML edit
  between starts actually picks up the user's changes.
- **Schema-mismatch tracebacks.** Unsupported `schema_version`
  values used to surface as raw Python tracebacks in `cmd_start`;
  now they print a single `ERROR:` stderr line with an actionable
  "upgrade alcatrazer" message.
- **Malformed `coding-environment.toml` tracebacks.** Symmetric
  with the schema-mismatch handler above: `tomllib.TOMLDecodeError`
  raised while parsing a user-edited `coding-environment.toml`
  (e.g. a left-over commented-out `[languages.node]` header above
  uncommented `version =` / `manager =` lines, which silently
  duplicates keys inside the previous section) used to surface as
  a raw Python traceback. Now `cmd_start` catches it and prints a
  single `ERROR: coding-environment.toml is not valid TOML:` line
  with tomllib's own line/column message.
- **`manager = "uv"` build failure.** `mise use --global uv` aborted
  with `GitHub artifact attestations verification failed` because
  mise's aqua plugin expects a workflow-signed build-provenance
  attestation, while uv 0.11.x publishes a release-type attestation
  signed by GitHub's release infrastructure (cert SAN
  `dotcom.releases.github.com`; predicateType `in-toto release/v0.2`).
  mise's check disqualifies the release-type attestation and the
  install fails. Phase 1.2.7 works around it by splitting the uv
  install into its own RUN, prefixed with
  `MISE_AQUA_GITHUB_ATTESTATIONS=false` and preceded by an
  explanatory comment block (visible per `docs/design_principles.md`
  §Trust & Verification — disabled-verification must be visible to
  the reader). mise's sha256 checksum verification still runs.
  Data anchored in the new `languages.AQUA_ATTESTATION_MISALIGNED`
  frozenset so the cleanup path is "delete the manager from the
  set, rebuild, ship" once upstream catches up. See
  [`docs/features/more_languages_support.md`](docs/features/more_languages_support.md)
  Phase 1.2.7 for full detail.

### Known issues (deferred to 0.0.5)

- **Promotion in default `mirror` mode rewrites outer history and
  leaves the working tree out of sync.** See the warning at the top
  of this entry and
  [`docs/features/change_promotion_machinery.md`](docs/features/change_promotion_machinery.md).

---

## [0.0.3] — 2026

Documentation-only release.

### Changed

- **Installation section in README** distinguishes the **ephemeral**
  install path (`pipx run alcatrazer init`, `uvx alcatrazer init`)
  from the **persistent** install path
  (`pipx install alcatrazer` followed by `alcatrazer init`). Earlier
  wording conflated the two and led some users to expect a global
  binary after running the ephemeral form.

---

## [0.0.2] — 2026

Release-pipeline and packaging-metadata fixes. No code changes
inside the package.

### Fixed

- **PyPI README rendering.** All image and link URLs in `README.md`
  are now absolute GitHub raw URLs. Relative paths render correctly
  on github.com but broke on the PyPI project page (PyPI cannot
  resolve relative paths against the source repo). Affected the
  Alcatraz Island banner image and several internal doc links.

### Added

- **CI release-yml guard.** New "tag is on main" check in the
  release workflow refuses to publish if the tag points at a
  commit that isn't reachable from `main`. Prevents accidental
  releases from feature branches.

---

## [0.0.1] — 2026

First public release. The PR title was *"first_release"*; the
release contents are the result of months of pre-public design and
implementation work converging on a publishable MVP.

### Added (the MVP surface)

- **Docker-based isolation primitive** — multi-stage Dockerfile
  built around three security properties: phantom UID (a UID that
  doesn't match any host user), no Docker socket inside the
  container, no host filesystem mounts beyond the workspace bind.
  `gosu` drops from root (build-time) to the agent user
  (runtime).
- **Snapshot-not-clone workspace creation.** The agent workspace
  is a flat tar-extracted snapshot of the outer repo's main
  branch tree, with one fresh `Initial commit` and no git history
  inherited. Agents see a vanilla repo with a randomly generated
  human-looking identity (random name + plausible email),
  preventing them from discovering the outer repo's developers,
  internal references, or the fact that they're inside Alcatraz.
- **Promotion engine.** `git fast-export | rewrite_identity |
  git fast-import` pipeline (operating in bytes mode to handle
  non-UTF-8 blob content like images) transfers commits from
  the workspace's inner git back to the outer repo, rewriting
  authorship to the user's configured identity. *(See
  Known-issues note in 0.0.4 — this pipeline turned out to be
  the wrong tool for the job; replacement landing in 0.0.5.)*
- **Promotion daemon.** Polls the workspace `.git/` from the
  host every 5 seconds; runs as a host-side process so nothing
  about it is visible to the agent inside the container. Two
  conflict-handling modes: `mirror` (default, promotes onto
  matching branch names) and `alcatraz-tree` (audit-only,
  promotes onto `refs/heads/alcatraz/<branch>`).
- **CLI surface.** `alcatrazer init` (interactive wizard generates
  `coding-environment.toml`, `.alcatrazer/config.toml`,
  `.env.example`), `alcatrazer start` (builds image, creates
  workspace, launches container + daemon), `alcatrazer stop`,
  `alcatrazer clear`, `alcatrazer test` (bundled selftest).
- **`coding-environment.toml`** as the user-visible recipe —
  `[os].packages`, `[languages.<name>]` with `version` and
  `manager`, `[startup].commands`. Generated with comment
  scaffolding so users can edit by hand later.
- **`.alcatrazer/`** as the on-disk per-repo state directory:
  extracted package source, phantom UID, agent identity, daemon
  PID + log, promotion marks, paused-branches state. Gitignored
  via `.git/info/exclude` so it never enters the user's
  version-controlled tree.
- **Three install paths converging on one PyPI package:**
  `pipx run alcatrazer`, `uvx alcatrazer`, and a `curl | bash`
  installer. Bundled tests (`alcatrazer test`) verify the
  security model on the user's own Docker setup.
- **Bash bootstrap, Python everything-else.** `resolve_python.sh`
  finds a Python 3.11+ via a four-tier fallback (`mise`, `pyenv`,
  PATH, system); container `entrypoint.sh` drops root via
  `gosu`. All other logic is Python stdlib only — no third-party
  dependencies.
- **Stable-source-import test discipline** — file-copy code is
  tested against the real repository source via snapshot +
  checksum, not synthetic fixtures, so refactors that drift from
  reality fail loudly.

This release reflects the full Iceberg-Principles design
(Principle 1: fight for security; Principle 2: inmates aren't
aware they live in Alcatraz) and the documented architecture in
[`docs/design_principles.md`](docs/design_principles.md).

---

[0.0.4]: https://github.com/greg-latuszek/alcatrazer/releases/tag/v0.0.4
[0.0.3]: https://github.com/greg-latuszek/alcatrazer/releases/tag/v0.0.3
[0.0.2]: https://github.com/greg-latuszek/alcatrazer/releases/tag/v0.0.2
[0.0.1]: https://github.com/greg-latuszek/alcatrazer/releases/tag/v0.0.1