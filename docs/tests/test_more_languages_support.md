# Manual test plan — `more_languages_support` (Phase 1)

## Purpose

Companion to [`docs/features/more_languages_support.md`](../features/more_languages_support.md).
Phase 1 (sub-phases 1.1 → 1.2.6) is covered by automated unit tests inside
`src/alcatrazer/tests/`, but several seams between sub-phases — Docker image
build, multi-alcatraz coexistence, in-container behavior, identity-leak audit,
and the new self-healing image detection — can only be exercised end-to-end
on a real host.

This plan is the manual-test checklist run before tagging a release that
includes Phase 1. Each section maps to one risk surface and references the
sub-phase it's verifying. Run on a clean Linux host (Ubuntu 24.04 primary,
macOS secondary).

## Setup

```bash
# Build alcatrazer from this branch
cd ~/gitrepos/train_ai/agents_in_sandbox
uv build

# Two test repos at different paths, each will become an Alcatraz
mkdir -p /tmp/alcatraz-test/{repo-py,repo-dotnet,repo-java,repo-multi}
for d in repo-py repo-dotnet repo-java repo-multi; do
  cd /tmp/alcatraz-test/$d
  git init
  echo "# $d" > README.md
  git add . && git commit -m "init"
done
```

Have a second terminal ready (host-side) for `docker ps`, `docker images`,
`docker inspect` cross-checks throughout.

## A. Fresh-init wizard UX (Phase 1.2.1, 1.2.3, 1.2.4)

**Goal: confirm the wizard reads naturally, banners render, tips display
correctly in both stdout and the generated TOML.**

For each language (run in separate fresh repos):

1. `cd /tmp/alcatraz-test/repo-py && alcatrazer init`
   - [ ] Intro panel: ASCII diagram with `your repo ←promote— Alcatraz`,
         mentions fake throwaway identity
   - [ ] `=== Promotion identity ===` banner before identity prompt
   - [ ] `=== Languages ===` banner mentions "baked into Alcatraz"
   - [ ] `Tip: Version examples: 3.11, 3.12, 3.13.` printed BEFORE
         `Version for python:` (blank line above tip, not below)
   - [ ] Manager tip mentions `pip (bundled with Python)` before
         `Package manager for python? [pip] / uv / poetry / pipenv:`
   - [ ] `=== System packages ===` banner with "baked"
   - [ ] `=== Startup commands ===` banner with "every time" +
         "NOT baked"
   - [ ] Closing message:
         `to build Alcatraz and run it with own git, then \`alcatrazer visit\` to step inside.`
   - [ ] No `workspace` / `image` words leaked in closing
2. Inspect the generated `coding-environment.toml`:
   - [ ] `schema_version = 1` is the FIRST data line
   - [ ] Tips appear as `# `-prefixed comments above each `version =`
         and `manager =`
   - [ ] `manager = "pip"` IS present even though pip is the default
         (Phase 1.2.4)
   - [ ] Long java tip wrapped at ~76 cols if java picked
3. Repeat with `dotnet`, `java`, then a multi-language combo
   `python, node, dotnet, java`.

## B. Build & run per language (Phase 1.2, 1.2.2, 1.2.4, 1.2.7)

For each `{python, node, rust, go, dotnet, java}`, after init run
`alcatrazer start`:

- [ ] Build succeeds (no apt errors, no mise errors, no missing-plugin
      errors)
- [ ] Closing line:
      `Ready. To enter the Alcatraz: alcatrazer visit`

Then `alcatrazer visit` and inside the container:

| Lang   | Verify |
|--------|--------|
| python | `python --version`, `pip --version` (bundled). Then **per-manager rebuild check** — the Phase 1.2.4 bundled-vs-installable rule plus the Phase 1.2.7 uv attestation workaround. For each of `manager = "uv"`, `"poetry"`, `"pipenv"`: edit toml, `alcatrazer start` (image rebuilds), `alcatrazer visit`, run `which <mgr> && <mgr> --version`. uv is the load-bearing case — its build path goes through the attestation workaround in `_render_mise_uses` (split RUN with `MISE_AQUA_GITHUB_ATTESTATIONS=false` prefix and explanatory comment). Cross-check the saved Dockerfile at `.alcatrazer/Dockerfile` shows that prefix + comment when uv is picked, and shows neither when uv isn't. |
| node   | `node --version`, `npm --version`, then `manager = "pnpm"` → `pnpm --version` |
| rust   | `cargo --version`, `rustc --version` |
| go     | `go version` |
| **dotnet** | `dotnet --version` returns `10.0.100` (the libicu74 fix; this is the bug Phase 1.2 caught) — also `apt list --installed 2>/dev/null \| grep libicu` |
| **java** | `java -version 2>&1` shows Temurin 21, `mvn -version` works (the Phase 1.2.4 bundled-managers fix — **user-reported bug**), then test `manager = "gradle"` → `gradle --version` |

