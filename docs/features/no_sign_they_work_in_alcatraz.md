# No Sign They Work in Alcatraz

## Status: Planning

## Goal

Agents inside the Docker container must not be able to discover that their environment was created by Alcatrazer. If an agent realizes it is inside an Alcatraz workspace, it can:

1. Search GitHub for "alcatrazer"
2. Read the tool's source code
3. Identify weaknesses in the isolation mechanism
4. Attempt to exploit them

The container should look like a **standard Linux development environment** with a git repository the agent is asked to code in. Nothing more. No traces of the tool that built the walls.

This is Principle 2: **inmates are not aware they live in Alcatraz.**

## Known Footprints

### 1. Git identity: "Alcatraz Agent" / "alcatraz@localhost"

**Where:** Dockerfile line 46-47, also set by `initialize_alcatraz.sh` in the workspace's local git config.

**Visible to agents via:**
- `git config user.name` → "Alcatraz Agent"
- `git config user.email` → "alcatraz@localhost"
- `git log` → every commit shows "Alcatraz Agent <alcatraz@localhost>"

**Risk:** An agent searching for "Alcatraz" finds our GitHub repo immediately.

### 2. Container name: "agent-alcatraz"

**Where:** `docker-compose.yml` line 18: `container_name: agent-alcatraz`

**Visible to agents via:**
- `hostname` inside the container may reflect the container name
- `cat /etc/hostname`
- Environment variables like `HOSTNAME`

### 3. Docker image/stage names: "alcatraz-base", "alcatraz-dev"

**Where:** Dockerfile stage names (`alcatraz-base`, `alcatraz-dev`), docker-compose target (`alcatraz-dev`), service name (`alcatraz`).

**Visible to agents via:**
- Possibly in `/proc` filesystem or environment variables
- Image metadata if agents can inspect it

### 4. Environment variable: ALCATRAZ_UID

**Where:** Passed via `.alcatraz/uid.env`, referenced in Dockerfile and docker-compose.yml.

**Visible to agents via:**
- `env | grep -i alcatraz` → `ALCATRAZ_UID=...`
- `printenv ALCATRAZ_UID`

### 5. Build-time error message

**Where:** Dockerfile line 15: `"ERROR: ALCATRAZ_UID build arg is required. Run ./initialize_alcatraz.sh first."`

**Visible to agents:** Only during build failure, not at runtime. Low risk but worth cleaning.

### 6. Dockerfile comments

**Where:** Multiple references to "Alcatraz", "alcatrazer.toml", "Principle 2" in Dockerfile comments.

**Visible to agents:** Not at runtime (comments are not in the image layers). But if image build layers are cached and inspectable, some metadata may leak. Low risk.

## Investigation Results

- **Process names / cgroup paths** — cgroups show container IDs (hex hashes), not image/service names. **Clean.**
- **Mount points** — **LEAK FOUND.** `/proc/self/mountinfo` exposes the full host path of bind mounts. Agents can see `.alcatraz/workspace` in the mount source path. Verified experimentally.
- **Docker labels / image metadata** — agents can't run `docker inspect` (no socket mounted). Labels are not in `/proc/1/environ`. **Clean.**
- **Shell history / config** — fresh container, default Ubuntu `.bashrc`. **Clean.**
- **Claude credentials path** — `~/.claude/.credentials.json` is standard Claude Code layout. **Clean.**
- **Cached package manager metadata** — volume names (`mise-cache`, `pip-cache`, etc.) are generic. **Clean.**

### 7. Mount path leak: `.alcatraz/workspace` in /proc/self/mountinfo

**Where:** Host bind mount path visible inside container via `/proc/self/mountinfo`.

**Visible to agents via:**
- `cat /proc/self/mountinfo | grep workspace` → shows `/path/to/.alcatraz/workspace /workspace`
- `mount` command may also show it

**Risk:** Directly reveals the `.alcatraz` directory name on the host.

