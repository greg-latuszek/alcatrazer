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

The dict entry — shape parallel to the existing `go` and `rust`
entries, plus one new field (`required_os_packages`, see below):

```python
"dotnet": {
    "default_manager": "dotnet",
    "managers": ("dotnet",),
    "version_check": "dotnet --version",
    "required_os_packages": ("libicu74",),
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

### New schema field — `required_os_packages`

.NET is the first language Alcatrazer supports that has a non-trivial
OS-level *runtime* dependency. Without `libicu74`, every `dotnet`
invocation crashes immediately with:

> Couldn't find a valid ICU package installed on the system. Please
> install libicu (or icu-libs) using your package manager …

(Verified empirically — see "Verification" below.) Python, Node, Rust,
and Go all run on what Ubuntu 24.04's minimal base provides; .NET
needs ICU for globalization at startup, and `libicu` isn't in the
base. Java will likely surface similar deps once we add it.

To keep the language whitelist data-driven instead of special-casing
.NET in Dockerfile-generation code, `SUPPORTED_LANGUAGES` gains an
optional **`required_os_packages: tuple[str, ...]`** field, defaulting
to `()`. The Dockerfile generator unions this with the user-declared
`[os].packages` before emitting the apt-get RUN.

**Why this design and not "tell users to add `libicu74` themselves":**
the dependency isn't optional or use-case-specific (like a Python user
adding `libpq-dev` for psycopg) — `dotnet` literally cannot run without
it. Encoding it next to the language entry means the user picks
`[languages.dotnet]` and gets a working environment, with no
discovery-by-crash step.

**Why this design and not "let the agent install it via sudo":**
**there is no sudo inside Alcatraz.** The agent user has no privilege
escalation at runtime — that's a deliberate security property, not an
oversight. Anything that needs root (apt-get, system-wide installs,
shared library setup) **must** be baked during image build, where
Dockerfile `RUN` lines already execute as root before the entrypoint
drops to agent via gosu. `required_os_packages` rides the existing
build-time apt-get, so it's compatible with the no-sudo invariant by
construction.

For one-off debugging, a host-side `docker exec -u root -w /workspace
workspace bash` is the supported escape hatch — it grants temporary
root inside the container without giving the agent user any standing
privilege.

### Concrete code touches

- `src/alcatrazer/languages.py` — add the `dotnet` entry **with**
  `required_os_packages = ("libicu74",)`. Update the module docstring
  to document the new field.
- `src/alcatrazer/docker_prison.py` — update the apt-install rendering
  to take the union of `[os].packages` and every declared language's
  `required_os_packages`. Sort + dedupe so the rendered Dockerfile is
  stable across runs.
- `src/alcatrazer/start.py` — add wizard example:
  `"dotnet": ["# [languages.dotnet]", '# version = "10.0.100"']`.
- `src/alcatrazer/tests/test_start.py` — per-language tests parallel
  to the existing `test_*_default_manager_is_*_with_alternatives`,
  `test_*_version_check`:
  - `test_dotnet_default_manager_is_dotnet`
  - `test_dotnet_version_check_uses_dotnet`
  - `test_dotnet_declares_libicu74_as_required_os_package`
  - existing `test_every_language_has_a_version_check_command` covers
    presence automatically.
- `src/alcatrazer/tests/test_docker_prison.py` — generator tests:
  - dotnet declared → rendered Dockerfile's apt-install line includes
    `libicu74`.
  - python only declared → rendered Dockerfile's apt-install line does
    NOT include `libicu74` (no over-reach).
  - dotnet + user-declared `[os].packages = ["build-essential"]` →
    apt-install line contains both, sorted/deduped.
- `README.md` — add `dotnet` to the supported list with a parenthetical
  "C# / F# / VB.NET", and a one-line note that selecting it
  auto-installs `libicu74` so users aren't surprised by an unfamiliar
  package in their build.

### Verification — completed

The previously-flagged unknown (mise plugin auto-install) and a new
unknown (runtime OS deps) were both probed inside a fresh Alcatraz
container:

```bash
mise use --global dotnet@10.0.100   # SDK install — succeeded
dotnet --version                     # crashed: missing libicu (ICU)
```

mise treats `dotnet` as a **core plugin** (the trace shows
`core:dotnet@10.0.100` and uses the official MS install script
`dotnet-install.sh`) — not asdf-backed. So no `requires_plugin_install`
flag is needed; `mise use --global dotnet@<version>` works as-is.

The remaining failure was the ICU runtime dependency. Resolution
verified by installing `libicu74` from the host side as root (NOT via
sudo inside the container, per the no-sudo invariant):

```bash
docker exec -it -u root -w /workspace workspace bash
apt-get update && apt-get install -y libicu74
exit
# back as agent:
dotnet --version  # → 10.0.100   ✓
```

This is what `required_os_packages = ("libicu74",)` baked into image
build will produce automatically — same effect, just done at the
correct phase (image build, root legitimate) rather than as runtime
privilege escalation (which is forbidden).

### Phase 1.2.1 — Wizard self-explanation

The `alcatrazer init` wizard uses domain terms — "promoted commits",
"baked into Alcatraz", "agents" — without explaining them. New users
see "Use for promoted commits? [Y/n]" before they know what
"promoted" means in this context, why we ask for git identity, what
gets baked vs. run-on-each-boot, and how their commits differ from
agent commits. Phase 1.2.1 fixes that without expanding the wizard
into a help system.

**Three changes to the wizard:**

1. A one-time intro panel at the top of `alcatrazer init` (after
   "Starting interactive setup...") with an ASCII diagram + two short
   prose paragraphs that establish the credential and promotion model.
2. Section banners + 2–4 lines of context before each subsequent
   `ask_*` prompt (promotion identity, languages, system packages,
   startup commands).
3. Closing messages rewritten in Alcatraz vocabulary instead of
   container terminology ("Alcatraz" / "agents repo", not "workspace"
   / "image").

**The planned transcript:**

```text
$ alcatrazer init
Starting interactive setup...

What this sets up:

   your repo  <--- promote ---  Alcatraz (agents repo)
   ---------                    ---------------------
   YOUR identity                fake throwaway identity
   YOUR git credentials         no credentials, no SSH keys

Agents commit inside Alcatraz; a daemon promotes commits back to your
repo, re-authored as YOU. Agents cannot push — only you can.

This wizard writes coding-environment.toml; you can edit it before
running `alcatrazer start` if you want to tweak.

=== Promotion identity ===
Re-authored onto agent commits when the daemon promotes them to your
repo (see diagram above).

