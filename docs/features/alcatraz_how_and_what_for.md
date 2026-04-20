# Alcatrazer: How and What For — Architecture Brainstorm

## Status: Brainstorm (in progress)

This document captures a brainstorming session about Alcatrazer's fundamental architecture —
what isolation mechanism to use, how to integrate with end-user Docker infrastructure,
and whether the tool can be fully transparent.

---

## Starting Point: Docker Template Machinery

The original question was about extending the Docker template machinery for end users.
The ultimate goal is to either extend the end user's existing Docker infrastructure
or create new infrastructure if none exists.

This raises several problems:
- **Finding** end-user Docker infrastructure (it might be in project root or not)
- **Injecting** into it (they may use multi-stage Dockerfiles or not, docker-compose or not)
- **User preferences** (do they prefer pure Dockerfiles, docker-compose, or something else?)

We need to understand what is critical in our Dockerfile for Alcatrazer to operate
and what is "accidental" — artifacts from this repo's development history.

### What's Critical in Our Dockerfile

These are the parts Alcatrazer cannot function without:

1. **Phantom UID user creation** — `groupadd`/`useradd` with `USER_UID`
2. **`gosu` installed** — for privilege dropping in entrypoint
3. **The entrypoint pattern** — run as root, `chown` workspace, `gosu agent` to drop privileges
4. **`git` installed** — agents need it, promotion needs it
5. **`commit.gpgsign false`** — no signing keys in container
6. **No global git identity set** — workspace local config only

### What's Accidental (Dev History Artifacts)

These are specific to developing Alcatrazer itself, not requirements for end users:

- `ubuntu:24.04` base image
- `mise` as the tool version manager
- Claude Code CLI
- Python/Node/Bun specific versions
- `tmux`, `ripgrep`, `build-essential`, `unzip`
- The two-stage build structure (dev-base/dev)

The critical security layer is really ~15 lines of Dockerfile instructions plus the entrypoint script.

---

## Two Integration Models: Wrap vs Base

### Model A: FROM theirs (wrap — no touch)

```dockerfile
FROM user-existing-image:latest
# inject security layer on top
RUN install gosu, create phantom UID user...
COPY entrypoint.sh ...
ENTRYPOINT ["entrypoint.sh"]
```

**Pros:**
- User's existing Dockerfile is untouched
- Works for teams that already have a working dev container
- Clean "we're just a security wrapper" story

**Cons (serious):**
- **Distro detection** — we need `gosu`, but that means `apt-get` on Debian/Ubuntu,
  `apk` on Alpine, `yum` on RHEL. We'd need to sniff the package manager.
- **User conflict** — their image might already have a non-root user (e.g., `node` images
  ship with user `node`). We'd be adding a second user, and their `USER` directive
  fights with our entrypoint.
- **Entrypoint chaining** — if their image has its own ENTRYPOINT, we override it.
  We'd need to detect and chain.
- **No guarantee git exists** in their image
- **Layer ordering** — security-critical stuff is in the LAST layers, easiest to
  accidentally override in derived images

### Model B: FROM us (base — building on)

```dockerfile
FROM alcatrazer/base:latest
# user adds their tools
RUN apt-get install ...
```

**Pros:**
- Security layer is guaranteed correct — we control it fully
- git, gosu, phantom UID, entrypoint — all there before user touches anything
- Familiar pattern (like `devcontainers/base`, `nvidia/cuda` base images)
- Security stuff in early layers — hardest to accidentally break

**Cons:**
- User must rewrite their existing Dockerfile to use our base
- We dictate base distro (ubuntu? alpine? both?)
- If user has a complex multi-stage build, integration is painful
- Feels more invasive — "replace your foundation" vs "add a wrapper"

### Key Tension

The security layer needs deep access — root start, user creation, entrypoint control,
package installation. That's fundamentally at odds with being a thin wrapper.

---

## Stepping Back: Do We Even Need Docker?

### How Programmers Operate Without Alcatrazer

Developers have three major activities in their repositories:

1. **Coding** — writing code, asking agents to write code
   - (1a) **Unit/module testing** — doesn't need a fully built product, part of the coding loop
2. **Building** — compile, link, or use Docker to obtain the final product
   (may be multi-container microservices)
3. **Running** — execute the product, observe behavior
   - (3a) **System tests** — automated validation from the whole-product viewpoint

### Where Alcatrazer Fits

**Alcatrazer targets activity (1) — coding.** Not building, not running.

The agent writes code and runs unit tests. That's it. If the project uses Docker
to build a multi-container app, that's the project's concern. The agent needs:
a filesystem with the code, git, language runtimes, and test tools.

**Scope boundary:** Alcatrazer targets a single repository. If an application is
microservices-based across multiple repos, we target one repo at a time.

### Can Alcatrazer Be Fully Transparent?

The origin of this question: the goal of Alcatrazer is to ensure that agents won't escape
from folders they should touch, nor steal any secret, nor modify files outside their
assignment — intentionally or accidentally.

What Alcatrazer intercepts in git development:
- Agents don't know the real developer identity
- Internal git has no remote, no signing keys, no GitHub tokens
- Communication to GitHub comes from the external git only
- Alcatrazer has copy machinery (promotion) between external and internal repos
- Alcatrazer protects surrounding folders via careful mounting (only workspace)
  and phantom UID

**Can we achieve these fundamentals while being fully transparent?** Making the end user
unaware of the mechanics — telling them: "use Alcatrazer and you can forget about
laptop/GitHub security — Alcatrazer will do it for you."

**What can be hidden (transparent):**
- How isolation works (Docker, Sysbox, whatever)
- Phantom UID mechanics
- Workspace directory naming
- The internal git setup
- Promotion mechanics (daemon does it automatically)

