# Broadening language support — backend-agnostic provisioning

## Status: Design exploration. No code change yet. We work from this document.

## Origin

The triggering question was: *what would we need to change to support C# and
Java?* Following that thread surfaced a deeper architectural question, which
is the actual subject of this doc: **what is the right user-facing contract
for "install my toolchain inside the sandbox" if Alcatrazer is to stay
backend-agnostic?**

Today the contract is shaped by Docker (Dockerfile stages, `mise use --global`).
That coupling caps both the addressable user pool and the ability to add
non-Docker sandbox backends later (VM, Firecracker, Sysbox, Kata, plain LXC,
etc.). This doc records what we found, the alternatives we evaluated, and
the design we landed on as the working hypothesis.

## Current state — what's actually there

### Whitelist

`src/alcatrazer/languages.py:10-33` exposes `SUPPORTED_LANGUAGES`, a hardcoded
dict with four entries: `python`, `node`, `rust`, `go`. Each entry declares a
`default_manager`, an allowed `managers` tuple, and a `version_check` command.

This whitelist drives three things:

1. Wizard input validation in `start.py` (`_ask_version` at line 290,
   `SUPPORTED_LANGUAGES` consumers at lines 304/321/329).
2. The post-install verify block — `docker_prison.py:122` reads
   `version_check` per declared language.
3. The `mise use --global` block — `_render_mise_uses` at
   `docker_prison.py:106-116`.

Only (1) is user-facing UX. (2) and (3) are implementation details that
exist because we picked mise and Dockerfile stages.

### Image structure

Three Dockerfile stages assembled in `_render_dockerfile`
(`docker_prison.py:127-159`):

- **Stage 1 `dev-base`**: security primitive — phantom UID, gosu, ca-certs,
  git, curl. Also installs **mise** via `curl https://mise.run | sh`
  (line 57). Mise is in the security baseline today, but has no security
  role; it's a convenience tool that happens to live there.
- **Stage 2 `ai-base`**: AI client baseline — installs the Claude Code CLI
  (line 76). Also no language work.
- **Stage 3 `dev`**: language layer, fully generated from
  `coding-environment.toml`. `[os].packages` becomes one apt-get RUN,
  `[languages.*]` becomes `mise use --global` calls, and a verify block
  closes it.

**Stage 3 is the only stage that touches languages.** Stages 1 and 2 are
language-agnostic.

### TOML schema (today)

```toml
[os]
packages = ["build-essential", "libpq-dev"]

[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"

[startup]
commands = ["uv sync", "npm install"]
```

`version` is a free-form string (see `_ask_version` returning
`input().strip()`, and `start.py:408-411` quoting all examples). It flows
through `_render_mise_uses` via plain f-string interpolation
(`docker_prison.py:112`), so anything mise's plugin syntax accepts works —
including distribution prefixes like `temurin-21.0.5` or `corretto-21`
for Java.

## The C# / Java sub-question — what we found

### Standard variants

- **Java**: no single canonical distribution. OpenJDK is the spec; the
  shipped distributions are Eclipse Temurin (Adoptium — the de-facto
  default), Amazon Corretto, Azul Zulu, BellSoft Liberica, GraalVM, Oracle
  JDK (proprietary). LTS in active use: 8, 11, 17, **21**; 25 is the next
  LTS. mise's `java` core plugin accepts `java@21`, `java@temurin-21.0.5`,
  `java@corretto-21`, etc.
- **C# / .NET**: one canonical distribution (Microsoft .NET, MIT, cross-
  platform). LTS: **8** (current), 9 (STS), 10 LTS due Nov 2025. mise has
  a `dotnet` plugin (asdf-backed, not core).

### Can `uv` install them?

No. `uv` is Astral's Python-only tool — it manages Python interpreters and
Python packages. Java and .NET would go through mise the same way `go` and
`rust` already do.

### Default package managers

| Lang     | Default manager                       | Alternatives           | Bundled?                                          |
| -------- | ------------------------------------- | ---------------------- | ------------------------------------------------- |
| **Java** | `maven`                               | `gradle`, `sbt`, `ant` | No — installed separately (mise has plugins)      |
| **.NET** | `dotnet` (uses NuGet under the hood)  | none meaningful        | Yes — ships with the SDK                          |

