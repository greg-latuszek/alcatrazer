# Alcatrazer

*Your code gets out. Your secrets don't.*

<p align="center">
  <img src="https://raw.githubusercontent.com/greg-latuszek/alcatrazer/main/images/alcatraz.jpg" alt="Alcatraz Island" width="700">
  <br>
  <sub>Photo: Javier Branas — <a href="https://commons.wikimedia.org/wiki/File:Alcatraz_-_panoramio.jpg">Wikimedia Commons</a> — <a href="https://creativecommons.org/licenses/by/3.0/">CC BY 3.0</a></sub>
</p>

---

> *The year is 2026. AI agents have become the fastest coders on the planet. They write, test, refactor, and ship — tirelessly, in parallel, around the clock.*
>
> *There's just one problem: you gave them the keys to your machine.*
>
> *Your SSH keys. Your git credentials. Your browser sessions. That `.env` file with production database passwords. The tax return PDF you forgot in your Downloads folder. All of it — one careless mount, one leaked environment variable, one hallucinated `curl` command away from somewhere it should never be.*
>
> *You didn't mean to. Nobody does. You just wanted the agent to scaffold a FastAPI backend. But it runs as you. It sees what you see. And when it phones home to its LLM, it sends whatever context it thinks is relevant.*
>
> *Alcatraz was built for a different kind of prisoner. The kind that works hard, produces valuable output, and never — ever — gets to touch the mainland.*
>
> *The island is a sandboxed env. The inmates are your AI agents. They get a workspace, tools, and internet access to talk to their LLM. They write code, create branches, run tests, commit their work. They can even orchestrate swarms of sub-agents, each on their own branch, merging results like a well-run development team.*
>
> *But the water around the island is real. No SSH keys exist inside. No git credentials. No host filesystem. The agents don't even know your name — they commit under a randomly generated ghost identity that maps to nobody real. They run under a phantom UID that doesn't exist on your machine, so even if they tunnel through the walls, they surface as nobody, owning nothing, permitted nowhere.*
>
> *Every commit (and only commits) crosses the water, but the ghost identity is replaced with yours. When the work is done, you — the warden — inspect it from the mainland. You review the commits, the branches. If you approve, you run the transfer:* **you push to GitHub**
>
> *Your agents built it. You own it. And your secrets never left the mainland.*
>
> ***Alcatrazer** — the tool that builds your Alcatraz.*

---

## Why Alcatrazer Exists

Watch out developers community. Your paradigm has changed. You trust yourself - that is understood. And that was you who have coded inside container up till now. But the world has changed. It is no more you who is coding inside container. These are AI agents that might do harmful things due to hallucination or prompt injecting via accidental download of malicious software. Harmful both - to your localhost and to your public git repository. So, don't trust them - put them in Alcatraz.

### What Alcatrazer protects

**Your development environment.** Your SSH keys, GPG keys, git credentials, browser sessions, `.env` files, personal documents — everything on your localhost that agents have no business touching.

**Your professional reputation.** Your public repositories carry your name. Other developers pull from them, depend on them, trust them. Unsupervised AI coding can inject trojans, backdoors, or other malware into YOUR repositories — code that others may pull and be harmed by. It is our responsibility as software engineers to review, test, and security-scan AI-created code before we publish it under our name. No AI writing to your repo without your knowledge. Alcatrazer enforces this: agents commit to an isolated inner repo, and nothing reaches your real repository until you — the warden — inspect and approve the transfer.

**The threat is concrete.** Two recent supply-chain attacks share the same pattern: a compromised npm package uses the developer's local credentials to create public GitHub repositories *under the developer's own account*. **Nx `s1ngularity`** (August 2025) hijacked the victim's `gh` authentication to upload harvested credentials into ~1,400 new public repos named `s1ngularity-repository-*`, each under a different victim's account — 2,349 credentials from 1,079 developers. **Shai-Hulud** (September & November 2025) used stolen tokens to create public repos under victims' accounts *and* backdoor every package those developers maintained (796 packages, 20M+ weekly downloads in the November wave). Inside Alcatrazer, an agent has no GitHub credentials, no SSH keys, no `gh` authentication, no knowledge of your account, and no path to your real repository. A compromised package can poison the agent's workspace; it cannot create repositories under your name or push to GitHub on your behalf. Every commit crosses the water only after you review it.

---
## Using it
**You have to be in root of your repository** (where .git/ resides), otherwise following commands won't work.