**What cannot be fully hidden:**
- The `.alcatrazer/` directory exists (gitignored, but visible on the host)
- `coding-environment.toml` exists (version controlled — but zero alcatrazer branding,
  see "Resolved: Config Split" below)
- The user must provide API keys (`.env`)
- The user must review promoted code (that's the whole point)

**Conclusion:** The *mechanism* can be transparent. The user runs `alcatrazer init`,
then `alcatrazer start`, agents work, code appears in the outer repo. The user never
thinks about Docker, UIDs, or mounts.

### Alternative Isolation Mechanisms

If not Docker, what else could provide the isolation?

**Sysbox** (Container Runtime by Nestybox, now part of Docker Inc.):
- Provides "VM-like" containers — full system containers
- Can run Docker inside Docker securely (no privileged mode)
- Handles user namespace remapping natively (eliminates phantom UID gymnastics)
- Would give agents a full Linux environment where their Docker builds work too

**Other options:**
- **gVisor** — Google's container runtime, intercepts syscalls
- **Kata Containers** — lightweight VMs
- **Firecracker** — microVMs (what AWS Lambda uses)
- **Podman** — rootless containers, can do nested containers
- **bubblewrap (bwrap)** — lightweight sandboxing used by Flatpak
- **nsjail** — Google's lightweight process isolation
- **LXC/LXD** — system containers (more VM-like than Docker)

With Sysbox, the architecture would change radically:
```
Current:  Host -> Docker container (carefully crafted Dockerfile)
Sysbox:   Host -> Sysbox system container (generic Linux, full environment)
```

**Limitation:** Sysbox is Linux-only. It doesn't work on macOS (Docker Desktop runs
a Linux VM, and Sysbox can't be installed as a runtime inside it).

### Platform Requirements

- **Linux** — primary target
- **macOS** — must support (many developers use Macs)
- **Windows** — nice to have, some Docker limitations, but if possible include

This rules out Linux-only solutions like Sysbox as the sole isolation mechanism.

### Network Access

Agents need network access. Blocking it would be too limiting for coding freedom:
- Access to LLMs and their APIs
- Scanning GitHub repos as input/templates for coding
- Reading documentation (freshest view, not embedded in LLM training data)
- Scanning webpages as UI templates, data sources (government services,
  weather services, mapping services), UI resources (images, movies, emojis)

Network restriction is out of scope.

### The Security Fundamentals (Independent of Mechanism)

What Alcatrazer actually needs from ANY isolation layer:

1. **Filesystem isolation** — agent sees only the workspace, nothing else on host
2. **Secret isolation** — no SSH keys, GPG keys, tokens, dotfiles leak in
3. **Identity isolation** — agent can't discover the real developer
4. **Process isolation** — agent can't see/signal host processes
5. **Git isolation** — no remote, no real identity, no signing keys

Docker gives us all five, but at a cost — we're bending a deployment tool
into a dev sandbox. That's why we fight with UIDs, mount paths,
Dockerfile complexity, and compose path resolution.

---

## Key Insight: Two Classes of Docker

There are two fundamentally different uses of Docker in a repository:

1. **Build Docker** — produces the deployable artifact. `docker build` -> `docker push` -> deploy.
   Alcatrazer NEVER touches these. It's the project's concern, like a Makefile.

2. **Coding Docker** — the environment where a human (or agent) writes and tests code.
   This is Alcatrazer's territory.

### The Landscape (What a Repo Might Have)

| Scenario | Build Docker | Coding Docker | Alcatrazer Action |
|----------|-------------|---------------|-------------------|
| A | None | None | Provide everything |
| B | Yes (Dockerfile for the app) | None | Provide coding Docker, ignore build Docker |
| C | None | Yes (devcontainer, custom) | Wrap/extend their coding Docker |
| D | Yes | Yes | Identify coding Docker, wrap it, ignore build Docker |

**Scenario A** — clear, Alcatrazer provides everything from scratch.

**Scenario B** — during installation, either ask "are your Dockerfiles for coding or
building?" or autodetect. If building, we provide Alcatrazer as a new coding Docker
alongside the existing build Docker. Same outcome as A.

**Scenarios C and D** require understanding what a "coding Docker" typically looks like
in the wild. The industry standard for this is the **devcontainer spec**.

---

## Devcontainer Spec — Background

### Origin and Governance

Microsoft created devcontainers for the VS Code Remote - Containers extension (~2019).
The idea: define your dev environment as code so every developer gets the same setup.

In 2022, Microsoft open-sourced the spec as the
**Development Containers Specification** (https://containers.dev/),
governed by an open community. It's a real standard, not just a term.

### Adoption

- VS Code (native)
- GitHub Codespaces
- JetBrains (Gateway, IntelliJ) — uses their own CLI (`ijdevc`), not Microsoft's
- DevPod (open-source)
- Coder (cloud dev environments)
- Google Cloud Workstations

### How It Relates to Docker

Devcontainers **build on top of Docker** (or any OCI runtime). They don't replace it.

```
.devcontainer/
    devcontainer.json    <-- the spec file (required)
    Dockerfile           <-- optional, custom image
    docker-compose.yml   <-- optional, multi-container setups
```

`devcontainer.json` is a configuration layer ABOVE Docker:

```jsonc
{
  "name": "My Project",
  "image": "mcr.microsoft.com/devcontainers/python:3.12",
  // OR:
  "build": { "dockerfile": "Dockerfile" },
  // OR:
  "dockerComposeFile": "docker-compose.yml",

  "features": {
    "ghcr.io/devcontainers/features/node:1": { "version": "22" },
    "ghcr.io/devcontainers/features/rust:1": {}
  },

  "mounts": ["source=${localEnv:HOME}/.ssh,target=/home/dev/.ssh,type=bind,readonly"],
  "forwardPorts": [3000, 8080],
  "postCreateCommand": "npm install",
  "remoteUser": "vscode"
}
```

### Key Concepts

1. **Image source** — three options: prebuilt image (`"image"`), custom Dockerfile (`"build"`),
   or docker-compose (`"dockerComposeFile"`). Under the hood, it's always a Docker container.

2. **Features** — composable, installable add-ons. Each feature is a small script that runs
   during image build. E.g., "install Node 22", "install Go", "install Docker-in-Docker".
   Published as OCI artifacts. Anyone can create them. This is the extension mechanism.

3. **Lifecycle hooks** — `postCreateCommand`, `postStartCommand`, `postAttachCommand` —
   run scripts at various container lifecycle points.

4. **User** — `remoteUser` controls who runs inside the container
   (typically `vscode` or `node`, not root).

### Potential Benefits of Fitting into Devcontainer Spec

1. **Clear signal** — `.devcontainer/` is an unambiguous indicator "this is for coding"
   (not building). No detection heuristics needed.

2. **Established pattern** — developers who use devcontainers already understand the model.
   Alcatrazer injects into a well-known ecosystem rather than inventing its own.

3. **IDE tooling for free** — VS Code, JetBrains, and others natively support
   `.devcontainer/`. Developers could connect their IDE to the running Alcatrazer
   container and visually observe agents coding in real-time — file explorer shows
   changes as they happen, terminal gives interactive access, git panel shows commits.
   This enables a "pair programming with AI" experience through the IDE.

### The Two-Mode Workflow

When a developer connects their IDE to the Alcatrazer container, they operate
as the container user (phantom UID, random agent identity). This means:
- No SSH keys, no GitHub tokens — by design
- No `git push` from inside — no remote configured
- The developer's git identity inside is the fake agent identity

This is fine because:
- Developers using Alcatrazer intentionally want agents to code — the point is
  to delegate coding to agents, not to code alongside them
- Being inside the container is for **observing and controlling**, not coding
- The "real" git work (pushing, PRs, review) happens on the host, in the outer repo
- Promotion daemon moves commits automatically

The developer operates in **two modes:**
1. **Inside the container** (via IDE) — observe agents, prompt, control, review in real-time
2. **On the host** (normal workflow) — review promoted code, push, create PRs

This two-mode workflow reflects how the software engineer's role is changing due to
agentic coding — more designing, dialog, oversight, and heavy review; less direct coding.
This should be clearly stated in documentation so developers know what to expect.

If a developer does edit files inside the container, those changes are committed with
the phantom UID and agent identity — same as agent commits. No problem. The promotion
daemon treats all inner commits the same.

---

## Devcontainer Security Research

### The Fundamental Problem

**Devcontainers are designed for developer convenience, not security.**

Their goal is to make containers feel like local development — sharing credentials,
forwarding SSH keys, mounting host folders. This is the opposite of what Alcatrazer needs.

Binding Alcatrazer to the devcontainer concept would send a false message to developers —
"ah, this is for my convenience of working with agentic coding." Our message must be
straight: **don't trust AI agents, put them in Alcatraz.**

### VS Code: Actively Hostile to Security

VS Code automatically forwards the following into devcontainers:

| What gets forwarded | Mechanism | Can be disabled? |
|---|---|---|
| **SSH agent** | Relay socket (`/tmp/vscode-ssh-auth-*.sock`) via VS Code server IPC | **No official setting** (confirmed by Microsoft maintainer in [#9897](https://github.com/microsoft/vscode-remote-release/issues/9897)) |
| **Git credential helper** | Node.js relay script injected into global git config, communicates via IPC socket | **Partially** — `dev.containers.copyGitConfig: false` + `gitCredentialHelperConfigLocation: "none"` stops the helper, but not env vars |
| **GPG agent** | Socket forwarding | **No official setting** |
| **Host command execution** | `vscode-ipc-*.sock` — enables running commands on host via `code` CLI | **No official setting** |
| **Git IPC** | `vscode-git-*.sock`, `VSCODE_GIT_IPC_HANDLE` | **No official setting** |
| **GIT_ASKPASS** | Credential prompt handler delegating to host | Re-injected even after clearing |
| **Environment variables** | `VSCODE_IPC_HOOK_CLI`, `REMOTE_CONTAINERS_IPC`, `REMOTE_CONTAINERS_SOCKETS`, etc. | Re-injected after `remoteEnv` clears them |

Microsoft maintainer's position ([#4426](https://github.com/microsoft/vscode-remote-release/issues/4426)):
> *"This won't make the container safe to run untrusted code though. 1) We also forward
> ssh and gpg agents. 2) Docker containers are not considered a secure sandbox."*

#### Three-Layer Defense (Best Available Mitigation)

Daniel Demmel documented a comprehensive defense in
[Coding Agents in Secured VS Code Dev Containers](https://www.danieldemmel.me/blog/coding-agents-in-secured-vscode-dev-containers).
It requires three layers and still has race windows:

**Layer 1 — remoteEnv (partial):**
```jsonc
"remoteEnv": {
  "SSH_AUTH_SOCK": "",
  "GPG_AGENT_INFO": "",
  "BROWSER": "",
  "VSCODE_IPC_HOOK_CLI": null,
  "VSCODE_GIT_IPC_HANDLE": null,
  "GIT_ASKPASS": null,
  "VSCODE_GIT_ASKPASS_MAIN": null,
  "VSCODE_GIT_ASKPASS_NODE": null,
  "VSCODE_GIT_ASKPASS_EXTRA_ARGS": null,
  "REMOTE_CONTAINERS_IPC": null,
  "REMOTE_CONTAINERS_SOCKETS": null,
  "REMOTE_CONTAINERS_DISPLAY_SOCK": null,
  "WAYLAND_DISPLAY": null
}
```

**Critical limitation:** VS Code re-injects its own variables when spawning new processes.

**Layer 2 — Shell hardening (.bashrc, line 1, before interactive guard):**
```bash
unset VSCODE_IPC_HOOK_CLI VSCODE_GIT_IPC_HANDLE GIT_ASKPASS \
      VSCODE_GIT_ASKPASS_MAIN VSCODE_GIT_ASKPASS_NODE VSCODE_GIT_ASKPASS_EXTRA_ARGS \
      REMOTE_CONTAINERS_IPC REMOTE_CONTAINERS_SOCKETS REMOTE_CONTAINERS_DISPLAY_SOCK \
      WAYLAND_DISPLAY
export BROWSER= SSH_AUTH_SOCK= GPG_AGENT_INFO=
```

Must be at line 1 because AI agents invoke bash as non-interactive shells.

**Layer 3 — Socket file deletion (postStartCommand + background loop):**
```bash
find /tmp -maxdepth 2 \( -name 'vscode-ssh-auth-*.sock' \
  -o -name 'vscode-remote-containers-ipc-*.sock' \
  -o -name 'vscode-remote-containers-*.js' \) -delete 2>/dev/null || true
```

Plus a background cleanup loop (10 passes at 30-second intervals) because VS Code
creates sockets **after** `postStartCommand` runs.

**Even all three layers have race windows.** The socket exists briefly before deletion,
and VS Code may re-create it.

#### Security Research on VS Code Container Escape

- [The Red Guild](https://blog.theredguild.org/leveraging-vscode-internals-to-escape-containers/) —
  demonstrated that VS Code's forwarded SSH socket can be used to exfiltrate data
  from "isolated" containers.
- [Jamie McCrindle](https://dev.to/jamiemccrindle/exploiting-visual-studio-code-devcontainers-16fb) —
  showed SSH agent exploitation from inside devcontainers.

### JetBrains: Significantly Less Hostile

JetBrains IDEs (IntelliJ, PyCharm, WebStorm, etc.) use their own devcontainer implementation
(`ijdevc` CLI), NOT Microsoft's open-source CLI. Their architecture is fundamentally different:
the full IDE backend runs inside the container, communicating with a thin client on the host
via TLS 1.3-encrypted RD protocol.

| What gets forwarded | JetBrains behavior |
|---|---|
| **SSH agent** | **NOT forwarded** (broken/missing — [IJPL-162844](https://youtrack.jetbrains.com/issue/IJPL-162844)) |
| **Git credential helper** | **NOT forwarded** (open feature request — [IJPL-181474](https://youtrack.jetbrains.com/issue/IJPL-181474)) |
| **GPG agent** | **NOT forwarded** (open feature request — [IJPL-162470](https://youtrack.jetbrains.com/issue/IJPL-162470)) |
| **Docker credentials** | **NOT forwarded** (open feature request — [GTW-8899](https://youtrack.jetbrains.com/issue/GTW-8899)) |
| **.gitconfig** | **Partially copied** — only `user.name`, `user.email`, `pull.rebase`, `alias.*` |
| **IPC sockets for host escape** | **No equivalent** of VS Code's `vscode-ipc-*.sock` |

**The only leak is `.gitconfig` identity** (`user.name`, `user.email`) — which our workspace
local git config already overrides (local config takes priority over global).

JetBrains deploys IJent (IntelliJ Execution Agent) into the container for file system
and process APIs, but these serve the IDE backend itself, not as a relay back to the host.

### Open-Source Devcontainer CLI: Clean

The open-source devcontainer CLI (`@devcontainers/cli`, used by GitHub Codespaces and DevPod)
has **zero credential forwarding code**. Confirmed by maintainer in
[devcontainers/cli#441](https://github.com/devcontainers/cli/issues/441):
> *"The ssh-agent forwarding is part of the Dev Containers extension and not part of
> the Dev Containers CLI."*

No SSH agent, no git credential helper, no GPG, no IPC sockets.

### Summary: IDE Security Comparison

| IDE / Tool | SSH agent | Git credentials | GPG | IPC escape | Overall |
|------------|-----------|----------------|-----|------------|---------|
| **Devcontainer CLI** (open-source) | No | No | No | No | **Clean** |
| **JetBrains** (IntelliJ, PyCharm, etc.) | No | No (only gitconfig identity, overridden by local config) | No | No | **Very good** |
| **GitHub Codespaces** | Uses CLI (no forwarding) + scoped GITHUB_TOKEN | Block token via remoteEnv | No | No | **Good** |
| **VS Code** | Yes, no disable | Partially disableable | Yes, no disable | Yes, no disable | **Hostile** |

---

## Conclusion: Devcontainers Are Not the Right Frame

### Why We Should NOT Bind Alcatrazer to the Devcontainer Concept

The research shows a clear picture: **devcontainers are designed for developer convenience,
not security.** That's why VS Code implemented all the "backdoors" to the host — SSH agent
forwarding, git credential relay, IPC sockets. They make development seamless. Developers
like this convenience because they can share whole dev environments with team members,
solving "works on my machine." The question is whether developers are aware of the
security impact — that under VS Code especially, this is not a good environment for
agentic coding.

Look at the [Daniel Demmel article](https://www.danieldemmel.me/blog/coding-agents-in-secured-vscode-dev-containers) —
it shows the enormous effort needed to secure a VS Code devcontainer for AI agents.
Three defensive layers, race conditions, background cleanup loops, shell hardening.
Why? **Because we are fighting against the goal of the given technology.**
Devcontainers' goal is not security but convenience.

**Binding Alcatrazer to the devcontainer term would send a false message to developers** —
a message like "ah, this is for my convenience of working with agentic coding."

**Our message must be straight:**

> Watch out developers community. Your paradigm has changed. You trust yourself - that is
> understood. And that was you who have coded inside container up till now. But the world has
> changed. It is no more you who is coding inside container. These are AI agents that might
> do harmful things due to hallucination or prompt injecting via accidental download of
> malicious software. Harmful both - to your localhost and to your public git repository.
> So, don't trust them - put them in Alcatraz.

### What We Take From This Research

1. **Alcatrazer is NOT a devcontainer.** It is a security tool that happens to use Docker
   containers as an isolation mechanism. The framing matters.

2. **The devcontainer spec is useful as input, not as identity.** If a user has a
   `.devcontainer/`, we can read it as a recipe for what tools to install — but we build
   our own secure container, not a devcontainer.

3. **IDE attachment is a feature, not the architecture.** If developers want to observe
   agents via VS Code or JetBrains, that's optional. JetBrains attachment is safe.
   VS Code attachment requires documented caveats and defensive layers.

4. **The devcontainer CLI (open-source) is safe.** If we ever do interact with
   the devcontainer ecosystem, the CLI is the right integration point — it has
   zero credential forwarding.

---

## Resolved: Isolation Machinery Lives Inside `.alcatrazer/`

**Decision (2025-04-20):** The Dockerfile and any future isolation machinery (compose files,
entrypoint scripts, etc.) reside inside `.alcatrazer/`, which is gitignored.

**Why:** The workspace snapshot copies everything from the target repo's main branch.
If the Dockerfile lived in the repo (version controlled), agents would see it in the
workspace — the prison blueprints problem (Principle 2). The alternative — selective
snapshot logic to exclude specific files — adds real complexity with no upside.

By placing isolation machinery in `.alcatrazer/`, the existing gitignore model handles
exclusion for free: what's gitignored isn't in the repo, isn't in the snapshot, isn't
visible to agents. No filtering, no configuration, no edge cases.

This also aligns with:
- **Zero Pollution** — repo gets only `coding-environment.toml` and `.env.example`
  (see "Resolved: Config Split" below for how `alcatrazer.toml` was eliminated)
- **Single Self-Contained Package** — Docker templates live in the package, get generated
  into `.alcatrazer/` during init
- **Per-Repo Install** — each repo's `.alcatrazer/` is its own self-contained installation

## Resolved: Config Split — Visible Environment vs Hidden Machinery

**Decision (2025-04-20):** Analyzing what agents can see in the workspace snapshot revealed
that `alcatrazer.toml` (version controlled) would violate Principle 2 in multiple ways:
the filename reveals the tool, comments describe it, and `[promotion]` leaks the real
developer identity. The `.gitignore` with `.alcatrazer/` entries has the same problem.

**The trap:** version control is needed for team sharing, but version-controlled files
enter the workspace snapshot and become visible to agents.

**The escape:** split configuration by visibility and sensitivity.

### Three-file architecture

**`coding-environment.toml`** — repo root, version controlled, visible to agents.

Contains ONLY the coding environment definition: language runtimes, package managers,
OS packages, boot-up commands. Zero alcatrazer branding — not in the filename, not in
comments, not in section names. Looks natural in any repository ("here's what tools
this project uses"). If the repo already has a file by this name, use a random hex
suffix: `coding-environment-a3f7.toml`.

Agents see this file and treat it as a dev environment definition — nothing suspicious.
They can even improve it (add a missing OS package, a new framework), which gets promoted
to the outer repo, reviewed by the developer, and used to rebuild the container.

**`.alcatrazer/config.toml`** — gitignored, per-developer, invisible to agents.

Contains everything alcatrazer-specific:
- Pointer to the coding environment file
- `[promotion]` — real developer identity (name, email)
- `[promotion-daemon]` — interval, branches, mode, verbosity

This is where sensitive and alcatrazer-branded configuration lives.

**`.git/info/exclude`** — git's built-in per-repo ignore, NOT version controlled.

Contains ignore patterns for `.alcatrazer/`, workspace directory.
Works exactly like `.gitignore` but lives inside `.git/` — never in the working tree,
never in the snapshot. `alcatrazer init` writes these patterns automatically.

### The flow

```
coding-environment.toml    (version controlled — team-shared, agent-visible)
            ↓ read by
.alcatrazer/config.toml    (gitignored — points to it, adds alcatrazer-specific config)
            ↓ generate
.alcatrazer/Dockerfile     (gitignored — generated build artifact)
            ↓ docker build
container: alcatrazer security base + coding environment layer
```

### Principle 2 scorecard

| Artifact | Version controlled | In snapshot | Reveals Alcatraz |
|---|---|---|---|
| `coding-environment.toml` | yes | yes | **no** |
| `.alcatrazer/config.toml` | no | no | n/a (invisible) |
| `.git/info/exclude` | no | no | n/a (invisible) |
| `.env.example` | yes | yes | **no** |

### What this resolves

1. **"Wrap vs Base" disappears.** It's always base — alcatrazer controls the security
   foundation (phantom UID, gosu, entrypoint, git). The user's tools are a generated
   layer on top. The real question was never "wrap or base" — it was "who writes the
   Dockerfile?" Answer: nobody, it's generated from `coding-environment.toml`.

2. **"Detection heuristics for build vs coding Docker" becomes irrelevant.** Alcatrazer
   always generates its own Dockerfile. Existing Docker in the repo is build Docker —
   the project's concern, not ours.

3. **Principle 2 is fully satisfied.** No alcatrazer-branded artifacts enter the snapshot.

4. **Team sharing works where it matters.** The evolving part (coding environment) is
   version controlled. The stable, sensitive parts (promotion identity, daemon config)
   are per-developer in `.alcatrazer/`.

5. **Agents can improve their own environment.** They see `coding-environment.toml`,
   add a missing dependency, it gets promoted, reviewed, and used to rebuild.
   A virtuous cycle — without ever knowing they're in Alcatraz.

### Design properties

- **Docker is an implementation detail.** The user thinks "what tools do my agents need,"
  not Docker syntax. If isolation machinery changes in the future, the toml stays the
  same — only the generator changes.
- **Rebuild is simple.** Edit toml, run a command, get a new container.
- **Security layer stays under alcatrazer's control.** The user cannot accidentally
  weaken the base — they can only add tools on top.
- **`[coding-environment]` is a "usable startup," not a lockdown.** Agents have sudo
  inside the container (the container boundary is the security perimeter). The toml
  pre-installs tools for convenience/speed, but agents can install anything at runtime.

## `coding-environment.toml` Format

### Guiding principles

- **Zero alcatrazer branding** — no filename, comment, or section name reveals the tool.
- **Usable startup, not lockdown** — pre-installs tools for convenience and speed, but
  agents have sudo and can install anything at runtime. The container boundary is the
  security perimeter, not the toml.
- **Don't repeat what the repo already defines** — language libraries belong in
  `requirements.txt`, `package.json`, `Cargo.toml`, etc. Those files are already in
  the repo and get snapshotted into the workspace. The toml defines what must exist
  BEFORE those files can be consumed (runtimes, managers, OS deps).
- **Agents can improve it** — agents see this file in the workspace and can add missing
  dependencies. Changes get promoted, reviewed by the developer, and used to rebuild.

### Sections and installation order

The sections follow a strict dependency order. OS packages may be dependencies for
language runtimes or their libraries. Language runtimes must exist before package
managers can run. Startup commands depend on everything above. The reverse is
essentially never true.

The Dockerfile generator enforces this order regardless of how the file is written,
but the file should reflect the order for readability.

**1. `[os]` — system packages (installed first)**

```toml
[os]
packages = ["build-essential", "libpq-dev", "ffmpeg"]
```

Installed via the OS package manager (`apt-get`) at Docker build time. Needed for:
compilation dependencies (`build-essential`, `libpq-dev`), system tools that language
packages can't provide (`ffmpeg`, `graphviz`), libraries with C bindings.

Optional section. Omit if the project has no system-level dependencies.

**2. `[languages.<name>]` — runtimes and package managers (installed second)**

```toml
[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"
```

One subtable per language runtime. `version` is required — no `"latest"`, reproducibility
matters. Usually 1–2 languages, but no artificial limit.

`manager` is optional — defaults to the language's standard package manager:

| Language | Default manager | Non-default examples |
|----------|----------------|---------------------|
| python   | pip            | uv, poetry, pipenv  |
| node     | npm            | pnpm, yarn          |
| rust     | cargo          | (rarely overridden)  |
| go       | go modules     | (rarely overridden)  |

Specify `manager` only when using a non-default. Non-default managers are installed
as a separate step after the runtime.

**3. `[startup]` — boot-up commands (run last, at container start)**

```toml
[startup]
commands = [
    "uv sync",
    "npm install",
    "bash scripts/setup-bmad-framework.sh",
]
```

Ordered list of commands run after container start. This is where:
- Repo dependencies get installed from the project's own files (`uv sync`, `npm install`)
- Agentic frameworks with non-standard setup procedures get configured
- Any arbitrary bash command can go — the open-ended escape hatch

Commands run as the container user (not root), in order. Fail-fast: a failing command
stops execution (see "Build & Startup Error Handling" section below).

Optional section. Omit if agents can figure out setup themselves (they have sudo).

### Dockerfile generation mapping

| TOML section | Dockerfile action | When |
|---|---|---|
| `[os]` packages | `RUN apt-get install -y ...` | build time (layer 1) |
| `[languages.*]` version | `RUN mise use --global <lang>@<version>` | build time (layer 2) |
| `[languages.*]` manager | `RUN mise use --global <manager>` or `pip install <manager>` | build time (layer 2) |
| `[startup]` commands | post-start script | container start |

The alcatrazer security base (phantom UID, gosu, git, mise, entrypoint) is always the
foundation — generated unconditionally, not configurable via this file.

### Language runtime installation: mise

**Decision:** Language runtimes are installed via `mise` — a multi-language version manager
already present in the alcatrazer base layer.

The existing Dockerfile (`src/alcatrazer/container/Dockerfile`) already uses this pattern:
```dockerfile
# Base layer: mise installed as agent user
RUN curl https://mise.run | sh
ENV PATH="/home/agent/.local/share/mise/shims:${PATH}"

# Language layer: mise installs runtimes
RUN mise use --global python@3.12 && \
    mise use --global node@22
```

**Why mise over alternatives:**

| | apt-get | mise | pyenv + nvm + ... |
|---|---|---|---|
| Version pinning | limited to distro | any version | any version |
| Language coverage | poor | all major languages | same, but one tool per language |
| Already in base | apt is always there | **yes** | no — each needs separate install |
| Syntax | inconsistent names | `mise use --global <lang>@<ver>` | different per tool |
| Dockerfile complexity | PPA management for non-default versions | one RUN per language | multiple install steps, PATH per tool |
| Maintenance | low per tool, PPA repos go stale | one tool | N tools, N update cycles |

**apt-get** was rejected because version availability is limited to what the distro ships.
Ubuntu 24.04 has Python 3.12 but not 3.11 or 3.13 without PPAs. Node is usually outdated.
Users need to pin exact versions for reproducibility.

**Language-specific managers** (pyenv, nvm, rustup, rbenv, etc.) were rejected because
they add complexity with no benefit over mise — multiple install steps in the base layer,
inconsistent interfaces, separate PATH management per tool.

**mise covers:** Python, Node.js, Ruby, Go, Rust, Java, Bun, Deno, PHP, Erlang/Elixir,
.NET, Zig, Lua, R, and non-language tools (terraform, kubectl, etc.).

**Build speed note:** Some languages (notably Python) may compile from source via mise,
which is slow. mise has been adding prebuilt binary support (via python-build-standalone).
This only affects Docker build time — once the image is built, the layer is cached.

**Package managers:** Default managers that ship with the language (pip, npm, cargo) are
available immediately after mise installs the runtime. Non-default managers (uv, pnpm,
yarn, poetry) are installed as a separate step — either via mise (`mise use --global uv`)
if supported, or via the language's own installer (`pip install uv`, `npm install -g pnpm`).

### Examples

**Minimal (Python-only project):**

```toml
[languages.python]
version = "3.12"
```

One language, default manager (pip), no OS packages, no startup commands.
Agent has sudo — it can install what it needs.

**Typical (Python backend + TypeScript frontend):**

```toml
[os]
packages = ["build-essential", "libpq-dev"]

[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"

[startup]
commands = [
    "uv sync",
    "npm install",
]
```

**Complex (multi-language with agentic framework):**

```toml
[os]
packages = ["build-essential", "libpq-dev", "ffmpeg", "graphviz"]

[languages.python]
version = "3.12"
manager = "uv"

[languages.node]
version = "22"
manager = "pnpm"

[startup]
commands = [
    "uv sync",
    "pnpm install",
    "bash scripts/setup-bmad-framework.sh",
]
```

### What's NOT in this file

- **Language libraries** — live in `requirements.txt`, `package.json`, `Cargo.toml`, etc.
  Installed by startup commands or by agents at runtime.
- **Alcatrazer configuration** — lives in `.alcatrazer/config.toml` (gitignored).
- **Promotion identity** — lives in `.alcatrazer/config.toml` (sensitive, per-developer).
- **Daemon settings** — lives in `.alcatrazer/config.toml`.
- **Anything that reveals Alcatrazer** — no branding, no security config, no tool-specific
  comments.

## Build & Startup Error Handling

Installation from `coding-environment.toml` is a pipeline that can fail at each stage.
Part of Alcatrazer's job is detecting these failures, reporting them clearly, and guiding
the developer through the fix cycle.

### Two failure times

The pipeline executes across two distinct phases:

**Build time** (`docker build` — image creation):
- `[os]` packages — typo in package name, package doesn't exist, dependency conflict,
  network unreachable
- `[languages.*]` runtimes — version doesn't exist, build from source fails
  (missing OS dependency), network unreachable
- `[languages.*]` managers — installation script fails, network unreachable

**Container start time** (after image is built):
- `[startup]` commands — `uv sync` fails (broken `pyproject.toml`), `npm install` fails
  (native module needs a missing OS dep), custom script errors out

This distinction matters:
- **Build-time failure** — no image created, cannot start. Very visible, hard to miss.
- **Start-time failure** — image exists, container starts, but environment is broken.
  More subtle, can silently leave agents in a broken state if not handled.

### Fail fast

Each phase runs in dependency order. If a step fails, execution stops immediately.
No point running language installation if OS packages failed — the missing system
dependency will just cause a different error downstream.

The pipeline:
```
[os] packages  →  fails?  →  STOP, report
       ↓ ok
[languages] runtimes + managers  →  fails?  →  STOP, report
       ↓ ok
--- image built, layers cached ---
[startup] command 1  →  fails?  →  STOP, report
       ↓ ok
[startup] command 2  →  fails?  →  STOP, report
       ↓ ok
...
READY
```

### Error reporting: three things the developer needs

Every error message must provide:

1. **Which phase failed** — "OS packages", "Python 3.12 installation",
   "startup command #2 (`npm install`)"
2. **The actual error output** — the raw output from apt-get/pip/npm/bash,
   not just "failed". Developers read error messages — show them.
3. **Which TOML entry caused it** — point back to the file and the specific section
   so the developer knows where to edit.

Example:
```
ERROR: Build failed during [os] packages installation.

  apt-get install -y libpqdev
  E: Unable to locate package libpqdev

  → Check [os] packages in coding-environment.toml
  → Did you mean "libpq-dev"?
```

Example:
```
ERROR: Startup command #2 failed.

  npm install
  npm ERR! gyp ERR! build error
  npm ERR! gyp ERR! not ok
  ...node-gyp rebuild failed: missing python...

  → Check [startup] commands in coding-environment.toml
  → The build tool needs Python. Add it to [languages] or [os] packages.
```

### The fix-rebuild loop

The developer's cycle after a failure:

```
see error → edit coding-environment.toml → rebuild → see if it passes
```

**Docker layer caching makes this fast.** Because each TOML section maps to a Dockerfile
layer, Docker caches successful layers and only re-runs from the changed layer onward:

| What you fix | What re-runs | Cost |
|---|---|---|
| `[startup]` command | nothing rebuilt, just restart container | cheapest — seconds |
| `[languages]` version/manager | language layer + everything after | medium — minutes |
| `[os]` package | OS layer + everything after | most expensive — minutes |

This means the most common fix (wrong startup command) is also the cheapest.
And a fix to an earlier layer doesn't waste the work done by later layers on previous
successful builds — Docker re-runs from the changed point.

### Suggested fixes for common errors

For known error patterns, alcatrazer can suggest fixes rather than just showing raw output:

| Error pattern | Suggestion |
|---|---|
| `Unable to locate package X` | Fuzzy match against known packages, suggest correct name |
| `Python/Node version X not found` | List available versions |
| `command not found: uv` | "Add `manager = \"uv\"` to the language's section" |
| `fatal error: X.h: No such file` | "Missing system header — add the `-dev` package to `[os]`" |
| Startup command exits non-zero | Show the command, its output, its index in `[startup]` |

Smart suggestions are a convenience, not a requirement for MVP. The essential contract is:
**never fail silently, always show what failed and where to edit.**

## CLI: Three Commands

**Design goal:** Minimalism. Developers are tired of learning new tools. Alcatrazer
should be operable and invisible — as few commands as possible, no Docker vocabulary
leaking through (no `up`/`down`/`build`/`compose`), no separate init to learn.

### The commands

```
alcatrazer start                     the only command for daily work
alcatrazer stop                      stop the container
alcatrazer upgrade                   check for and install new alcatrazer version
```

Everything else is a flag on `start` or `upgrade`:

```
alcatrazer start --run-selftest      also run security self-tests after starting
alcatrazer start --verify-checksum   also verify installed source against GitHub
alcatrazer start --rebuild           force full rebuild even if nothing changed
alcatrazer upgrade --dry-run         check for new version without installing
```

### `alcatrazer start` — does the right thing

`start` is always safe to run. It detects the current state and does what's needed:

```
alcatrazer start
       │
       ├── no .alcatrazer/ ?
       │       → first time: interactive questions → generate everything
       │         → build image → create workspace snapshot → start
       │
       ├── .alcatrazer/ exists, generate would-be Dockerfile in memory
       │   and compare against existing .alcatrazer/Dockerfile
       │       │
       │       ├── Dockerfile would differ?
       │       │       → [os] or [languages] changed
       │       │       → rebuild image, restart, run startup commands
       │       │
       │       └── Dockerfile identical?
       │               → just (re)start container, run startup commands
       │               → covers [startup]-only changes: no rebuild needed
       │
       └── container already running, Dockerfile identical?
               → no-op: "already running, environment up to date"
```

**Smart detection without heuristics:** The key insight is comparing the would-be
generated Dockerfile against the existing one. Since `[startup]` commands don't go
into the Dockerfile (they run at container start via a post-start script), changing
only `[startup]` won't change the generated Dockerfile — no rebuild, just restart.
Changes to `[os]` or `[languages]` change the Dockerfile — rebuild triggered.
The detection is exact, not a guess.

After each successful build, the current `coding-environment.toml` is also copied
to `.alcatrazer/coding-environment.toml.last` as a human-readable record of what
the last build used.

### `alcatrazer stop` — stop the container

No flags, no options. Just stop.

### `alcatrazer upgrade` — self-update

Checks PyPI for a newer version of alcatrazer and installs it into `.alcatrazer/`.

```
$ alcatrazer upgrade
Current: 0.2.0
Latest:  0.3.1
Upgrading... done.
Run 'alcatrazer start --rebuild' to apply changes to the container.

$ alcatrazer upgrade --dry-run
Current: 0.2.0
Latest:  0.3.1
Run 'alcatrazer upgrade' to install.
```

Per-repo upgrade — consistent with per-repo install. Each repo controls its own version.

### `--run-selftest` — verify security properties

Runs the bundled test suite that verifies: phantom UID isolation, no credential leaks,
no Docker socket, no git remotes, identity rewriting, file ownership.

Named `--run-selftest` (not `--test`) to make clear this tests alcatrazer itself,
not the target repository's test suite.

### `--verify-checksum` — verify source integrity

Verifies installed source code against `SHA256SUMS` from GitHub Releases.
A convenience — the user can always do this manually with `sha256sum -c`.

### What the user experiences

**First time:**
```
$ alcatrazer start
No alcatrazer setup found. Starting interactive setup...

What languages does this project use?
> Python 3.12

Package manager? [pip] / uv / poetry
> uv

... (questions about promotion identity, etc.) ...

Generated: coding-environment.toml
Generated: .alcatrazer/config.toml
Written:   .git/info/exclude
Building container image... ████████████████ done (47s)
Creating workspace snapshot... done
Starting container...
Running startup commands...
  ✓ uv sync
Ready.
```

**Daily work:**
```
$ alcatrazer start
Container started. Environment up to date.

$ alcatrazer stop
Container stopped.
```

**After editing coding-environment.toml ([os] or [languages]):**
```
$ alcatrazer start
coding-environment.toml changed — rebuilding image.
Building... ████████████████ done (12s)
Restarting container...
Running startup commands...
  ✓ uv sync
  ✓ npm install
Ready.
```

**After editing coding-environment.toml ([startup] only):**
```
$ alcatrazer start
Restarting container...
Running startup commands...
  ✓ uv sync
  ✓ pnpm install
Ready.
```

**Startup failure:**
```
$ alcatrazer start
Running startup commands...
  ✓ uv sync
  ✗ npm install
    npm ERR! gyp ERR! build error — missing python

ERROR: Startup command #2 failed.
  → Check [startup] commands in coding-environment.toml
```

User fixes toml, runs `alcatrazer start` again.

## Parked Questions (Future Extensions)

The following were explicitly parked (2025-04-20) to avoid opening an endless decision
space and to focus on a buildable MVP targeting Docker.

1. **Sysbox as optional backend** — could Alcatrazer use Sysbox on Linux where available
   (better isolation, no UID gymnastics) and fall back to regular Docker on macOS?
   Or is maintaining two backends too complex?

2. **Can the isolation mechanism be pluggable?** Define the security fundamentals
   (filesystem, secret, identity, process, git isolation) as an interface,
   then implement backends: Docker, Sysbox, Podman, etc.

3. **How to handle existing `.devcontainer/` repos?** If we don't become
   a devcontainer ourselves, how do we coexist with existing devcontainer setups?
   Do we read the devcontainer config as a "recipe" and build our own parallel
   secure container from it?

4. **How common are coding Dockerfiles in the wild?** If most Alcatrazer users start
   from scratch, the "wrap existing" problem can be deferred.
   If many have devcontainers, it's a priority.