So .NET maps onto the `go`/`rust` pattern (`default_manager: "dotnet"`,
single-element `managers`). Java looks more like Python/Node (one runtime,
multiple build tools).

### Concrete change list (under the **today's whitelist** assumption)

1. Add `java` and `dotnet` entries to `SUPPORTED_LANGUAGES`.
2. Extend the per-language assertions in
   `tests/test_start.py:368-420`.
3. Decide Java distribution policy — either default Temurin silently, or
   add an optional `distribution` field. Note: since `version` is already
   a free-form string passed verbatim to mise, users can write
   `version = "temurin-21.0.5"` today without any schema change — the
   string-equals-mise-spec convention covers it.
4. Java `version_check`: prefer `"java -version 2>&1"` because Java prints
   to stderr.

This was the answer under the existing architecture. It's correct but, as
the next sections argue, it's solving the wrong problem.

## First reframing — languages are convenience, not security

Alcatrazer's value proposition is the security sandbox: phantom UID, no
host mounts, no credentials, no docker socket, controlled network. **None
of that depends on what language runtime sits on top.** Stage 3 is icing.

Implications:

- A Java or .NET developer can already use Alcatrazer **today** by leaving
  `[languages]` empty and installing their toolchain inside the running
  container (apt, SDKMAN!, manual download). The whitelist is a wizard
  *convenience*, not a *capability gate*.
- Forcing **mise** on every user is opinionation, not infrastructure. A
  Ruby dev who lives in rbenv, a Python dev in pyenv, a Java dev in
  SDKMAN!, a Node dev in fnm — they're each being asked to learn a new
  tool for no security benefit. Mise is great, but it's not load-bearing.
- The whitelist gate (`SUPPORTED_LANGUAGES`) caps the addressable
  population to whatever we've personally curated. That's the opposite
  of "broaden the user pool."

### Path A — extend the whitelist (status quo)

- **Pro:** clean wizard UX for the curated set; predictable verify block.
- **Con:** every new language is a maintenance commitment we own; user
  pool capped at our roadmap.

### Path B — drop the whitelist, keep mise

- Treat `version` as a free-form mise spec; let any string through.
- `_render_mise_uses` already does this — only the wizard validation
  blocks it.
- **Pro:** every mise plugin works automatically.
- **Con:** still forces mise on users who'd rather use their ecosystem's
  native version manager.

### Path C — make stage 3 optional

- If `[languages]` is empty / `coding-environment.toml` absent, don't
  generate stage 3. Container is dev-base + ai-base only.
- Agents install their own toolchain on first run, persisted in the
  writable layer (same lifecycle as package caches today: survives
  `stop`/`start`, lost on `clear`).
- **Pro:** universally compatible; matches the "Alcatraz is a security
  primitive" framing.
- **Con:** "first-run polish" is the user's problem for anything we
  haven't pre-baked.

We were leaning toward C with an optional `Dockerfile.user` escape hatch
for users who want to bake their toolchain into the image. Then we
realized that's still Docker-coupled.

## Second reframing — backend portability requires bash

Alcatrazer ships an `Alcatraz` port (`alcatraz.py`) with `DockerPrison`
as one adapter (`docker_prison.py`). Future adapters could be VM-based,
Firecracker, Sysbox, Kata, plain LXC. `Dockerfile.user` is a leaky
abstraction — it bakes Docker's layering and `RUN` semantics into the
user contract.

The portable contract — what every Linux-guest sandbox backend can
actually execute — is **a list of bash commands.** Bash on Ubuntu (or any
Linux distro the backend chooses) is the universal substrate.

But raw "list of bash commands" is too coarse. Two axes matter to every
backend, regardless of mechanism:

### Axis 1 — Phase: provision vs startup

- **Provision** runs **once**, at image/snapshot build time. Output is
  baked (Docker layer, VM golden image via Packer/cloud-init, etc.).
  Network and root are available. Caches across restarts.
