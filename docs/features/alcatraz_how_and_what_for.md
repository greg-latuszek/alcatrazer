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
- The `.alcatrazer/` directory exists (gitignored, but visible)
- `alcatrazer.toml` exists (version controlled — the user's one deliberate config file)
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
- JetBrains (Gateway, IntelliJ)
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

```json
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

### What Devcontainers Get Right (Relevant to Us)

- **Declarative** — environment defined in a config file, not manual setup
- **Composable** — features can be layered without editing Dockerfiles
- **Standard** — multiple tools understand the same config format

### Alcatrazer as a Devcontainer Feature?

The features mechanism is interesting. A devcontainer feature is a small package
(shell script + metadata) published as an OCI artifact. It can install packages,
create users, add entrypoint scripts, set environment variables.

Alcatrazer's security layer could theoretically be a devcontainer feature:

```json
{
  "features": {
    "ghcr.io/greg-latuszek/alcatrazer/feature:1": {}
  }
}
```

The feature would inject: phantom UID user, entrypoint wrapper, git identity isolation,
mount restrictions.

### The Fundamental Tension with Devcontainers

Devcontainers were designed to **help** developers, not to **isolate from** them.
The spec assumes the container is a trusted environment. It has:

- SSH key mounting built in (`mounts`)
- Git credential forwarding by default
- Host folder mounting as the workspace
- Port forwarding to localhost

These are exactly the things Alcatrazer is trying to **prevent**. We'd be fighting
the spec's defaults.

If a developer already has a `.devcontainer/`, it's likely configured with SSH keys
mounted, git credentials forwarded — all the things we'd strip away. The agent-facing
environment should be a *restricted* version of their devcontainer, not the full thing.

### Open Question

Does this mean we don't really "extend" a user's devcontainer but rather
**use it as a recipe for what tools to install**, while replacing the
security-sensitive parts (mounts, credentials, identity, entrypoint)?

---

## Open Questions

1. **Detection heuristics for build vs coding Docker** — is asking the user during
   `alcatrazer init` sufficient, or should we autodetect? Signals for build Docker:
   multi-stage with small final stage, `EXPOSE`, `CMD` runs the app. Signals for
   coding Docker: `.devcontainer/`, dev tools installed, interactive, `CMD ["/bin/bash"]`.

2. **Sysbox as optional backend** — could Alcatrazer use Sysbox on Linux where available
   (better isolation, no UID gymnastics) and fall back to regular Docker on macOS?
   Or is maintaining two backends too complex?

3. **Devcontainer feature approach** — is it viable to package the security layer as a
   devcontainer feature, given that we need to *remove* default devcontainer behaviors
   (credential forwarding, SSH mounts) rather than add to them?

4. **How common are coding Dockerfiles in the wild?** If most Alcatrazer users start
   from scratch (scenarios A/B), the "wrap existing" problem can be deferred.
   If many have devcontainers, it's a priority.

5. **Can the isolation mechanism be pluggable?** Define the security fundamentals
   (filesystem, secret, identity, process, git isolation) as an interface,
   then implement backends: Docker, Sysbox, Podman, etc.