Detected git identity: Alice Example <alice@example.com>
Use for promoted commits? [Y/n]

=== Languages ===
Languages you list here are baked into Alcatraz so agents have a ready
dev environment from day one. Stored in coding-environment.toml; edit
before `alcatrazer start` to tweak the picks below.

What languages does this project use? (supported: python, node, rust, go, dotnet)
Languages (comma-separated): python, dotnet
  Version for python: 3.12
  Package manager for python? [pip] / uv / poetry / pipenv:
  Version for dotnet: 10.0.100

=== System packages (optional) ===
Extra apt packages baked into Alcatraz at build time, alongside the
languages above.

Any system packages needed? (e.g. libpq-dev ffmpeg; empty for none):

=== Startup commands (optional) ===
Run every time Alcatraz boots — typically `uv sync`, `npm install`, or
similar dev-env prep. Unlike languages above, these are NOT baked in.

Commands to run after Alcatraz start (one per line, empty line to finish):
> uv sync
>

Writing configuration...
  coding-environment.toml      (commit to git — your team's workspace recipe)
  .alcatrazer/config.toml      (per-developer; auto-excluded from git)
  .env.example                 (commit to git — placeholders only, no secrets)

Generating Alcatraz recipe...

Claude credentials found on host — they will be used by Alcatraz.
Next: run `alcatrazer start` to build Alcatraz and run it with own git.
```

**Naming choice in the diagram:** `Alcatraz (agents repo)` mirrors
`your repo` on the left side — both labels are 1-line "what this is",
parallel structure makes the contrast immediate. The next sentence
("Agents commit inside Alcatraz") is the equivalent of "where agents
work", so we don't lose that information.

**"See diagram above" appears once,** in the promotion-identity
banner — that's the one section whose content (identity + credentials)
is directly anchored in the diagram. Other sections don't reference
it because the diagram doesn't speak to them.

**Closing-line rewrites:**

| Before                                                                   | After                                                                       |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| "they will be mounted into the workspace"                                | "they will be used by Alcatraz"                                             |
| "Next: run `alcatrazer start` to build the image and launch the workspace" | "Next: run `alcatrazer start` to build Alcatraz and run it with own git"   |

The "with own git" tail is the explanatory hook that recalls the
diagram — Alcatraz has its own git history; promotion is what bridges
the two.

**Concrete code touches:**

- `start.py`: new `_print_init_intro()` helper that emits the
  diagram + two prose paragraphs.
- `cmd_init`: call `_print_init_intro()` after the
  "Starting interactive setup..." line; rewrite the two closing
  lines using Alcatraz vocabulary.
- `ask_promotion_identity`, `ask_languages`, `ask_os_packages`,
  `ask_startup_commands`: each prepends a `=== Section ===` banner
  plus a 2–4 line explainer before its existing prompt logic. No
  changes to input parsing or return values.

**Tests** (per the "test every decision" rule):

- `_print_init_intro` output contains the key terms the diagram is
  meant to introduce: `promote`, `Alcatraz`, `your repo`, `agents`,
  `fake` / `throwaway`, `credentials`, `coding-environment.toml`.
- `ask_promotion_identity` output contains the `Promotion identity`
  banner header and references `promote` (so the existing
  "Use for promoted commits?" prompt is no longer cold).
- `ask_languages` output contains the `Languages` banner header and
  the words `baked` and `coding-environment.toml`.
- `ask_os_packages` output contains the `System packages` banner
  header and `baked`.
- `ask_startup_commands` output contains the `Startup commands`
  banner header, `every time`, and `NOT baked` (the explicit contrast
  with languages).
- `cmd_init` closing-message scrub: when `_host_has_claude_creds()`
  returns True, the printed lines mention `Alcatraz` and do **not**
  contain `workspace` or `image`. Same for the API-key branch.

**What Phase 1.2.1 does NOT do:**

- Does **not** add new flags or change input parsing — same prompts,
  same accepted answers, same return values. Existing wizard tests
  keep working unchanged (they assert on return values, not stdout).
- Does **not** add a `--quiet` mode for the new prose. If the volume
  ever becomes a problem in CI / scripts, that's a follow-up; for
  now `init` is interactive by design.
- Does **not** refactor wizard structure (one intro + four `ask_*`
  functions stays the same shape).

### Phase 1.2.2 — Java added to `SUPPORTED_LANGUAGES`

Java is the next language that justifies a formal whitelist entry
under the dotnet pattern. Bun and Deno don't — they install fully in
agent-userspace via mise and have zero OS deps, so they're best
expressed as a single `[startup].commands` line by users who want
them; formal entries would just buy us maintenance commitment for no
ergonomic gain. Java is different because (a) mise's `java` plugin
gives multiple-manager UX worth wizarding (Maven vs Gradle), and (b)
encoding it in the whitelist documents Java as a first-class
supported runtime, which materially affects the addressable
population.

**The dict entry — adds an optional `version_tip` field used by the
wizard to nudge users about Java's distribution choice:**

```python
"java": {
    "default_manager": "maven",
    "managers": ("maven", "gradle"),
    # Java prints `-version` to stderr; redirect so the verify block's
    # chained `&&` doesn't lose the output to the docker-build noise.
    "version_check": "java -version 2>&1",
    # JDK is self-contained on Ubuntu 24.04 base — no extra OS deps
    # required for `java -version` / basic compilation. Verified
    # empirically inside a fresh Alcatraz.
    "required_os_packages": (),
    # Printed once before the `Version for java:` prompt. Java is the
    # first language with multiple shipped distributions (Temurin,
    # Corretto, Zulu, …) reachable via the same mise key, so the
    # wizard nudges users that the option exists. Other languages
    # don't declare this field; the wizard treats it as optional.
    "version_tip": (
        "Defaults to Eclipse Temurin. Prefix for alternatives, "
        'e.g. "corretto-21", "zulu-21", "graalvm-21". '
        "See README for the full list."
    ),
},
```

**Naming choice: `java`, not `jdk` and not a distribution-specific
key.** mise's plugin is `java`. A distribution prefix (Temurin,
Corretto, Zulu, …) goes in the *value*, not the key — see
"Distribution choice" below.

**Default-version suggestion for the wizard: `21`** (Java 21 LTS,
shipped September 2023, supported until September 2031). It is the
de-facto enterprise baseline as of April 2026 — wide library /
framework support, ubiquitous in production. `25` (the next LTS,
shipped September 2025) is a valid alternative for users who want
the newer LTS, but library compat is still catching up so we don't
push it as default. `17` (LTS, EOL 2029) is also still common for
legacy projects.

**Default package manager: `maven`.** Alternatives: `gradle`. We
intentionally limit to these two for v1.2.2 because both have
core/aqua mise plugins that auto-install on first use of
`mise use --global maven` / `… gradle`. `ant` and `sbt` exist as
asdf-community plugins but their mise-availability is less stable;
adding them would either need empirical verification + a
`requires_plugin_install` flag (we deliberately avoided that for
dotnet) or risk build-time failures for users who pick them. They
can be added later if a real user asks; for now the canonical
Java/Kotlin/Scala build tools are covered by `gradle`, and Maven is
the obvious default.

### Distribution choice — three touchpoints, one data-driven field

Java is the first language Alcatrazer supports where the same mise
key (`java@<version>`) maps to multiple shipped distributions:
Eclipse Temurin (default), Amazon Corretto, Azul Zulu, BellSoft
Liberica, GraalVM. mise resolves the distribution from a prefix
inside the version string, so the *schema* needs no new field —
`version` is already a free-form string passed verbatim to
`mise use --global java@<version>`:

| User writes in TOML       | mise resolves to                                         |
| ------------------------- | -------------------------------------------------------- |
| `version = "21"`          | Eclipse Temurin 21 (mise's default JDK distribution)     |
| `version = "21.0.5"`      | Eclipse Temurin 21.0.5                                   |
| `version = "corretto-21"` | Amazon Corretto 21                                       |
| `version = "zulu-21"`     | Azul Zulu 21                                             |
| `version = "graalvm-21"`  | GraalVM 21                                               |

What the schema *does* need is **discovery** — the prefix syntax is
invisible if a user doesn't know it exists. We surface the option at
three independent touchpoints, each catching a different audience:

| Touchpoint | When user encounters it | Content |
| ---------- | ----------------------- | ------- |
| **README** ("Configuration → Java distributions") | Researching alcatrazer before `init`; or later when switching distribution | Comprehensive table of supported prefixes |
| **Wizard tip** (printed before `Version for java:` prompt) | Mid-wizard, picking the version | One-line nudge with 1-2 examples + "see README" pointer |
| **Generated TOML comment** (in `_EXAMPLE_LANGUAGE_BLOCKS["java"]`) | After `init`, hand-editing `coding-environment.toml` | Inline reference at the exact line they're editing |

The wizard tip is plumbed through a new optional `version_tip` field
on `SUPPORTED_LANGUAGES` entries — symmetric with `required_os_packages`
(both optional, both per-language data, both consumed by code outside
`languages.py` without `if language == "java"` ladders). `_ask_version`
prints `cfg["version_tip"]` before the prompt when present, otherwise
prints nothing. Other current languages don't declare it; future
languages with similar nudges (Ruby's "use 3.x; 2.x is EOL", Python's
"3.10+ recommended") get the same plumbing for free.

### What the touchpoints look like

**README** — new "Java distributions" subsection under Configuration:

```markdown
### Java distributions

mise's java plugin defaults to Eclipse Temurin. Override by
prefixing the version string in `[languages.java].version`:

| TOML                 | Distribution                          |
| -------------------- | ------------------------------------- |
| `"21"`               | Eclipse Temurin (default)             |
| `"temurin-21.0.5"`   | Temurin, pinned build                 |
| `"corretto-21"`      | Amazon Corretto                       |
| `"zulu-21"`          | Azul Zulu                             |
| `"liberica-21"`      | BellSoft Liberica                     |
| `"graalvm-21"`       | GraalVM (incl. native-image)          |

Pick the distribution your project / employer requires; the choice
is opaque to alcatrazer — mise installs whatever the version string
asks for, and the runtime is binary-compatible across distributions
for standard Java workloads.
```

**Wizard tip** — printed by `_ask_version` when the language entry
declares a non-empty `version_tip`:

```text
  Tip: defaults to Eclipse Temurin. Prefix for alternatives,
       e.g. "corretto-21", "zulu-21", "graalvm-21".
       See README for the full list.

  Version for java: ▮
```

**Generated TOML comment** — in `_EXAMPLE_LANGUAGE_BLOCKS["java"]`,
the block landing in users' generated `coding-environment.toml`:

```toml
# [languages.java]
# version = "21"  # default: Eclipse Temurin. Prefix for alternatives:
#                 # corretto-21, zulu-21, graalvm-21, liberica-21,
#                 # or pin a build like "temurin-21.0.5".
```

The wizard's runtime example block emits `version = "21"` (plain LTS,
no prefix), which is what most users want. Distribution-conscious
users edit `coding-environment.toml` after `alcatrazer init` — same
escape-hatch contract Phase 1.1 captured ("you can edit before
`alcatrazer start` to tweak").

### Concrete code touches

- `src/alcatrazer/languages.py`
  - Add the `java` entry with `version_tip` (the new optional field)
    plus the standard fields.
  - Update the module docstring to document `version_tip` alongside
    `required_os_packages` — same shape (optional, per-language data,
    consumed by code outside this module).
- `src/alcatrazer/start.py`
  - `_ask_version` — before the version prompt, if the language's
    entry declares `version_tip` (non-empty), print it indented to
    match the prompt's two-space prefix. Languages without the field
    behave exactly as today.
  - `_EXAMPLE_LANGUAGE_BLOCKS` — add a multi-line `java` block:
    ```python
    "java": [
        "# [languages.java]",
        '# version = "21"  # default: Eclipse Temurin. Prefix for alternatives:',
        "#                 # corretto-21, zulu-21, graalvm-21, liberica-21,",
        '#                 # or pin a build like "temurin-21.0.5".',
    ],
    ```
- `src/alcatrazer/tests/test_start.py`
  - Extend `test_supported_language_set` to include `"java"`.
  - `test_java_default_manager_is_maven_with_alternatives` — asserts
    `default_manager == "maven"` and `gradle` is in `managers`.
  - `test_java_version_check_redirects_stderr` — asserts the
    `version_check` is `"java -version 2>&1"` (the `2>&1` is
    load-bearing; without it the stderr-only output is invisible to
    the chained `&&` verify block in stdout-driven log capture).
  - `test_java_has_no_required_os_packages` — keeps the "JDK is
    self-contained on Ubuntu 24.04 base" claim under test.
  - `test_java_declares_distribution_version_tip` — asserts the tip
    is non-empty and mentions Temurin + at least one alternative
    (`corretto`, `zulu`, or `graalvm`) + the word `README`.
  - `test_other_languages_have_no_version_tip` — keeps the field
    optional. The other four (python/node/rust/go/dotnet) declare no
    `version_tip`; only Java does, for now.
  - `test_ask_version_prints_tip_when_present` — patches input,
    captures stdout for `_ask_version("java")`, asserts the tip
    appears before the input prompt and the captured input still
    flows back as the chosen version.
  - `test_ask_version_prints_no_tip_when_absent` — same shape but
    for python (no `version_tip` declared); captured output contains
    no tip, only the bare prompt.
  - The existing `test_other_languages_have_no_required_os_packages`
    needs `java` added to its scope (test currently asserts
    `python/node/rust/go` are empty; with java in the whitelist it
    becomes `python/node/rust/go/java`).
- `src/alcatrazer/tests/test_docker_prison.py` — no new tests
  required; the existing `test_no_libicu74_when_dotnet_not_declared`
  (regression guard for per-language `required_os_packages`) already
  covers the "java doesn't pull libicu74" case implicitly because
  java declares no required_os_packages.
- `README.md`
  - Extend the "Supported" line (currently `python, node, rust, go,
    dotnet`) to include `java` with a parenthetical
    "JVM — Maven / Gradle".
  - Add a new "Java distributions" subsection under Configuration
    with the prefix table shown earlier.

### Verification — completed

The dotnet experience taught us that "looks fine on paper" can mask
runtime OS deps (libicu74 was invisible until `dotnet --version`
crashed). Same probe-then-merge discipline applied here, and the
result was clean. Run inside a fresh Alcatraz, as the agent user:

```bash
agent@…:/workspace$ mise use --global java@21
agent@…:/workspace$ java -version 2>&1
openjdk version "21.0.2" 2024-01-16
OpenJDK Runtime Environment (build 21.0.2+13-58)
OpenJDK 64-Bit Server VM (build 21.0.2+13-58, mixed mode, sharing)
agent@…:/workspace$ javac -version 2>&1
javac 21.0.2
```

- ✅ `mise use --global java@21` succeeded as agent (no sudo).
- ✅ JDK is self-contained — no missing-library crash, no apt
  packages needed. `required_os_packages = ()` is empirically
  correct.
- ✅ `java -version 2>&1` is the right `version_check` — output
  captured cleanly via stderr redirect.
- ✅ `javac -version 2>&1` also works (compiler bundled, no
  separate dep surface).
- ✅ Build signature `21.0.2+13-58` matches Eclipse Temurin's
  upstream build, confirming mise's default distribution
  resolution.

If a future user picks `manager = "gradle"`, the multi-manager
wizard path can be probed similarly:

```bash
mise use --global gradle
gradle --version
```

That probe is cheap and worth running before merge alongside the
test suite, but the JDK itself is the load-bearing surface and
that's confirmed.

### What Phase 1.2.2 does NOT do

To keep scope tight:

- Does **not** add a `distribution` field to the user-facing TOML
  schema. The version-string prefix (`temurin-21`, `corretto-21`,
  …) already covers it; users edit `[languages.java].version`, not
  a separate `distribution`. `schema_version` stays at `1`.
- Does **not** include `ant` or `sbt` in `managers`. Both can be
  added later if a user asks, after empirical verification that
  mise auto-installs the relevant plugins. Sticking to Maven + Gradle
  keeps Phase 1.2.2's risk surface identical to dotnet's.
- Does **not** add Kotlin or Scala — they require Java but have
  their own `SUPPORTED_LANGUAGES` rationale (asdf-only mise plugins,
  separate ecosystem decisions). Defer to Phase 2.
- Does **not** prescribe a JDK distribution. mise's default
  (Temurin) is what users get if they don't specify; that's not us
  picking a distribution, it's mise's existing convention.
- Does **not** auto-install anything beyond the JDK + selected build
  tool — the whole point is that Java fits the existing pattern. No
  new render path, no new conditional logic in `docker_prison.py`.
- Does **not** print `version_tip` for languages that don't declare
  one. Other languages get the same prompt they have today; only
  Java's prompt is extended (until/unless other languages declare a
  tip later — Ruby being the most likely candidate).

### Phase 1.2.2 independence

Independent of Phase 1.2.1 (wizard self-explanation) and Phase 1.2
(dotnet entry) — both predecessors landed already. Phase 1.2.2 is
purely additive on top:

- New optional `version_tip` field in `SUPPORTED_LANGUAGES` schema.
- One new entry (`java`) using all the existing fields plus the new
  `version_tip`.
- Two new lines in `_ask_version` to print the tip when present.
- One new multi-line block in `_EXAMPLE_LANGUAGE_BLOCKS`.
- ~7 new test methods + extending 2 existing ones for `java`.
- One new README subsection ("Java distributions") + one supported-
  list edit.

Estimated ~1 hour of code time once the empirical verification
(`mise use --global java@21` already ran clean — see "Verification"
above) is in the bag. Most of the time goes to the test methods and
README subsection, not the implementation itself.

### Phase 1.2.3 — Wizard ergonomics: reuse-prompt + every-language version_tip

Two themes — reusing existing `coding-environment.toml` on re-init,
and making `version_tip` more useful — bundled because they're both
small UX-polish items that touch the same wizard code paths.

#### Theme A — reuse-prompt for existing alcatrazer-generated config

**Today's behavior:** re-running `alcatrazer init` against a project
that already has a `coding-environment.toml` writes a NEW file with
a hex suffix (`coding-environment-a3f7.toml`), orphaning the
original. That's correct when the existing file is user-authored
(we mustn't clobber); wrong when it's alcatrazer-generated. Common
scenario: user committed their config to git, deleted `.alcatrazer/`
+ workspace, ran `init` again — they get an orphan plus a
near-duplicate.

**Fix:** detect alcatrazer-generated files via header markers, ask
the user whether to reuse, skip the languages/os/startup wizard
when they agree.

**Detection — schema-version-dependent header markers.** v1 schema
files (today's only) carry both header lines emitted by
`_render_coding_environment` via `_CODING_ENVIRONMENT_HEADER`:
- `# Coding environment definition for this repository.`
- `# Sections follow dependency order: OS -> languages -> startup.`

Both must appear at the top of the file for the file to be
recognized as v1 alcatrazer-generated. When v2 introduces
`[provision]` (deferred to Phase 2), the header text will likely
change too — so the detection logic stores markers in a dict keyed
by `schema_version`, and consults the file's declared version
before deciding. v2 just adds a new entry; the detection function
stays one place.

```python
_GENERATED_MARKERS_BY_SCHEMA: dict[int, tuple[str, ...]] = {
    1: (
        "# Coding environment definition for this repository.",
        "# Sections follow dependency order: OS -> languages -> startup.",
    ),
    # Future: 2: (...) when [provision] header text is finalized.
}
```

This explicitly captures the "markers depend on schema_version"
contract that the user flagged. Adding v2 support is a one-line
change to this dict, not a refactor of the detection logic.

**Prompt (printed mid-wizard, after the Promotion identity section
and before Languages):**

```text
Detected existing coding-environment.toml from a previous alcatrazer
install. Reuse it as-is? [Y/n]
```

- **Y / Enter** → load and use the file; skip
  `ask_coding_environment` entirely; print `Reusing
  coding-environment.toml.`
- **n** → run the languages/os/startup wizard fresh. Then before
  writing, confirm overwrite: `Overwrite existing
  coding-environment.toml? [y/N]`. If `n`, fall back to today's
  hex-suffix.

**Failure-mode analysis.** Asymmetric, in our favor:

- *False positive* (treats user-authored file as alcatrazer-generated,
  offers to reuse) — user accidentally Enters → we use their file →
  the wizard would have written their inputs anyway → no real harm.
- *False negative* (misses an alcatrazer file, falls back to hex
  suffix) — today's behavior, annoying but recoverable.

So conservative threshold (require BOTH header markers) is the
safer default.

#### Theme B — every language gets a `version_tip`

Currently only `java` declares one. Other languages ship without
version examples or default-version guidance, so users facing
`Version for dotnet:` don't know what format is expected.

**Plan: extend each entry with a `version_tip` string.** Wording
leads with "Version examples:" (per user direction):

| Lang   | Proposed `version_tip`                                                                                                                                                |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| python | `Version examples: 3.11, 3.12, 3.13. Pin a concrete release; "latest" is rejected.`                                                                                   |
| node   | `Version examples: 18, 20, 22. LTS lines (even-numbered) are recommended for production.`                                                                             |
| rust   | `Version examples: 1.75, 1.83. Pin a concrete release.`                                                                                                               |
| go     | `Version examples: 1.22, 1.23. Pin a concrete release.`                                                                                                               |
| dotnet | `Version examples: 8.0.404 (LTS), 10.0.100 (current LTS). Pin to major.minor.patch.`                                                                                  |
| java   | `Version examples: 17, 21. Defaults to Eclipse Temurin. Prefix for alternatives, e.g. "corretto-21", "zulu-21", "graalvm-21". See README for the full list.`          |

Java's existing tip ("Defaults to Eclipse Temurin…") is rewritten
to lead with version examples — same content, different ordering,
matches the rest.

#### Theme C — layout: tip BEFORE the upcoming prompt

**Today's `_ask_version`** prints the tip then a blank line then
the prompt:

```text
  Version for dotnet: 10.0.100
  Tip: …

  Version for java:
```

The tip is FOR `Version for java:`, but the blank line separates
the tip from `java:` and visually groups it with `Version for
dotnet:` instead. Wrong.

**Fix:** swap blank line position — tip groups with the *upcoming*
prompt, not the previous answer:

```text
  Version for dotnet: 10.0.100

  Tip: Version examples: 17, 21. Defaults to Eclipse Temurin…
  Version for java:
```

One-line move in `_ask_version` — the `print()` swaps from after
the tip to before it.

#### Theme D — tip lands in the generated `coding-environment.toml`

**Today's `_render_coding_environment`** emits each language's
section bare:

```toml
[languages.java]
version = "21"
```

After the change, the same `version_tip` is rendered as a comment
block above `version =`:

```toml
[languages.java]
# Version examples: 17, 21. Defaults to Eclipse Temurin. Prefix for
# alternatives, e.g. "corretto-21", "zulu-21", "graalvm-21". See README
# for the full list.
version = "21"
```

DRY: one source string in `SUPPORTED_LANGUAGES`, two consumers
(wizard prompt + TOML comment). Users editing the file later see
the same guidance the wizard gave them — no need to re-run init or
read the README.

**Bonus simplification:** today's `_EXAMPLE_LANGUAGE_BLOCKS["java"]`
hand-writes the distribution-prefix comment as a multi-line list.
Once `_render_coding_environment` auto-emits the tip as comments,
the example block goes back to the standard 2-line shape:
`# [languages.java]` + `# version = "21"`. Less duplication, the
generated comment is the single source of truth.

#### Concrete code touches

- `src/alcatrazer/languages.py`
  - Add `version_tip` field to python, node, rust, go, dotnet
    entries.
  - Rewrite java's `version_tip` to lead with "Version examples:
    17, 21." (same content as today, reordered).
- `src/alcatrazer/start.py`
  - `_ask_version` — move the `print()` blank from after the tip
    to before it.
  - `_render_coding_environment` — for each language section, render
    the language's `version_tip` as a comment block above the
    `version =` line, using `textwrap.fill` with `# ` line prefix
    and a sensible width (~76 cols).
  - `_EXAMPLE_LANGUAGE_BLOCKS["java"]` — simplify back to the
    standard 2-line shape now that the tip is auto-emitted.
  - New module-level `_GENERATED_MARKERS_BY_SCHEMA` dict (keyed by
    `schema_version`) holding the header-line markers per schema.
  - New helper `_load_existing_alcatrazer_config(project_dir) -> dict | None`
    — reads `coding-environment.toml` if present, applies the
    schema-version-aware marker check, returns the parsed dict on
    match or `None` otherwise.
  - `cmd_init` — before `ask_coding_environment`, check for an
    existing alcatrazer-generated file. If found, print the reuse
    prompt; on Y, skip the wizard and use the parsed dict; on n,
    fall through to the wizard and gate the write behind an
    overwrite-confirmation.
  - `write_coding_environment_toml` — accepts a new optional
    parameter (`overwrite_confirmed: bool = False`). If the target
    exists AND looks alcatrazer-generated AND `overwrite_confirmed`
    is False, raise / signal back so the caller knows to prompt.
- `src/alcatrazer/tests/test_start.py`:
  - Per-language: `test_python_declares_version_tip`,
    `test_node_declares_version_tip`, `test_rust_declares_version_tip`,
    `test_go_declares_version_tip`, `test_dotnet_declares_version_tip`.
    Each asserts non-empty tip starting with "Version examples:".
  - **Delete** `test_other_languages_have_no_version_tip` (now
    obsolete: every language declares one).
  - `test_java_version_tip_starts_with_version_examples` — explicit
    assertion on the rewritten leading words.
  - `test_ask_version_prints_blank_before_tip` — captures stdout,
    asserts a blank line precedes the `Tip:` line and no blank
    line separates the tip from the prompt.
  - `test_render_coding_environment_emits_version_tip_as_comment` —
    asserts each declared language's tip appears in the generated
    TOML as comment lines above its `version =`.
  - `test_render_coding_environment_wraps_long_tips` — long tips
    (java's) wrapped at the chosen width with `# ` prefix per line.
  - `test_init_reuses_existing_alcatrazer_generated_config` —
    pre-create an alcatrazer-style file, run cmd_init with patched
    input "y" for reuse, assert `ask_coding_environment` was NOT
    called.
  - `test_init_offers_overwrite_when_user_declines_reuse` — same
    setup, "n" then "y" to overwrite.
  - `test_init_falls_back_to_hex_suffix_when_user_declines_overwrite` —
    same setup, "n" then "n", asserts hex-suffix file created and
    original preserved.
  - `test_init_uses_hex_suffix_for_user_authored_config` —
    pre-create a TOML without the alcatrazer header markers, run
    cmd_init, assert hex-suffix file created and NO reuse prompt
    appeared (existing-today behavior preserved for non-alcatrazer
    files).
  - `test_generated_markers_table_has_entry_for_current_schema` —
    sanity guard: `_GENERATED_MARKERS_BY_SCHEMA[CODING_ENV_SCHEMA_VERSION]`
    exists, so a future schema bump can't silently lose detection.
- `README.md` — minor edit in the "Java distributions" subsection
  noting the wizard tip is also visible later as comments in the
  generated `coding-environment.toml`. No major rewrites — the
  table already lives there.

#### What Phase 1.2.3 does NOT do

- Does **not** pre-fill wizard answers from existing values when
  the user declines reuse. That "interactive update" feature
  requires changing every `ask_*` to accept defaults and emit
  "current value, press Enter to keep" prompts. Worth doing,
  defer to a follow-up phase.
- Does **not** change the schema (`schema_version` stays at `1`).
  Detection markers are schema-version-aware to ease the v2
  transition; until v2 ships, only v1 markers are checked.
- Does **not** add a `--no-prompt` / non-interactive mode for the
  new prompts. CI / scripted use cases need future flag work.
- Does **not** introduce a `tip` field for other prompts (managers,
  OS packages, startup commands). The pattern is generalizable but
  YAGNI for now — `version_tip` is the only one with concrete
  user-confusion evidence behind it.
- Does **not** revisit prior phases' tests. Phase 1.2.2's
  `test_other_languages_have_no_version_tip` gets deleted because
  every language now has one — that's a contract change, not a
  refactor.

#### Phase 1.2.3 independence

Independent of all earlier phases (1.1, 1.2, 1.2.1, 1.2.2 all
landed). Touches `start.py` heavily and `languages.py` lightly.
Estimated ~2 hours of code: ~30 min for tip extension across
languages + layout fix, ~30 min for tip-as-TOML-comment rendering,
~1 hour for the reuse-prompt logic + tests.

### Phase 1.2.4 — Manager always written; bundled vs installable; `manager_tip`

Surfaced by a real-user observation while testing Java in Alcatraz:
the wizard accepted `[maven]` as default, but the generated
`coding-environment.toml` had no `manager =` line and the image
shipped without Maven. Three intertwined defects all stem from one
data-model conflation:

1. **TOML omits `manager`** when the user accepts the default.
   Result: the file is silent about a meaningful choice, so users
   editing later have no anchor to switch managers without re-init
   or reading docs.
2. **`default_manager` conflates "wizard hint" with "what mise
   installs".** `_render_mise_uses` only emits `mise use --global
   <manager>` when the user *overrode* the default. This worked for
   python/node/rust/go/dotnet by accident — their default managers
   ship with the runtime (pip with Python, npm with Node, cargo with
   Rust, dotnet IS the runtime). Java is the first language where
   the default manager (Maven) is *not* bundled with the runtime, so
   accepting the default leaves the user without their build tool.
3. **No wizard tip for the manager prompt** (parallel gap to
   `version_tip` before Phase 1.2.3). Users see
   `Package manager for X? [maven] / gradle:` with no inline guide
   on what each option means or how it'd land in their image.

Fix: separate the three concepts cleanly, and apply the same
self-documentation pattern Phase 1.2.3 used for `version_tip`.

#### Theme A — always emit `manager =` in generated TOML

Today's `_render_coding_environment` skips the line unless `manager`
is in the dict. Phase 1.2.4 always emits it, with the resolved value
(user-picked or language default). Symmetric with `version`: required
field, always shown, never silently defaulted.

```toml
[languages.java]
# Version examples: 17, 21. Defaults to Eclipse Temurin. Prefix for
# alternatives, e.g. "corretto-21", "zulu-21", "graalvm-21". See README
# for the full list.
version = "21"
# Default: maven. Alternative: gradle. Both auto-install via mise.
manager = "maven"
```

Self-documenting at the line a user would edit, no need to re-run
init or read README. Same DRY principle as `version_tip`: one source
in `SUPPORTED_LANGUAGES`, two consumers (wizard prompt + TOML
comment).

#### Theme B — separate "default" from "bundled"

Add a `bundled_managers: tuple[str, ...]` field on every
`SUPPORTED_LANGUAGES` entry. It captures the install-time semantics
that `default_manager` previously over-served:

| Lang | default_manager | bundled_managers | What changes? |
| ---- | --------------- | ---------------- | ------------- |
| python | pip | `("pip",)` | pip stays bundled with CPython; not installed by mise. uv / poetry / pipenv install via mise when picked. |
| node | npm | `("npm",)` | npm stays bundled with Node. pnpm / yarn install via mise when picked. |
| rust | cargo | `("cargo",)` | cargo stays bundled with rustc. |
| go | go | `("go",)` | the manager IS the runtime. |
| dotnet | dotnet | `("dotnet",)` | the manager IS the runtime. |
| **java** | **maven** | **`()`** | **NOTHING is bundled — mise installs whatever's picked, including default `maven`**. |

`_render_mise_uses` becomes:

```python
manager = cfg.get("manager") or SUPPORTED_LANGUAGES[lang]["default_manager"]
if manager not in SUPPORTED_LANGUAGES[lang]["bundled_managers"]:
    uses.append(manager)
```

Two-line change, exact behavior we want:
- Java + no `manager` declared → mise installs `maven`. ✓
- Python + no `manager` → `pip` is bundled, mise skips. ✓
- Python + `manager = "uv"` → uv not bundled, mise installs. ✓
- Java + `manager = "gradle"` → gradle not bundled, mise installs. ✓

#### Theme C — `manager_tip` parallel to `version_tip`

Add an optional `manager_tip: str` field. Wizard prints it before
the manager prompt (when there are multiple managers — single-manager
languages skip the prompt entirely, but the tip still ends up in the
TOML as a comment). `_render_coding_environment` emits it as a
comment block above the `manager =` line.

Proposed wording (per-language):

| Lang | Proposed `manager_tip` |
| --- | --- |
| python | `Default: pip (bundled with Python). Alternatives: uv (fast, Rust-based), poetry, pipenv. Non-default options install via mise.` |
| node | `Default: npm (bundled with Node). Alternatives: pnpm (fast, disk-efficient), yarn. Non-default options install via mise.` |
| rust | `Default: cargo (bundled with the Rust toolchain). Single canonical manager.` |
| go | `Default: go (the manager is the runtime). Single canonical manager.` |
| dotnet | `Default: dotnet (the SDK CLI; uses NuGet under the hood). Single canonical manager.` |
| java | `Default: maven. Alternative: gradle. Both auto-install via mise.` |

Single-manager languages still get a tip — useful as a self-
documenting comment in the TOML even though no prompt fires.

#### Concrete code touches

- `src/alcatrazer/languages.py`
  - Every entry gains `bundled_managers: tuple[str, ...]` (empty
    tuple where nothing is bundled — currently only java).
  - Every entry gains `manager_tip: str` (single-line nudge).
  - Module docstring documents both new fields.
- `src/alcatrazer/start.py`
  - `_ask_manager` always returns the resolved manager string
    (never `None`). Single-manager case returns the lone option;
    multi-manager case returns the user's pick (or default if Enter).
  - `_ask_manager` prints `manager_tip` before the prompt (when
    multi-manager). For single-manager languages the tip is silent
    in the wizard but still lands in the TOML as a comment.
  - `ask_languages` always stores `manager` in the entry dict.
  - `_render_coding_environment`:
    - Always emit `manager =` line (no more `if "manager" in cfg`).
    - Emit `manager_tip` as `# `-prefixed comment block above the
      `manager =` line, parallel to how `version_tip` lands above
      `version =`.
- `src/alcatrazer/docker_prison.py`
  - `_render_mise_uses` resolves the manager (user pick or default)
    and installs it iff it's NOT in `bundled_managers`. Two-line
    change inside the existing per-language loop.
- `src/alcatrazer/tests/test_start.py`
  - Per-language: `test_<lang>_declares_bundled_managers`,
    `test_<lang>_declares_manager_tip`.
  - `test_python_pip_is_bundled_other_managers_are_not` — explicit
    contract: `bundled_managers == ("pip",)` and
    `"uv" not in bundled_managers`.
  - `test_java_has_empty_bundled_managers` — explicit contract.
  - `test_ask_manager_always_returns_resolved_value` — single-manager
    case returns the manager (not None); default-accepted case
    returns the default name.
  - `test_ask_manager_prints_tip_before_multi_manager_prompt` —
    `manager_tip` shows in wizard for python/node/java, with blank
    BEFORE (same layout convention as `version_tip`).
  - `test_render_coding_environment_always_emits_manager_line` —
    every declared language has `manager = "X"` in the generated
    TOML, regardless of whether user picked a non-default value.
  - `test_render_coding_environment_emits_manager_tip_as_comment` —
    same DRY pattern as `version_tip` rendering.
- `src/alcatrazer/tests/test_docker_prison.py`
  - `test_default_manager_for_java_installs_via_mise` — Java with
    no `manager` declared → rendered Dockerfile contains
    `mise use --global maven`. Closes the user-reported bug.
  - `test_default_manager_for_python_does_not_install_via_mise` —
    Python with no `manager` declared → rendered Dockerfile does
    NOT contain `mise use --global pip` (pip is bundled).
  - `test_user_picked_manager_installs_via_mise` — overriding still
    works (e.g. `manager = "uv"` for python emits the install line).
  - `test_bundled_manager_skipped_even_when_explicitly_chosen` —
    user explicitly types `manager = "pip"` for python → still
    skipped (it's bundled either way).
- Existing wizard tests need updating: today's tests assert that
  accepting the default omits `manager` from the result dict. Under
  Phase 1.2.4 the `manager` key is always present. Affected tests:
  `test_single_language_default_manager_omits_field`,
  `test_explicit_default_manager_name_still_omits_field`,
  `test_rust_single_manager_skips_manager_prompt`,
  `test_dotnet_single_manager_skips_manager_prompt`,
  `test_java_manager_prompt_offers_maven_default_and_gradle`. Each
  needs its expected dict updated to include the resolved manager.
- README minor update: "Java distributions" subsection notes that
  Maven now installs by default; add a brief note in the
  "Configuration" example that `manager` is always written.

#### What Phase 1.2.4 does NOT do

- Does **not** introduce a separate `manager_version` field. mise
  picks a sensible recent version when you say `mise use --global
  maven`. Pinning specific manager versions can be a follow-up if a
  user ever asks (today nobody has).
- Does **not** change the user-facing TOML schema. `schema_version`
  stays at `1`. The new fields (`bundled_managers`, `manager_tip`)
  live in Python `SUPPORTED_LANGUAGES`, not in user-edited TOML.
- Does **not** revisit Phase 1.2.3's `version_tip` plumbing. The two
  features are intentionally parallel — same wrapping, same
  comment-emit shape, same blank-line layout — but each ship
  independently.
- Does **not** install ant or sbt. Those stay deferred until a user
  asks.

#### Phase 1.2.4 independence

Independent of all earlier phases (1.1, 1.2, 1.2.1, 1.2.2, 1.2.3
all landed). Touches `languages.py` (data), `start.py` (wizard +
TOML render), `docker_prison.py` (install render), tests in two
files. Estimated ~1.5 hours: ~30 min for the language-data extension
+ tests, ~30 min for `_ask_manager` + `_render_coding_environment`
changes + tests, ~30 min for `_render_mise_uses` change + tests +
existing-test updates.

### Independence and ordering

Phase 1.1 (schema versioning) and Phase 1.2 (C# entry) don't depend on
each other and can land as two separate commits or one bundled PR.

Recommended order if separated: **schema versioning first**, so the
C# wizard-generated config naturally includes `schema_version = 1`
from the moment it ships. If bundled in one PR, no ordering question.

### What Phase 1 does NOT do

To keep scope tight:

- Does **not** introduce `[provision]` in `coding-environment.toml`.
  That's v2.
- Does **not** drop the `SUPPORTED_LANGUAGES` whitelist. The whitelist
  is still a gate in v1 — `required_os_packages` is a new *internal*
  field on existing entries, not a new user-facing schema concept.
- Does **not** change Dockerfile *structure* (still 3 stages, still
  apt-install + mise + verify). The apt-install RUN's *source list* is
  enriched (user `[os].packages` ∪ per-language `required_os_packages`),
  which is a small data-driven change inside `_render_apt_install`,
  not an architectural one.
- Does **not** change the user-facing TOML schema. `schema_version`
  stays at `1`; users still write `[os].packages` and `[languages.*]`
  the same way they do today. The new field lives in Python code, not
  in user-edited TOML.
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
2. **C# / .NET.** Add `dotnet` entry to `SUPPORTED_LANGUAGES` with
   `required_os_packages = ("libicu74",)`. Extend the apt-install
   renderer in `docker_prison.py` to merge per-language deps with
   user-declared `[os].packages` (first-occurrence dedupe, user list
   first). Tests on both the languages whitelist (entry shape) and
   the Dockerfile generator (libicu74 appears iff dotnet declared).
   README + wizard example. mise plugin model already verified —
   `dotnet` is core, no `requires_plugin_install` flag needed.
3. **Wizard self-explanation (Phase 1.2.1).** Intro panel with ASCII
   diagram, section banners + 2–4 line context per `ask_*`, closing
   messages rewritten in Alcatraz vocabulary. No structural changes;
   no new flags or parsing.
4. **Java (Phase 1.2.2).** Add `java` entry to `SUPPORTED_LANGUAGES`
   with `default_manager = "maven"`, `managers = ("maven", "gradle")`,
   `version_check = "java -version 2>&1"`, `required_os_packages = ()`.
   Wizard suggests Java 21 LTS. Distribution choice (Temurin /
   Corretto / Zulu / GraalVM) handled by free-form mise version
   string — no schema field. Bun and Deno deliberately NOT added —
   `[startup].commands` is the canonical path for languages that
   need no root-time install.
5. **Wizard ergonomics (Phase 1.2.3).** Reuse-prompt for existing
   alcatrazer-generated `coding-environment.toml` (schema-version-
   aware header detection, Y reuses + skips wizard / n offers
   overwrite + falls back to hex suffix). Every language gets a
   `version_tip` (leading "Version examples: …") covering format +
   default. Layout fix: blank line moves to BEFORE the tip so it
   visually groups with the upcoming prompt. Tip is also emitted
   as comments above each `version =` in the generated TOML, so
   the same guidance reaches users editing the file later.
6. **Manager always written; bundled vs installable; `manager_tip`
   (Phase 1.2.4).** `_render_coding_environment` always emits
   `manager =` (resolved value, default or override) — symmetric
   with `version`. New `bundled_managers` field separates "default"
   from "ships with runtime"; `_render_mise_uses` installs the
   manager iff it's not bundled (fixes the user-reported bug where
   accepting Java's default `[maven]` left Maven uninstalled). New
   `manager_tip` field surfaces in the wizard before the manager
   prompt and as a TOML comment above `manager =`, parallel to
   `version_tip`. Existing wizard tests' "default omits field"
   expectations updated.

**Phase 2 — v2 refactor (deferred):**

7. Validate the empty-stage-3 path on the current container — confirm
   `apt-get` works at provision time and `curl | bash` works as agent.
8. Sketch the `Alcatraz` port interface under the two-method contract
   (`provision`, `start`). Verify `DockerPrison` can implement it
   without regression. Sketch a hypothetical `FirecrackerPrison` for
   the same methods — surface any axis we missed.
9. Implement `[provision]` as a v2 TOML section. Bump
   `schema_version` to `2`. **NOTE:** the
   `_GENERATED_MARKERS_BY_SCHEMA` dict from Phase 1.2.3 needs a v2
   entry here so `init`'s reuse-prompt detection keeps working
   across the v1→v2 transition. Header text emitted by v2's
   `_render_coding_environment` is what the v2 marker tuple should
   match.
10. Make existing `[os].packages` and `[languages.*]` desugar into
    `[provision]` lists in the config loader (still under v1 schema for
    backcompat; v2 schema decides whether to keep or remove the sugar).
11. Demote `SUPPORTED_LANGUAGES` from gate to suggestion (rename to
    `WIZARD_SUGGESTIONS` or similar).
12. Kotlin / Scala / Erlang+Elixir / PHP / Lua / Zig / Dart and the
    other Tier B/C/D languages added cleanly under v2 — either as raw
    `[provision]` bash, or as suggestions (curated wizard menu, no
    longer a gate). Distribution-conscious Java users who want SDKMAN!
    or jenv instead of mise can also do it via raw `[provision]`.
13. Documentation pass — README "Supported languages" section becomes
    "Curated convenience languages — and how to add anything else."

## Open questions for the next pass

- **Phase 1 — mise plugin model for dotnet.** Resolved: `dotnet` is a
  core mise plugin, no `requires_plugin_install` flag needed.
- **Phase 1 — runtime OS deps for languages.** Resolved by introducing
  `required_os_packages` on `SUPPORTED_LANGUAGES` entries; populated
  for `dotnet` (libicu74), empty for everything else for now. Java
  will likely add to this when introduced under v2.
- **Agent user privilege escalation at runtime.** Resolved: the agent
  user has **no sudo** inside Alcatraz, by design. Sudo is attack
  surface; an agent compromise must not escalate to root. Anything
  that needs root (apt-get, system-wide installs) must be baked at
  image build time, where the Dockerfile `RUN` already executes as
  root. The v2 `[provision].root_commands` proposal is consistent
  with this — provision is build time, not runtime. For one-off
  debugging from outside, `docker exec -u root` is the supported
  escape hatch and grants no standing privilege to the agent user.
- **Phase 2 — `[provision]` per-step user selection.** Should
  `[provision]` allow per-step `user` selection (per-command root vs
  agent) instead of two separate lists? Two lists is simpler;
  per-step is more flexible. Lean toward two lists until a real use
  case forces per-step.
- **Phase 2 — mise as default sugar.** Keep or remove from `dev-base`?
  Leaning keep — small, harmless, makes the curated path zero-friction.