- **Startup** runs **every time** the sandbox boots. Output is ephemeral
  (writable overlay, tmpfs). Runs as the agent user.

`coding-environment.toml` already has `[startup].commands`. The missing
half is `[provision]` — currently expressed implicitly through
`[os].packages` and `[languages.*]`, both of which are Docker-shaped
sugar.

### Axis 2 — User context: root vs agent

- Some commands must run as **root** (`apt-get install`, `useradd`,
  kernel modules, system-wide installs).
- Others must run as **agent** so files land in `$HOME` with the right
  ownership (`curl https://get.sdkman.io | bash`, `mise use --global`,
  `pyenv install`).

A single list with `sudo` works but is fragile across backends and forces
the user to think about privilege escalation. Splitting them is cleaner
and matches what every backend already does internally (Docker:
`USER root` / `USER agent`; VM: cloud-init `runcmd` runs as root, then
drop; etc.).

## The proposed contract — two lists, two phases, two users

```toml
[provision]
# Runs once at build/snapshot time. Network + root available.
root_commands = [
  "apt-get update && apt-get install -y --no-install-recommends openjdk-21-jdk maven",
]
# Same phase, but dropped to agent before running. Lands in agent's $HOME.
user_commands = [
  "curl -s https://get.sdkman.io | bash",
  "bash -c 'source ~/.sdkman/bin/sdkman-init.sh && sdk install kotlin 1.9.22'",
]

[startup]
# Runs as agent every boot.
commands = ["mvn dependency:go-offline"]
```

That's the entire user-facing contract for "install my toolchain." Every
sandbox backend can implement it because every backend can: (a) run a
Linux userspace, (b) execute bash as a chosen UID, (c) distinguish
"build phase" from "run phase". Backends without a separate build phase
collapse provision into a first-boot script and cache via snapshot.

### The Alcatraz port reduces to

```
provision(root_cmds, user_cmds) -> image_or_snapshot
start(image_or_snapshot, startup_cmds) -> running_sandbox
```

(Plus the security guarantees the port already owns: phantom UID, no host
mounts, no credentials, no socket, controlled network. Those are
backend-specific implementations of the same security invariants and are
*not* in the user contract.)

## How today's structures map onto the contract

`[os].packages`, `[languages.*]`, and any future `Dockerfile.user` idea
all become **sugar that desugars to bash**:

| Current | Desugars to |
|---|---|
| `[os].packages = ["X", "Y"]` | `provision.root_commands += ["apt-get update && apt-get install -y X Y"]` |
| `[languages.python] version="3.12" manager="uv"` | `provision.user_commands += ["mise use --global python@3.12 uv"]` |
| `[languages.java] version="temurin-21.0.5"` | `provision.user_commands += ["mise use --global java@temurin-21.0.5"]` |

The desugaring happens **in the wizard / config loader**, not in the
backend. The `Alcatraz` port only ever sees the resolved bash lists.

Architectural consequences:

1. **Backend interface stays tiny.** `DockerPrison` and a hypothetical
   `FirecrackerPrison` both implement the same two-method contract.
   Adding a backend doesn't require understanding mise or Java versions.
2. **Sugar is pluggable.** mise + `SUPPORTED_LANGUAGES` becomes one of
   *several possible* sugar generators — alongside SDKMAN, asdf, pyenv,
   raw apt, Nix, whatever someone writes. None of them are
   backend-coupled.
3. **The whitelist demotes from gate to suggestion.** Rename
   `SUPPORTED_LANGUAGES` → `WIZARD_SUGGESTIONS` (or similar) to be honest
   about its role: a curated UX hint for the interactive wizard, not a
   capability cap.

## Caveats / things to nail down

- **Idempotency expectation.** `provision` runs once-per-image; commands
  needn't be idempotent. `startup` runs every boot; commands must be
  (`mvn dependency:go-offline` already is). Document this clearly — it's
  the most common foot-gun across backends.