After [installation (or even without permanent installation)](1-set-up-the-cli) you can call:
```bash
alcatrazer test                  # Run bundled tests to verify installation
alcatrazer init                  # Answer few questions to configure tool
alcatrazer start --run-selftest  # start Alcatraz for AI agents (with security invariants check)
alcatrazer visit                 # visit Alcatraz and tell agents what to do

alcatrazer --help                # see all other possibilities
# uvx alcatrazer ...               to run it without permanent installation
```

---

## CAUTION

| | |
|:---:|:---|
| <img src="https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExcjJqbTZ5NHZnZ2IybThnbWg2NXpvMDJrYW1sZmpwNnI3NnIwcjhnZSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/lTOmZHdG7ycHGRdlMF/giphy.gif" alt="Under Construction" width="120"> | **This project is under active construction.** File layout, APIs, naming, and the overall architecture may change without notice. The core security model works and is tested, but the tooling is not yet packaged for distribution. Use at your own risk — and contributions are welcome. |

---

## Releases 0.1.1 — operable

- v0.1.1 is first operable release with the promotion machinery bug fixed.
 
The promotion daemon no longer rewrites your branch's history — it appends agent commits as
fast-forwards, so the working tree never drifts out of sync with `HEAD`, and it handles
file-presence collisions gracefully by pausing and waiting for user resolution rather than
erroring out.