**Java distribution probe** (Phase 1.2.2): `version = "corretto-21"` in
`[languages.java]`, rebuild, verify `java -version 2>&1` shows Corretto.

**uv attestation workaround removal probe** (Phase 1.2.7): periodically
re-check whether the workaround is still needed. With `manager = "uv"`,
hand-edit `.alcatrazer/Dockerfile` to drop the
`MISE_AQUA_GITHUB_ATTESTATIONS=false` prefix from the uv RUN line, then
`docker build` from `.alcatrazer/`. If the build now succeeds, upstream
has caught up — open a PR removing `"uv"` from
`languages.AQUA_ATTESTATION_MISALIGNED`. If it still fails with the
attestation-mismatch error, the workaround stays.

## C. Inmates-not-aware-of-Alcatraz audit (Principle 2)

Inside the container via `alcatrazer visit`:

- [ ] `cat /proc/self/mountinfo | grep -i alcatraz` → no hits (mount path
      is sibling `.devspace-*`)
- [ ] `env | grep -i alcatraz` → no hits
- [ ] `git config --get user.name` and `user.email` → random human-looking
      name, NOT yours
- [ ] `git log --format='%an <%ae>'` → only the random identity (one
      initial commit, generic message)
- [ ] `hostname`, `whoami` (should be `agent`), `id -u` → phantom UID not
      matching host user
- [ ] `ls -la ~` → no alcatrazer dotfiles
- [ ] `ps -ef` → no `alcatraz` in process names

## D. `alcatrazer visit` semantics (Phase 1.2.5)

- [ ] In `repo-py` (initialized but not started): `alcatrazer visit` →
      exit 1, stderr mentions `alcatrazer start`
- [ ] In `/tmp` (no `.alcatrazer/`): `alcatrazer visit` → exit 1, stderr
      mentions `alcatrazer init`
- [ ] After `alcatrazer start`: `alcatrazer visit` opens interactive bash,
      prompt is `agent@…`
- [ ] Inside bash: `Ctrl+D` exits cleanly back to host shell, exit code 0
- [ ] Inside bash: `Ctrl+C` on a `sleep 30` interrupts cleanly (signal
      flows through `execvp`)
- [ ] Run `alcatrazer visit` while another `visit` session is open → both
      work concurrently

## E. Multi-alcatraz coexistence (Phase 1.2.5)

The headline new behavior. Open two host terminals:

1. Terminal A:
   `cd /tmp/alcatraz-test/repo-py && alcatrazer init && alcatrazer start`
2. Terminal B:
   `cd /tmp/alcatraz-test/repo-dotnet && alcatrazer init && alcatrazer start`
   - [ ] Second `start` does NOT collide with
         `Container name "/workspace" already in use`
3. Host: `docker ps --format '{{.Names}}\t{{.Image}}'`
   - [ ] Two distinct `workspace-repo-py-<hash12>` and
         `workspace-repo-dotnet-<hash12>` containers
   - [ ] Two distinct `alcatraz-workspace:repo-py-<hash12>` and
         `…repo-dotnet-<hash12>` images
4. Terminal A: `alcatrazer visit` → lands in repo-py's container (verify
   with `pwd` showing repo-py contents)
5. Terminal B: `alcatrazer visit` → lands in repo-dotnet's container
6. Stop & restart one: identity is **stable** — same image tag and
   container name after `alcatrazer clear && alcatrazer start`

**Symlink probe**:
`ln -s /tmp/alcatraz-test/repo-py /tmp/alcatraz-test/repo-py-link &&
cd /tmp/alcatraz-test/repo-py-link && alcatrazer start` — should reuse
the same image (canonical path resolution).

## F. Stale-image self-healing (Phase 1.2.6) — highest-risk new logic

Three scenarios, each in `repo-py` after a clean `init` + `start`:

1. **TOML edit, no init re-run**:
   - Edit `coding-environment.toml` to add `node` to languages
   - `alcatrazer start` → [ ] message mentions stale/rebuild, image
     rebuilds, new node appears in container
2. **`rm -rf .alcatrazer/` recovery** (the headline bug Phase 1.2.6
   closes):
   - `rm -rf .alcatrazer/ .devspace-*/`
   - Edit `coding-environment.toml` (e.g., bump python version `3.12`
     → `3.13`)
   - `alcatrazer init` (accept reuse prompt — see G below) `&&
     alcatrazer start`
   - [ ] Image rebuilds — `python --version` inside container reflects
     3.13, NOT the stale 3.12
3. **Idempotent restart** (no false positives):
   - `alcatrazer clear && alcatrazer start` with no toml changes
   - [ ] No rebuild fires, fast start, image not regenerated