- **Artifact paths and ownership.** `provision.root_commands` writes as
  root; the agent must be able to read at runtime. Either commands chown
  explicitly (`chown -R agent /opt/jdk`) or land tools in already-readable
  locations (`/usr/local/bin`, `/opt`). This bites identically on Docker
  and VMs — belongs in user docs, not backend code.
- **Network during provision.** Docker build has network by default.
  Some sandbox backends restrict it. We need to make "outbound during
  provision" a backend capability the user can rely on, and document it
  in the port contract.
- **Where does mise itself live?** Today `dev-base` installs mise
  unconditionally (`docker_prison.py:57`). Under the new model:
  - Option 1: keep it. Mise is small, harmless, and pre-installing it
    makes the curated path zero-friction. Users who don't want mise
    simply don't reference it from `[provision]`.
  - Option 2: move it into the mise-sugar generator's emitted commands,
    so users who pick a non-mise sugar (or no sugar) get a leaner image.
  - Leaning toward Option 1 — the size cost is trivial and the UX win
    for the curated path is real.
- **Backwards compatibility.** Existing `coding-environment.toml` files
  with `[os].packages` and `[languages.*]` must keep working. They do —
  they're sugar that desugars to `[provision]`. `[provision]` is
  additive; users who don't write it lose nothing.
- **Verify block.** The current verify block (`_render_verify_block` at
  `docker_prison.py:119-124`) runs version checks for declared languages.
  Under the new model, verify becomes:
  - Always-present checks (`git --version`, `claude --version`) — kept.
  - Per-language checks — only emitted if the user opted into curated
    sugar that knows the version-check command. Raw bash users don't get
    automatic verify, which is fair; they can put their own checks at the
    end of `provision.user_commands`.
- **Wizard UX under the new model.** The wizard still offers the curated
  set (Python/Node/Rust/Go, plus whatever else we add as sugar). For
  anything outside the set, it tells the user: *"Add your install
  commands to `[provision]` in `coding-environment.toml`."* — and
  optionally drops a commented example.

## What this means for C# / Java specifically (long-term view)

The eventual right answer for arbitrary languages is:

1. **Ship the `[provision]` contract.** Once that exists, C#, Java, Ruby,
   PHP, Kotlin, Elixir, Lua, Zig, Dart, Scala, Clojure, R, Julia, Perl,
   Crystal — and anything else with a Linux installer — work without us
   touching the codebase.
2. **Then decide separately** whether to add wizard sugar (curated
   defaults, version-check commands) for big-population languages. That
   becomes ergonomic polish, not a prerequisite.
3. **The bottleneck check:** verify the empty-stage-3 / `[provision]`-
   driven path actually works under the agent user — specifically, that
   `apt-get install` works at provision time as root, and that
   user-context commands like `curl | bash` install into the agent's
   `$HOME` correctly.

That's a v2 refactor and breaks the current `coding-environment.toml`
contract. We don't want to gate C# adoption on it.

## Phase 1 — low-hanging fruit (do now)

To get C# / .NET developers onboard immediately and prepare the file
format for the v2 refactor without trapping any existing user, we land
two small, independent changes first. Both stay inside the current
architecture (mise + Stage 3 sugar). Neither requires the v2 contract.

### Phase 1.1 — Schema versioning of `coding-environment.toml`

Add a top-level integer field, set today to `1`. The loader reads it,
defaults to `1` when absent (so every existing config keeps working),
and errors on any value the running alcatrazer doesn't recognize. This
gives us a clean cut-over when v2 introduces `[provision]`.

```toml
schema_version = 1

[os]
packages = ["build-essential"]

[languages.python]
version = "3.12"
```

Loader behavior:

| Found `schema_version` | Behavior                                                    |
| ---------------------- | ----------------------------------------------------------- |
| absent                 | treat as `1` (backwards compatible)                         |
| `1`                    | parse current schema (`[os]`, `[languages.*]`, `[startup]`) |
| `2` (future)           | parse v2 schema (`[provision]`, etc.)                       |
| unknown / higher       | error — ask user to upgrade alcatrazer                      |