> **Read the [CHANGELOG](https://github.com/greg-latuszek/alcatrazer/blob/main/CHANGELOG.md) before installing any release.** Per-release "what's new" / "what's broken" notes live there.

---

## Purpose

Alcatrazer is a secure development environment for AI-powered coding agents. 
It isolates agent work inside Docker containers (now, VMs - future), 
protecting your host machine from accidental or intentional credential leakage, while letting agents do their job: write code, commit, branch, merge, and talk to LLMs.

It is designed to drop into any existing git repo — install the CLI, run `alcatrazer init`, then `alcatrazer start`, and start experimenting with any agentic framework (Claude Code, os-eco, custom agent swarms, etc.) in any language you've declared in `coding-environment.toml`.

## What Alcatrazer Commits To

These are the design commitments that define Alcatrazer. They are properties of the tool, not promises about features we plan to add.

- **Agent commits land in your repo, on your starting branch, under your name — automatically.** No manual cherry-pick step. The promotion daemon transfers commits in the background and rewrites the agent's identity to yours on the way out.
- **Hold rather than overwrite when your in-flight edits would collide with agent work.** Agent work is held until the safe condition returns, then resumes automatically. Your work is never lost to an automated process.
- **Random fictitious identity for agent commits inside the workspace** — generated fresh per bring-up; never your name or email. The outer repo only sees commits attributed to you, after the rewrite.
- **The agent inside the workspace cannot detect that Alcatrazer is the surrounding tool.** Mount paths, environment variables, container hostname, file contents, and commit metadata all read as a generic working environment.
- **A bundled verification suite proves the security model on your own machine.** `alcatrazer start --run-selftest` runs the same assertions the project's CI does. We don't ask you to trust the marketing; we hand you the test.
- **Zero third-party runtime dependencies in the core.** The trust boundary is the language standard library plus our own source — full stop. The audit surface stays small enough to read in an afternoon.
- **Apache-2.0 licensed, source readable.** See the [License](#license) section below for details and the dependency-graph compatibility analysis.

## Repository Structure

Alcatrazer ships as a single Python package. Once installed into a
target repo it creates a nested git architecture — a workspace repo
inside your repo:

```
your_repo/                              <-- outer repo (your identity, has GitHub remote)
├── .git/                               <-- outer git
├── coding-environment.toml             <-- agent-visible recipe (committed, zero branding)
├── .env.example                        <-- committed template for API keys
├── .env                                <-- gitignored, real secrets
├── README.md
├── .alcatrazer/                        <-- gitignored via .git/info/exclude; tool state + installed source
│   ├── src/alcatrazer/                 <-- extracted package source incl. schemas.json (readable install, bundled tests)
│   ├── Dockerfile                      <-- generated Alcatraz recipe (image build input)
│   ├── entrypoint.sh                   <-- generated container entrypoint (chown, drop via gosu)
│   ├── python -> /usr/bin/python3      <-- symlink to the Python that was used to install
│   ├── config.toml                     <-- per-developer config (schema_version, identity, daemon settings)
│   ├── coding-environment.toml.last    <-- recipe snapshot at last build (stale-image detection)
│   ├── uid                             <-- phantom UID
│   ├── agent-identity                  <-- random agent name + email
│   ├── workspace-dir                   <-- pointer to the workspace directory name
│   ├── state.json                      <-- running state of Alcatrazer (pin + replay + paused);
│   │                                       created during first `alcatrazer start`, gone after `clear`
│   ├── promotion-daemon.pid            <-- daemon PID (single-instance guard)
│   └── promotion-daemon.log            <-- daemon activity log
└── .<workspace>-<random>/              <-- gitignored via .git/info/exclude, randomly named (e.g., .devspace-7f3a/)
    ├── .git/                           <-- inner git (random agent identity, no remote)
    └── ... agent work ...
```

The package itself (inside the wheel, and extracted into
`.alcatrazer/src/alcatrazer/`):

```
alcatrazer/
├── cli.py                              <-- entry point: init / start / visit / stop / clear / status / test
├── start.py                            <-- cmd_init / cmd_start / cmd_visit / cmd_stop / cmd_clear / cmd_selftest
├── alcatraz.py                         <-- Alcatraz port (backend-agnostic interface)
├── docker_prison.py                    <-- Docker adapter of the Alcatraz port
├── snapshot.py                         <-- flat snapshot from outer repo into the workspace
├── promote.py                          <-- replay agent commits onto your branch via git format-patch | git am
├── git_runner.py                       <-- central git command runner (safety funnel for all git subprocess calls)
├── daemon.py                           <-- sync daemon (polls from the host side)
├── daemon_lifecycle.py                 <-- launch / shutdown wiring for start / stop / clear
├── identity.py                         <-- random agent identity + workspace dir generation
├── languages.py                        <-- declared runtimes → Dockerfile fragments
├── selftest.py                         <-- bundled security self-tests (phantom UID, etc.)
├── state.py                            <-- .alcatrazer/state.json reader + schema-compatibility gate
├── schema.py                           <-- loader for schemas.json (schema history, version constants)
├── schemas.json                        <-- schema history for state.json + config.toml + coding-environment.toml
├── status.py                           <-- cmd_status implementation
├── container/entrypoint.sh             <-- container entrypoint (chown, drop via gosu)
├── scripts/                            <-- bash bootstrap (runs before Python exists)
├── templates/                          <-- coding-environment.toml + .env.example templates
├── tests/                              <-- unit + non-Docker integration tests
└── integration_tests/                  <-- Docker smoke tests (requires a real daemon)
```

- The **outer repo** is the host-side control plane: it receives
  promoted agent work, has your real identity, and owns the GitHub
  remote.
- The **inner repo** (`.<workspace>-<random>/`) is the agent workspace.
  Randomly named (e.g. `.devspace-7f3a/`, `.sandbox-2c91/`). This directory is the only
  thing mounted into Docker — the generic name prevents leaking
  "alcatrazer" via `/proc/self/mountinfo`.
- **Tool state and installed source** live in `.alcatrazer/` — never
  mounted into Docker, invisible to agents.
- **Bootstrap bash** (`scripts/`) exists to get Python running on the
  host. Everything else is Python, stdlib only, no third-party
  dependencies.
- **Ignore patterns** for `.alcatrazer/` and the workspace directory
  are written to `.git/info/exclude`, not `.gitignore` — so the
  exclusions themselves don't enter the agent's workspace snapshot.

## Security Model

### Container Isolation

The container runs as a **phantom UID** — a user ID that does not exist on the host machine. This provides defense in depth: even if an agent escapes the container, the process cannot write to any host files because no host user matches that UID.

The phantom UID is determined automatically during `alcatrazer init` — a bash helper scans the host for the first unused UID starting from 1001 and stores it in `.alcatrazer/uid` for reuse across container rebuilds.

### What we protect against

Alcatraz protects **host filesystem integrity**. The threat model is an agent (intentionally or accidentally) reading local secrets, PII, or credentials and exfiltrating them over the network. Alcatraz prevents this by ensuring agents have no access to host files outside the mounted workspace.

Agents **are expected** to talk to LLM APIs — that's their job. Claude OAuth credentials are mounted read-only so agents can use your existing Claude subscription.

### What agents CAN do

- Read and write files inside the workspace (mounted into the container as `/workspace`)
- Create git commits using a randomly generated throwaway identity (different per init)
- Create branches, merge branches, and build complex branch/merge histories
- Access the internet to communicate with LLM APIs via Claude OAuth or API keys
- Install packages and run code inside the container
- Use mise to manage tool versions (Python, Node.js, Bun, etc.)

### What agents CANNOT do

- Push to GitHub or any remote repository (no git credentials or SSH keys are available)
- Access the host user's identity, email, or signing keys
- Access the host filesystem outside of the mounted workspace
- Access the Docker socket or spawn new containers
- Read host files (SSH keys, GPG keys, git config, shell history, environment variables, etc.)
- Write to host-owned files even if container escape occurs (phantom UID has no host permissions)
- Delete or modify files outside the mounted workspace

## Branch handling

Alcatrazer slots into the normal feature-branch workflow with no new
ceremony:

1. `git checkout -b feat/X` — branch off `main` as you normally would.
2. `alcatrazer start` — Alcatrazer remembers the branch you started
   on and uses its tree as the agent's starting point.
3. Agents work inside the workspace and commit. Each agent commit
   replays onto outer `feat/X` as your own — the working tree and
   branch ref update together, in the same instant the file appears
   on disk.
4. `git push origin feat/X` and open a PR like any other.

```
outer  feat/X  (checked out) ──── snapshot ────▶  workspace  main  ("Initial commit")
                                                       │
                                                       │  agents code, commit,
                                                       │  branch, merge
                                                       ▼
                                          workspace  main + new commits
                                                       │
                                                       │  Alcatrazer replays each
                                                       │  commit onto outer feat/X
                                                       ▼
outer  feat/X  + new commits (appended under your identity, working tree in sync)
```

Alcatrazer is bound to the branch you started on. That binding is
fixed for the workspace's lifetime:

- **You stay on `feat/X`** → agent commits appear there as you work
  alongside, working tree stays clean and current.
- **You switch to a different branch** (e.g. `git checkout main` to
  check something) → Alcatrazer **holds**: agents keep committing
  inside the workspace, but nothing replays to outer until you return
  to `feat/X`. On your next `git checkout feat/X` Alcatrazer replays
  everything that piled up, in order.
- **You delete `feat/X`** → Alcatrazer holds with a message explaining
  how to recreate the branch (or how to discard pending agent work via
  `alcatrazer clear --discard-pending`).

Run `alcatrazer status` to see which state you're in, how many agent
commits are pending, and when the last replay happened.

### When outer already has a conflicting file

Outer's working tree gets the same atomic update as `feat/X`'s ref —
there's no window where the branch says "this file exists" and the
file isn't on disk yet (or vice versa). The one situation that pauses
the replay is **file-presence collision**: outer already has a file at
the same path the agent's commit adds. Two sub-cases, each with its
own resolution:

- **Outer's file is local-only** (untracked, or modified-but-not-yet-
  committed) — `git rm` the path, or copy it aside under another name
  first if you want to keep your version. For a modified-but-tracked
  file you can `git stash` instead.
- **Outer's file is already committed** — `git rm <path> && git commit
  -m "make way for agent work"`. If you want your outer-side version
  preserved, copy the file to a non-conflicting name before the `rm`.

Either way, `alcatrazer status` shows a paused message naming the
branch and the next step. Once the collision is gone, the next replay
cycle picks up where it left off — no daemon restart needed.

### Two ways to pause

| Need | Command pair | Effect on the pin |
|---|---|---|
| Pause and pick this workspace back up later | `alcatrazer stop` → `alcatrazer start` | Same workspace, same pin. Freeze-restart. |
| Done with this branch's agent work; want to start fresh on a different branch | `alcatrazer clear` → `git checkout <new>` → `alcatrazer start` | Terminal teardown + fresh first-run. New pin. |

Both `stop` and `clear` wait for the daemon to drain pending agent
commits to outer before exiting, so no work is lost. `clear` also
removes the inner workspace and the pin so the next `start` snapshots
from your currently-checked-out branch (your identity and daemon
settings carry over — no `alcatrazer init` needed in between).

## Getting Started

### 1. Set up the CLI

Alcatrazer is a pure-Python package (stdlib only, Python 3.11+).
There are two ways to run it, and the choice changes how you'll type
every subsequent command:

| Mode | How you invoke it | `which alcatrazer` | Typical when |
|---|---|---|---|
| **A. Ephemeral** | `uvx alcatrazer <cmd>` (or `pipx run alcatrazer <cmd>`) | *empty* — by design | Trying it out; you don't want anything persistent on `PATH` |
| **B. Persistent install** | `alcatrazer <cmd>` | `~/.local/bin/alcatrazer` | You'll use it regularly |

Both modes converge on the exact same package — the only difference is whether the `alcatrazer` executable lives on your `PATH`.

**Mode A — ephemeral (recommended for first try):**

```bash
uvx alcatrazer init              # or: pipx run alcatrazer init
uvx alcatrazer start             # …prefix every command with `uvx `
uvx alcatrazer stop

which alcatrazer                 # prove ephemeral
alcatrazer not found
```

Each call spins up (or reuses the cached) throwaway venv under `~/.cache/uv/`. **`which alcatrazer` stays empty — that is expected, not broken.** Nothing to uninstall later; the cache is GC'd automatically, or you can force it with `uv cache clean alcatrazer`.

**Mode B — persistent install:**

```bash
uv tool install alcatrazer       # or: pipx install alcatrazer
which alcatrazer                 # → ~/.local/bin/alcatrazer
alcatrazer --version
```

Then call `alcatrazer <cmd>` directly, as the examples below do.

- Upgrade:  
  - `uv tool upgrade alcatrazer` or
  - `pipx upgrade alcatrazer`)
- Uninstall: 
  - `uv tool uninstall alcatrazer` or
  - `uv pip uninstall alcatrazer` or
  - `pipx uninstall alcatrazer`

> The rest of this README uses the short `alcatrazer <cmd>` form. If
> you're in Mode A, prefix every call with `uvx ` (or `pipx run `).
> The behavior is identical.

### 2. Initialize Alcatrazer in your repo

From the root of the git repository you want to protect:

```bash
alcatrazer init
```

One-time interactive setup. It asks a few questions (promotion
identity, languages, OS packages, startup commands) and writes:

- `coding-environment.toml` — committed, zero branding, the recipe
- `.alcatrazer/config.toml` — per-developer (identity, daemon settings)
- `.env.example` — committed; `.env` stays gitignored
- A random agent identity, phantom UID, and workspace directory name
- The extracted package source under `.alcatrazer/src/alcatrazer/` and
  a Python symlink at `.alcatrazer/python` (used by the daemon)

`init` also generates the Alcatraz recipe (Dockerfile + entrypoint)
for the Docker backend.

### 3. Start the workspace

```bash
alcatrazer start
```

On the first run this builds the Docker image, creates the workspace
(flat snapshot of your current branch — no history), starts the
container, runs the `[startup]` commands from `coding-environment.toml`,
and **launches the promotion daemon in the background**. Subsequent
runs detect what changed and rebuild or restart only as needed.

The daemon polls the workspace's `.git/` every few seconds and
promotes new agent commits out to the outer repo under your identity —
you don't need to start it separately.

### 4. LLM authentication

**Recommended:** your existing Claude OAuth credentials at
`~/.claude/.credentials.json` are mounted read-only into the
container. If you've authenticated Claude Code on your host, nothing
else is needed.

**Alternative:** use an API key — copy `.env.example` to `.env` and
set:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### 5. Attach to the container

`alcatrazer start` leaves the container running detached. Step inside it
as the `agent` user:

```bash
alcatrazer visit
```

`visit` opens an interactive bash inside the running Alcatraz, working
directory set to `/workspace`. Errors cleanly if the Alcatraz isn't
running (no auto-start — explicit by design).

All tools declared in `coding-environment.toml` are available
(Python / Node / Rust / Go / .NET / Java plus any `[os]` packages),
along with the always-on security baseline: git, mise, Claude Code CLI,
gosu.

### 6. Watch promotion (optional)

In a separate terminal:

```bash
alcatrazer status                          # one-line summary
tail -f .alcatrazer/promotion-daemon.log   # full event stream
```

### Stop and clear

```bash
alcatrazer stop     # freeze: stop container + daemon; workspace, pin,
                    # and writable image layer preserved. Next `start`
                    # picks up where this one left off, same branch.

alcatrazer clear    # terminal: stop container + daemon (after draining
                    # pending commits), wipe inner workspace, and drop
                    # the pin. Image is preserved (skip rebuild) and so
                    # is `.alcatrazer/config.toml` (your identity carries
                    # over). Next `start` snapshots fresh from whichever
                    # branch you're currently on.
```

Both are idempotent and both do a final-sync of any pending commits
before shutting the daemon down. The difference is what survives:
`stop` preserves the inner workspace and the pin, so the next `start`
resumes the same branch. `clear` is terminal — it wipes the inner
workspace contents (including its `.git`) and drops the pin, so the
next `start` snapshots fresh from whichever branch you're currently
on. Promoted commits already live in your outer repo and are
unaffected either way.

### Verify the installation

```bash
alcatrazer test                # bundled unit + non-Docker integration tests
alcatrazer test --smoke        # also run Docker smoke tests (requires Docker)
alcatrazer start --run-selftest
                               # after start, run the bundled security
                               # self-tests against the running Alcatraz
                               # (phantom UID, credential isolation,
                               #  no docker socket, …)
```

## Configuration

Alcatrazer splits configuration across three files so that nothing in the
target repo's working tree reveals the tool to agents (Principle 2).

### `coding-environment.toml` — repo root, version-controlled

Agent-visible, zero alcatrazer branding. Describes what the container
must provide before the project's own setup can run:

```toml
schema_version = 1

[os]
packages = ["build-essential", "libpq-dev"]

[languages.python]
version = "3.12"
manager = "uv"          # always written; defaults to "pip" if omitted

[languages.node]
version = "22"
manager = "npm"         # always written; defaults to "npm" if omitted

[startup]
commands = ["uv sync", "npm install"]
```

`schema_version` stamps the file format. Files written before this field
existed are accepted as version 1, so existing configs keep working
unchanged. An older alcatrazer that meets a newer schema refuses to read
it rather than silently misinterpreting — upgrade alcatrazer in that case.

`manager` is always written by `alcatrazer init` (since Phase 1.2.4) so
the choice is visible at the line you'd edit. Each language declares
which managers ship bundled with the runtime — `pip` with Python, `npm`
with Node, `cargo` with Rust, `go` and `dotnet` are runtimes themselves.
Bundled managers are skipped at image build (no double-install); anything
else (`uv`, `poetry`, `pnpm`, `yarn`, `maven`, `gradle`) is installed via
`mise use --global` whether it's the language default or your override.

#### Java distributions

mise's `java` plugin defaults to Eclipse Temurin. Override by prefixing
the version string in `[languages.java].version`:

| TOML                | Distribution                          |
| ------------------- | ------------------------------------- |
| `"21"`              | Eclipse Temurin (default)             |
| `"temurin-21.0.5"`  | Temurin, pinned build                 |
| `"corretto-21"`     | Amazon Corretto                       |
| `"zulu-21"`         | Azul Zulu                             |
| `"liberica-21"`     | BellSoft Liberica                     |
| `"graalvm-21"`      | GraalVM (incl. native-image)          |

Pick whichever your project or employer requires; the choice is opaque
to alcatrazer — mise installs whatever the version string asks for, and
the JDKs are binary-compatible across distributions for standard Java
workloads. The `alcatrazer init` wizard prints the same hint when you
pick `java`, and it's also rendered as a comment block above each
language's `version =` line in your generated `coding-environment.toml`,
so the guidance reaches you whether you're at the prompt or hand-editing
the file later.

### `.alcatrazer/config.toml` — gitignored, per-developer

Alcatrazer-specific, invisible to agents. Contains the promotion identity
and daemon settings:

```toml
schema_version = 2
coding_environment_file = "coding-environment.toml"

[promotion]
name  = "Your Name"
email = "your@email.com"

[promotion-daemon]
interval     = 5                # polling interval (seconds)
verbosity    = "normal"         # or "detailed"
max_log_size = 512              # log rotation threshold (KB)
```

### `.git/info/exclude` — per-repo gitignore, not committed

`alcatrazer init` appends `.alcatrazer/` and the workspace directory
name here instead of the working-tree `.gitignore` — so the ignore
patterns themselves don't enter the agent snapshot.

See [docs/features/install_method.md](https://github.com/greg-latuszek/alcatrazer/blob/main/docs/features/install_method.md) for
the full rationale.

## Promoting Agent Work

Each new agent commit inside the workspace replays onto your working
branch in the outer repo as a fast-forward — the working tree and the
branch ref update together, atomically. Properties:

- **Working tree always in sync with HEAD.** Files appear under your
  cursor the same instant the commit lands on the branch. No window
  where `git status` shows phantom deletions.
- **Identity is rewritten to yours.** Both author and committer on
  the outer-side commit are the name + email from
  `.alcatrazer/config.toml`'s `[promotion]` section. The agent's
  random throwaway identity stays inside the workspace.
- **Byte-safe.** Binary diffs (images, archives, compiled artifacts)
  round-trip through the patch stream unchanged.
- **Incremental.** Only commits past the last successful replay get
  applied; nothing is reprocessed.
- **Unidirectional.** Inner → outer only. Your own commits to outer
  never flow into the workspace; if you want agents to see new
  outer work, `clear` and `start` a fresh workspace
  (see [Branch handling](#branch-handling)).
- **Abortable.** On apply failure (see
  [When outer already has a conflicting file](#when-outer-already-has-a-conflicting-file)),
  the replay rolls back cleanly via `git am --abort` — your outer's
  HEAD and working tree are byte-identical to their pre-attempt
  state.

The **sync daemon** runs on the host outside the workspace
container — it polls the inner repo's `.git/` and never writes
into the workspace itself. It's launched automatically by
`alcatrazer start` and torn down automatically by
`alcatrazer stop` / `alcatrazer clear`. Both teardown paths wait for
the daemon to apply any pending agent commits before exiting, so no
commit is lost in a graceful shutdown.

Run `alcatrazer status` for a one-line summary of which branch the
workspace is bound to, how many commits are pending, and when the
last successful replay was. Tail `.alcatrazer/promotion-daemon.log`
for the daemon's transition events (held / resumed / paused /
applied).

## Container Details

### Base image and tools

- **Ubuntu 24.04** base image
- Always-on baseline: **git**, **mise** (version manager), **Claude
  Code CLI**, **gosu** (for privilege drop in the entrypoint)
- Language runtimes come from `[languages.*]` in
  `coding-environment.toml` — they are installed via mise, with the
  exact version the user declared (no hidden defaults). Supported:
  `python`, `node`, `rust`, `go`, `dotnet` (the .NET SDK — runs
  C#, F#, and VB.NET), `java` (JVM — Maven or Gradle as build tool;
  see [Java distributions](#java-distributions) for picking Temurin
  vs Corretto vs Zulu vs GraalVM).
- OS packages come from `[os].packages` (installed with `apt-get`).
  Some languages also auto-add OS packages they need at runtime: for
  example, picking `[languages.dotnet]` includes `libicu74` (.NET
  cannot start without an ICU library). These are baked at image
  build time — the agent user has no runtime privilege escalation,
  so anything requiring root has to land before the container's
  entrypoint drops to the non-root user.

Inside the container agents can use `mise` to layer additional
runtimes on top; those stay local to the writable layer.

### Per-repo names

Image tag and container name are derived from the repo's canonical
absolute path: `alcatraz-workspace:<basename>-<hash12>` and
`workspace-<basename>-<hash12>` (e.g. `workspace-myrepo-7c4a92b14f3e`).
Two alcatrazers on different repos run simultaneously without
collision; `docker ps` lists them with recognizable names. Use
`alcatrazer visit` to step inside without typing the hash.

> **Upgrading from pre-Phase 1.2.5 alcatrazer?** The old shared names
> (`alcatraz-workspace:local`, container `workspace`) won't match the
> new derived names; do a one-time cleanup:
>
> ```bash
> docker rm -f workspace 2>/dev/null
> docker rmi alcatraz-workspace:local 2>/dev/null
> alcatrazer start   # rebuilds under per-repo names
> ```

### Stale-image self-healing

Each built image carries an `alcatrazer.config_hash` LABEL whose value
is a SHA-256 of the recipe (Dockerfile body) that built it. On
`alcatrazer start`, the running image's label is compared against the
hash of the recipe alcatrazer would build right now; on mismatch, the
image is rebuilt automatically. This catches scenarios that bare "is
there an image?" checks miss — `rm -rf .alcatrazer/` followed by
`alcatrazer init` regenerates the Dockerfile but leaves the old image
in place; the LABEL mismatch then forces a clean rebuild.

`alcatrazer clear` semantics are unchanged (image kept across
`stop`/`clear`/`start` cycles when the recipe hasn't changed). Images
built before this LABEL existed are treated as stale and trigger one
rebuild after upgrade.

### Entrypoint behavior

The container starts as root to fix ownership of the mounted
workspace and the mise directory, then drops to the non-root `agent`
user via gosu. On each start the entrypoint runs `mise install` so
any newly-declared tools materialize before the `[startup]` commands
run.

### Ephemeral caches, no shared Docker volumes

All package caches (mise, pip, npm, etc.) live **inside the container's
writable overlay layer** — there are no named Docker volumes shared
between Alcatrazes.

**Why:** sharing writable caches across Alcatrazes would let one
compromised agent poison every other Alcatraz on the laptop via cache
tampering. Keeping caches per-container closes that attack surface,
at the cost of re-downloading packages on `clear` + `start`. A
`stop` + `start` cycle preserves the writable layer, so caches
survive a normal restart. See
[docs/features/install_method.md](https://github.com/greg-latuszek/alcatrazer/blob/main/docs/features/install_method.md)
§ "Ephemeral caches — no shared Docker volumes" for the full
rationale.

## Docker Container Rules

These rules are enforced by `DockerPrison` (the Docker adapter of the
Alcatraz sandboxing port) when it builds and runs the workspace container:

1. **Mount only the workspace directory** as the working volume — never the outer repo or the host home directory.
2. **Mount only `~/.claude/.credentials.json`** (read-only) for LLM auth — never the entire `~/.claude/` directory (which contains project memories, settings, and other config).
3. **Never mount `~/.ssh`, `~/.gnupg`, `~/.config`, or `~/.gitconfig`** into the container.
4. **Never mount the Docker socket** (`/var/run/docker.sock`) — this gives root-equivalent access to the host.
5. **Never pass host environment variables blindly** (e.g. `--env-file` with shell profile). Only explicitly chosen variables from `.env` are passed.
6. **Allow outbound internet access** so agents can call LLM APIs (Anthropic, etc.).
7. **Run as phantom UID** — the container user's UID does not exist on the host.

## Workflow

1. `alcatrazer init` — one-time interactive setup (per repo).
2. `git checkout -b feat/X` — start a feature branch as you would
   for any normal git work.
3. `alcatrazer start` — build the image if needed, snapshot
   your currently-checked-out branch into the workspace, start the
   container, run `[startup]` commands, and launch the sync daemon.
   Alcatrazer remembers which branch you started on; agent commits
   replay back to that same branch.
4. `alcatrazer visit` — step inside as the agent user.
5. Agents inside the container write code, run tests, commit
   incrementally. They may use branches, delegate to sub-agents, and
   merge.
6. Each agent commit replays onto outer `feat/X` under your identity
   automatically, working tree and ref updating together. Watch
   activity with `alcatrazer status` (single-line summary) or
   `tail -f .alcatrazer/promotion-daemon.log` for the full event log.
7. `git push origin feat/X` if you want to push the promoted commits to GitHub.
8. `alcatrazer stop` when done for the day (freeze-restart, same
   branch) — or `alcatrazer clear` to tear the workspace down for good
   (its contents are wiped and the pin dropped; your already-promoted
   commits stay safe in the outer repo).

## Running Tests

The bundled test suite is the user-facing verification surface —
invoke it via the CLI:

```bash
alcatrazer test            # unit + non-Docker integration tests
alcatrazer test --smoke    # also run Docker smoke tests (requires Docker)
```

For development on the tool itself (checkout of this repo):

```bash
mise run test              # full non-Docker suite
mise run test-fast         # unit tests only, skip slow integration
mise run test-smoke        # Docker smoke tests (needs an initialized Alcatraz)
mise run lint              # ruff check
mise run format            # ruff format + ruff check --fix
mise run build             # build the wheel into dist/
```

The suite covers identity generation (name/email pools, workspace-dir
naming, collision avoidance), init/start/stop/clear command flows,
snapshot (branch detection, extraction, `.gitignore` filtering,
exclusions), promotion (pin-at-start binding, identity rewrite,
byte-safe binary blobs, incremental replay, held/paused state
transitions, clean abort on conflict), daemon lifecycle (PID guard,
config, signals, hold/resume/pause detection, final-sync on
shutdown), language manifest
generation, Dockerfile templating, the `Alcatraz` port contract and
its `DockerPrison` adapter, and the bundled security self-tests
(phantom UID, credential isolation, no docker socket, workspace
ownership, no git remotes). All tests use Python's `unittest`
framework with real git repos for integration tests and mocking for
unit tests.

## License

Alcatrazer is licensed under the **Apache License, Version 2.0** — see [`LICENSE`](https://github.com/greg-latuszek/alcatrazer/blob/main/LICENSE).

The licence change from MIT to Apache-2.0, the differences in plain English with concrete scenarios for non-lawyer readers, and the verification that Alcatrazer's full dependency graph is compatible with Apache-2.0 are documented in two companion files:

- [`docs/licence_change_reasoning.md`](https://github.com/greg-latuszek/alcatrazer/blob/main/docs/licence_change_reasoning.md) — the licence change explained for users
- [`docs/license_dependencies_and_usage.md`](https://github.com/greg-latuszek/alcatrazer/blob/main/docs/license_dependencies_and_usage.md) — every dependency, its licence, and the compatibility analysis