**Cross-check via host**:
`docker inspect alcatraz-workspace:repo-py-<hash> --format '{{ index .Config.Labels "alcatrazer.config_hash" }}'`
returns 16 hex chars. Edit toml, rebuild, inspect again → different hash.

## G. Re-init reuse-prompt (Phase 1.2.3)

In `repo-py` (already has alcatrazer-generated `coding-environment.toml`):

1. `rm -rf .alcatrazer/ .devspace-*/ && alcatrazer init`
   - [ ] Prompt: `Detected existing coding-environment.toml from a
     previous alcatrazer install. Reuse it as-is? [Y/n]`
2. **Y path**: press Enter → wizard skips languages/os/startup, prints
   `Reusing coding-environment.toml.`
3. **n + y path**: re-run init, `n` then `y` to overwrite → wizard runs,
   file overwritten, no hex-suffix orphan
4. **n + n path**: re-run init, `n` then `n` → hex-suffix file
   (`coding-environment-XXXX.toml`) created, original preserved
5. **User-authored config** (no alcatrazer headers): create a
   `coding-environment.toml` by hand (no
   `# Coding environment definition…` header), run init → no reuse
   prompt, hex-suffix path

## H. Schema version error path (Phase 1.1)

- [ ] Hand-edit `coding-environment.toml` to `schema_version = 2`, run
      `alcatrazer start` → exit 1, single `ERROR:` stderr line mentioning
      "upgrade alcatrazer", **no Python traceback**
- [ ] Same with `schema_version = 0` → similar clean error message
- [ ] Delete the `schema_version` line entirely → loads as v1, works fine
      (backwards compat)

## I. No-creds branch (worth a quick pass)

- [ ] On a host with `~/.claude/` removed/renamed, run `alcatrazer init`
      → wizard prompts for API key, closing message also mentions
      `alcatrazer visit`

## J. Promotion (KNOWN BROKEN in 0.0.4 and 0.1.0)

> **Note:** Manual testing of this section in 0.0.4 revealed that
> default `mirror` mode rewrites outer branch history and leaves the
> working tree desynced from `HEAD`. The behaviour is unchanged in
> 0.1.0 (a licence-change-only release; no promotion code was
> modified). See
> [`docs/features/change_promotion_machinery.md`](../features/change_promotion_machinery.md)
> and [`docs/tests/test_promotion_diagnosis.md`](test_promotion_diagnosis.md)
> (the discriminating test that surfaced the bug). The fix lands in 0.1.1.
>
> For 0.0.4 / 0.1.0 release validation, run section J only as a
> **regression-pin test** — confirm the broken behavior matches what
> the design doc describes, so we know the bug surface is what we
> documented.

- [ ] `alcatrazer start`, agent makes a commit inside Alcatraz, daemon
      promotes it
- [ ] Outer repo `git log` on `main`: original "outer initial" commit is
      NO LONGER an ancestor (matches documented bug)
- [ ] Outer working tree: `git status` shows phantom "deleted" entry for
      the agent's new file (matches documented bug)
- [ ] No alcatraz strings leak into outer repo's commit messages or
      identities (Principle 2 still holds for what *does* land)

When 0.1.1 ships, this section gets rewritten to assert the *fixed*
behavior: outer history preserved, working tree atomically updated,
agent files visible immediately under user's identity.

## K. Pre-1.2.5 upgrade scenario (one-time cost)

If you have any existing alcatraz on this machine built before this
branch:

- [ ] `docker rmi alcatraz-workspace:local; docker rm -f workspace` (per
      README upgrade note)
- [ ] `cd <old-repo> && alcatrazer start` → builds fresh per-repo image,
      no error

---

## Suggested run order

A → B → C → D → E → F → G → H → I → J. Skip K if no pre-branch alcatraz
exists on the host.

## When this plan runs

- Before tagging any release that includes Phase 1 changes (0.0.4 and
  later).
- After any change to wizard flow, Dockerfile generator, image-tag
  derivation, or `Alcatraz` port methods.
- The 0.1.1 release (promotion rewrite) keeps every section here
  unchanged except section J, which becomes a positive validation
  instead of a regression-pin.

## Maintenance

Every Phase 1.X sub-phase in
[`docs/features/more_languages_support.md`](../features/more_languages_support.md)
maps to a section here:

| Sub-phase | Section |
| --------- | ------- |
| 1.1 — schema versioning | H |
| 1.2 — dotnet | A, B |
| 1.2.1 — wizard self-explanation | A |
| 1.2.2 — java | A, B |
| 1.2.3 — version_tip + reuse-prompt | A, G |
| 1.2.4 — manager always written / bundled | A, B |
| 1.2.5 — per-repo naming + visit | D, E, K |
| 1.2.6 — config_hash LABEL | F |
| 1.2.7 — uv attestation workaround | B |

When a new Phase 1.X sub-phase lands, extend the relevant section above
or add a new one and update this table.