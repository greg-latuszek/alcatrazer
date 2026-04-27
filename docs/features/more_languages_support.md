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

## What this means for C# / Java specifically

The right answer is no longer "add entries to `SUPPORTED_LANGUAGES`."
It's:

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
   `$HOME` correctly. Both should be straightforward, but should be
   exercised end-to-end before we commit to the contract.

## Recommended next steps (sequenced)

1. **Validate the empty-stage-3 path today.** With current code: init
   accepting empty `[languages]`, start, exec in, attempt `apt-get
   install openjdk-21-jdk` (likely needs root — check entrypoint posture)
   and `curl -s https://get.sdkman.io | bash` (as agent). This tells us
   whether the new contract requires entrypoint changes or works on top
   of what's there.
2. **Sketch the `Alcatraz` port interface** under the two-method contract
   (`provision`, `start`). Verify `DockerPrison` can implement it without
   regression. Sketch what a hypothetical `FirecrackerPrison` would do
   for the same methods — surface any axis we missed.
3. **Implement `[provision]` as a TOML section.** Wire it through the
   config loader, then through `DockerPrison._render_dockerfile` so that
   `[provision].root_commands` and `[provision].user_commands` emit
   `USER root` / `USER agent` RUN blocks in stage 3.
4. **Make existing `[os].packages` and `[languages.*]` desugar into
   `[provision]` lists** in the config loader. Backend code stops seeing
   the sugar. Tests assert the desugaring (independent of backend).
5. **Demote `SUPPORTED_LANGUAGES` from gate to suggestion.** Rename,
   stop validating user input against it, keep it as the wizard's
   curated menu.
6. **Documentation pass.** README "Supported languages" section becomes
   "Curated convenience languages — and how to add anything else."

Steps 1-2 are research; 3-5 are the implementation; 6 closes it.

## Open questions for the next pass

- Does the entrypoint leave the agent user with usable `sudo`, or is the
  expectation that all root work happens at provision time and runtime
  has no privilege escalation? (Probably the latter, by design — but
  needs to be stated explicitly in the contract.)
- Should `[provision]` allow per-step `user` selection (per-command root
  vs agent) instead of two separate lists? Two lists is simpler; per-
  step is more flexible. Lean toward two lists until a real use case
  forces per-step.
- Mise as default sugar: keep or remove? (Leaning keep — see caveat
  above.)
- Versioning of the `coding-environment.toml` schema: do we need a
  `schema_version` field now that we're adding `[provision]`? Probably
  yes, even if we don't enforce it for v1.