**Fix options investigated:**
- ~~Rename `.alcatraz/`~~ — rejected. `.alcatraz/` is the user-facing brand, should stay.
- ~~Symlink with generic name~~ — rejected. Docker resolves symlinks; mountinfo still shows real path. Verified experimentally.
- ~~Run docker-compose from inside `.alcatraz/`~~ — rejected. Docker resolves relative paths to absolute. Verified experimentally.
- **Docker named volume** — works (no host path in mountinfo), but heavy impact: all host-side tools (promotion daemon, snapshot, reset) lose direct filesystem access to the workspace. Would require running them inside containers or via `docker cp`.
- **Accept as known limitation** — the leak requires an agent to parse `/proc/self/mountinfo`, recognize `.alcatraz` as meaningful, and find our repo. Low probability compared to the git identity which literally says "Alcatraz." **Chosen for now — revisit if threat model changes.**

## Resolved Questions

### 1. Git identity: random realistic human name

Generated once per `initialize_alcatraz.sh` run. Stored in `.alcatraz/` for reuse.

**Name generation:**
- 50 most popular English first names (mix of male/female)
- 50 most popular English last names
- Random draw: one first + one last = 2500 possible combinations

**Email generation** — random pattern from:
- `{firstname}.{lastname}{0-2 digits}@{domain}` (e.g., `james.smith42@gmail.com`)
- `{first_initial}{optional_underscore}{lastname}{0-2 digits}@{domain}` (e.g., `j_smith7@yahoo.com`)

**Domain pool** — ~20 most popular email domains:
- gmail.com, yahoo.com, outlook.com, hotmail.com, icloud.com, protonmail.com, aol.com, mail.com, zoho.com, fastmail.com, yandex.com, gmx.com, tutanota.com, live.com, msn.com, me.com, inbox.com, pm.me, hey.com, duck.com

**Storage:** `.alcatraz/agent-name` and `.alcatraz/agent-email` (or a single `.alcatraz/agent-identity` file).

**Promotion doesn't need it.** `promote.py` uses a blind regex — it overwrites whatever author/committer is in the fast-export stream with the target identity from `alcatrazer.toml`. The agent identity is never matched, just replaced.

**Where used:**
- `initialize_alcatraz.sh` Step 4: workspace git config (`user.name`, `user.email`)
- Dockerfile: global git config (must be passed as build args or generated inside container)

## Open Questions

1. ~~**What should the container/service/hostname be?**~~ **Resolved:** Rename Dockerfile stages from `alcatraz-base`/`alcatraz-dev` to `dev-base`/`dev`. Rename docker-compose service from `alcatraz` to `workspace`, container name from `agent-alcatraz` to `workspace`. We don't try to hide Docker — agents can trivially detect containers (`.dockerenv`, cgroups, etc.) and that's fine. Running in Docker is normal (devcontainers, Codespaces). The threat is agents discovering **Alcatrazer**, not Docker.

2. ~~**Should ALCATRAZ_UID be renamed?**~~ **Resolved:** Rename to `USER_UID` everywhere — typical Docker naming for container user setup. Remove `uid.env` from the runtime `env_file` list (UID is baked into the `agent` user at build time, no runtime reference needed). `initialize_alcatraz.sh` still generates `.alcatraz/uid.env` as `USER_UID=...` for docker-compose build consumption. Dockerfile error message becomes: `"ERROR: USER_UID build arg is required. Run initialization to obtain proper UID (see README for details)."`

3. ~~**Dockerfile identity: build-time vs runtime?**~~ **Resolved:** Remove git config from Dockerfile entirely. The workspace is mounted with local git config already set by `initialize_alcatraz.sh` (random identity). No global fallback needed — lack of global git config is normal in a Docker container. If agents `git init` outside `/workspace`, git will complain about missing identity, which prevents accidental commits outside the workspace.

---

## Detailed Implementation Plan

Each step is one commit, small enough for human review. Dependencies flow top to bottom.

**TDD discipline:** Each step follows the RED/GREEN/BLUE cycle where possible:
- `[RED]` commit — failing test for the planned functionality
- `[GREEN]` commit — implementation that makes the test pass
- `[BLUE]` commit — improvements/cleanup if applicable

### Phase 1: Random identity generator

A Python module that generates realistic-looking human identities for agents. Standalone, fully testable.

**Step 1.1** — `Generate random agent identity`
> A Python function that returns a (name, email) tuple. Name from 50 first + 50 last name pools. Email with random pattern: `firstname.lastname{0-2 digits}@domain` or `f_lastname{0-2 digits}@domain`. Domain from pool of ~20. Must be deterministic given a seed (for testing) but random by default.