**Why integer, not semver:** for a config file the relevant question is
"do I know how to parse this?" — yes/no. Minor-version additive changes
inside one major can be handled by tolerating unknown sub-keys at parse
time (warn, don't error). Bumping the integer is the explicit "you must
migrate" signal. Simpler, clearer, and matches the convention in
analogous tools (VS Code `tasks.json` uses `"version": "2.0.0"` but only
the major matters; dprint, asdf-style configs use plain integers).

**Concrete code touches:**

- Wherever `coding-environment.toml` is parsed (the wizard / start
  path) reads the field and dispatches.
- Wizard writer (`start.py` near the `_format_toml_string` usage at
  line 488) emits `schema_version = 1` as the first line of generated
  TOML.
- Tests assert: missing field loads as v1, explicit `1` loads, `2`
  errors clearly with a "upgrade alcatrazer" message, `0` errors with
  a "downgrade or migrate" message.
- `cmd_start` catches `UnsupportedSchemaVersionError` raised by either
  routing branch (`_first_run_after_init`, `_subsequent_run`) and prints
  the validator's message as a single `ERROR:` stderr line — same
  presentation pattern already used for `PrisonBuildError` /
  `PrisonStartError`. Without this catch, a too-new schema surfaces as
  a Python traceback, which reads as a tool crash even though the
  message itself is actionable.
- Tests assert that path: exit code 1, stderr contains the offending
  version + "upgrade alcatrazer", stderr does **not** contain
  `Traceback` or the exception class name, and `prison.build` /
  `prison.start` are never invoked.
- README configuration section gains a one-line note about the field.

**Out of scope for this phase but worth flagging:**
`.alcatrazer/config.toml` (per-developer alcatrazer config) is a
separate file with a separate schema. Applying the same convention
there is a parallel cleanup, not blocking.

### Phase 1.2 — C# / .NET added to `SUPPORTED_LANGUAGES`

The single dict entry, parallel to the existing `go` and `rust` shape:

```python
"dotnet": {
    "default_manager": "dotnet",
    "managers": ("dotnet",),
    "version_check": "dotnet --version",
},
```

**Naming choice: `dotnet`, not `csharp`.** The runtime is .NET (it also
hosts F# and VB.NET); mise's plugin is named `dotnet`; the user-visible
TOML key becomes `[languages.dotnet]`. The wizard's help text and
README should mention "C# / F# / VB.NET" so C# developers find it
when scanning.

**Default-version suggestion for the wizard: `10.0.100`** (.NET 10
LTS, shipped Nov 2025, supported until Nov 2028 — current LTS as of
April 2026). `8.0.404` (.NET 8 LTS, supported until Nov 2026) is a
valid alternative for users on legacy projects.

**Default package manager:** `dotnet` (the CLI). Unlike Java, .NET has
one canonical toolchain — `dotnet add package`, `dotnet restore`,
`dotnet build` all ship with the SDK and use NuGet under the hood. So
the entry has a single-element `managers` tuple, same as `go` and
`rust`.

**Concrete code touches:**

- `src/alcatrazer/languages.py:10-33` — add the entry.
- `src/alcatrazer/start.py:408-411` — add wizard example:
  `"dotnet": ["# [languages.dotnet]", '# version = "10.0.100"']`.
- `src/alcatrazer/tests/test_start.py:368-420` — per-language tests
  parallel to the existing `test_*_default_manager_is_*_with_alternatives`,
  `test_*_version_check`. Specifically:
  - `test_dotnet_default_manager_is_dotnet`
  - `test_dotnet_version_check_uses_dotnet`
  - existing `test_every_language_has_a_version_check_command` covers
    presence automatically.
- `README.md` line 505 — add `dotnet` to the supported list, with a
  parenthetical "C# / F# / VB.NET".

### Caveat to verify before merging Phase 1.2

**mise's `dotnet` plugin is asdf-backed, not a core mise plugin.** The
current `_render_mise_uses` (`docker_prison.py:106-116`) emits a single
chained `RUN mise use --global dotnet@<version>` line. We need to
confirm empirically that mise auto-installs the asdf plugin on first
use of an unknown tool name.

**Verification command** (run inside a fresh Alcatraz container):

```bash
mise use --global dotnet@10.0.100
dotnet --version
```

- **If clean:** no further change needed. Ship the entry as-is.
- **If errors with "plugin not found":** small enhancement to
  `_render_mise_uses` — emit `mise plugin install dotnet` before the
  `mise use --global` line for languages flagged as needing it. Keep
  the data-driven shape by adding an optional per-language field, e.g.
  `"requires_plugin_install": True`, defaulting to `False`. Touches
  `languages.py` (one extra key), `docker_prison.py` (one extra line
  in the rendered RUN), and one new test asserting the `plugin
  install` line is emitted only for flagged languages.

This is the only unknown in Phase 1. Everything else is mechanical.

### Independence and ordering

Phase 1.1 (schema versioning) and Phase 1.2 (C# entry) don't depend on
each other and can land as two separate commits or one bundled PR.

Recommended order if separated: **schema versioning first**, so the
C# wizard-generated config naturally includes `schema_version = 1`
from the moment it ships. If bundled in one PR, no ordering question.

### What Phase 1 does NOT do

To keep scope tight:

- Does **not** introduce `[provision]`. That's v2.
- Does **not** drop the `SUPPORTED_LANGUAGES` whitelist. The whitelist
  is still a gate in v1.
- Does **not** change the Dockerfile structure or backend interface.
- Does **not** address Java. Java's distribution-choice question
  (Temurin vs Corretto vs ...) is non-trivial and benefits from being
  done under the v2 model where users can express their distro
  preference as raw bash. .NET is the cleaner first add because it has
  one canonical distribution.

## Recommended next steps (sequenced)

**Phase 1 — now:**

1. **Schema versioning.** Add `schema_version = 1` to
   `coding-environment.toml`. Loader, wizard writer, tests,
   documentation note.
2. **C# / .NET.** Add `dotnet` entry to `SUPPORTED_LANGUAGES`. Tests,
   README, wizard example. Verify mise plugin auto-install in a smoke
   test; emit `mise plugin install dotnet` only if the smoke test
   reveals it's needed.

**Phase 2 — v2 refactor (deferred):**

3. Validate the empty-stage-3 path on the current container — confirm
   `apt-get` works at provision time and `curl | bash` works as agent.
4. Sketch the `Alcatraz` port interface under the two-method contract
   (`provision`, `start`). Verify `DockerPrison` can implement it
   without regression. Sketch a hypothetical `FirecrackerPrison` for
   the same methods — surface any axis we missed.
5. Implement `[provision]` as a v2 TOML section. Bump
   `schema_version` to `2`.
6. Make existing `[os].packages` and `[languages.*]` desugar into
   `[provision]` lists in the config loader (still under v1 schema for
   backcompat; v2 schema decides whether to keep or remove the sugar).
7. Demote `SUPPORTED_LANGUAGES` from gate to suggestion (rename to
   `WIZARD_SUGGESTIONS` or similar).
8. Java added cleanly under v2 — distribution prefix expressed as raw
   `version = "temurin-21.0.5"` or via raw `[provision]` bash for users
   on SDKMAN!.
9. Documentation pass — README "Supported languages" section becomes
   "Curated convenience languages — and how to add anything else."

## Open questions for the next pass

- **Phase 1 — mise auto-install of asdf plugins.** Resolved by the
  smoke test above; one of two known outcomes.
- **Phase 2 — agent user sudo at runtime.** Does the entrypoint leave
  the agent user with usable `sudo`, or is the expectation that all
  root work happens at provision time and runtime has no privilege
  escalation? (Probably the latter, by design — but needs to be
  stated explicitly in the v2 contract.)
- **Phase 2 — `[provision]` per-step user selection.** Should
  `[provision]` allow per-step `user` selection (per-command root vs
  agent) instead of two separate lists? Two lists is simpler;
  per-step is more flexible. Lean toward two lists until a real use
  case forces per-step.
- **Phase 2 — mise as default sugar.** Keep or remove from `dev-base`?
  Leaning keep — small, harmless, makes the curated path zero-friction.