**Step 1.2** — `Store and retrieve agent identity`
> Functions to write identity to `.alcatraz/agent-identity` and read it back. Format: two lines, name and email. If file exists, reuse it (identity is stable per workspace). If not, generate and store.

### Phase 2: Wire identity into initialization

Replace hardcoded "Alcatraz Agent" with the random identity in `initialize_alcatraz.sh`.

**Step 2.1** — `Generate identity during init, use in workspace git config`
> `initialize_alcatraz.sh` Step 4 (git init) calls the identity generator via `.alcatraz/python` to produce `.alcatraz/agent-identity`. Then reads name/email from it and sets workspace git config. Replaces hardcoded "Alcatraz Agent" / "alcatraz@localhost".
>
> The snapshot's initial commit (`snapshot.py`) already uses the workspace's git config — no changes needed there.

### Phase 3: Remove identity from Dockerfile

The Dockerfile no longer sets git identity. The workspace's local config is sufficient.

**Step 3.1** — `Remove git identity from Dockerfile`
> Remove the `git config --global user.name` and `git config --global user.email` lines from the Dockerfile. Keep `commit.gpgsign false` and `init.defaultBranch main` — these are standard container defaults, not identity.

### Phase 4: Rename ALCATRAZ_UID to USER_UID

Remove "alcatraz" from the build arg and runtime environment.

**Step 4.1** — `Rename ALCATRAZ_UID to USER_UID`
> Rename in: Dockerfile (ARG, RUN, error message), docker-compose.yml (build arg), `initialize_alcatraz.sh` (uid.env generation, variable names), smoke_test.sh (all references). The `.alcatraz/uid.env` file content changes from `ALCATRAZ_UID=...` to `USER_UID=...`.

**Step 4.2** — `Remove uid.env from runtime env_file`
> Remove the `../.alcatraz/uid.env` entry from docker-compose.yml `env_file` list. `USER_UID` is only needed at build time — it's already baked into the `agent` user. Verify with a test that `USER_UID` is not visible in the container's runtime environment.

### Phase 5: Rename container and service names

Remove "alcatraz" from all Docker naming visible to agents.

**Step 5.1** — `Rename Dockerfile stages and docker-compose service`
> Dockerfile: `alcatraz-base` → `dev-base`, `alcatraz-dev` → `dev`. Docker-compose: service `alcatraz` → `workspace`, container name `agent-alcatraz` → `workspace`, target `alcatraz-dev` → `dev`. Remove "alcatraz" and "alcatrazer" from Dockerfile comments. Update all references in smoke_test.sh, README, and any scripts that reference the service name.

### Phase 6: Smoke test and verification

Update the smoke test to verify zero Alcatraz footprint inside the container. This is the safety net — if any future change reintroduces a footprint, this catches it.

**Step 6.1** — `Update smoke test for new identity`
> Smoke test sections 2, 6, 7 currently assert "Alcatraz Agent". Update to read expected identity from `.alcatraz/agent-identity` and assert against that. Section 3 (env vars) should verify `ALCATRAZ_UID` is NOT present.

**Step 6.2** — `Add alcatraz-grep smoke test`
> New smoke test section: run `env && git config --list && cat /etc/hostname` inside the container, pipe through `grep -i alcatraz`. If any match is found, the test fails. This is a catch-all — if we miss a footprint, this finds it.

### Phase 7: Documentation

**Step 7.1** — `Update README and plan`
> Update README: remove "Alcatraz Agent" references, document that identity is randomly generated, update docker-compose command examples with new service name. Mark plan complete.

### Implementation Notes

Decisions to evaluate during implementation:
- **Identity file format:** Two lines (name, email) vs JSON vs TOML. Two lines is simplest and needs no parser.
- **Seed for testing:** `random.seed()` for deterministic tests, system entropy for production.
- **Smoke test scope:** The alcatraz-grep test is a powerful catch-all but may need exceptions for false positives (e.g., if the project IS Alcatrazer being dogfooded).
- **Migration:** Existing workspaces have "Alcatraz Agent" in their git config. `--reset` will fix this (re-init with new identity). No migration of existing workspaces without reset — document this.
