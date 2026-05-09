# License — Dependencies and Usage

## What's in this document

This is the companion artifact to [`licence_change_reasoning.md`](./licence_change_reasoning.md). The licence-change document explains *what changes and why* when Alcatrazer moves from MIT to Apache-2.0. **This document does the work behind the line "no dependency in the graph creates any incompatibility"** — it walks through every component Alcatrazer depends on at build time, distribution time, and runtime; states each component's licence; and explains why the graph is clean for Apache-2.0.

This document exists for the same reason as the licence-change document: Alcatrazer is built on **verifiable trust**. We don't want users to take "the dependency situation is fine" on faith. Here is exactly what we depend on, in what way, with the verification commands you can run yourself if you want to confirm any of it.

If you spot something we got wrong, or a dependency we missed, please open an issue. Compatibility analysis is checkable; we want it to be checked.

## Summary

- **The wheel Alcatrazer ships contains 100% Alcatrazer-authored content.** No third-party code is bundled. Apache-2.0 covers the wheel cleanly without any cross-licence complication.
- **At runtime, Alcatrazer shells out to other tools.** Process boundaries are not a licence-combining event in any major OSS licence. This is the same legal posture as Docker (Apache-2.0) calling git (GPLv2-only), or Bazel (Apache-2.0) calling Make (GPL).
- **The container Alcatrazer generates is built on the user's machine.** The Dockerfile is a build artifact the user runs `docker build` against; Alcatrazer never redistributes container binaries.
- **Net result:** Apache-2.0 is a clean fit for Alcatrazer's actual dependency graph. No bundled-licence-text obligations beyond shipping `LICENSE`, no copyleft induction, no remediation needed.

## What we mean by "dependency"

Software projects accumulate "dependencies" in several distinct ways, and the licence implications of each are different. To talk about Alcatrazer's situation precisely, we group them into five categories:

- **A. Bundled in the released wheel.** Code that travels inside Alcatrazer's distributable. The release's licence must cover all of this.
- **B. Python standard library.** Used at runtime; ships with Python itself, not with Alcatrazer's wheel.
- **C. Dev-only tooling.** Used by us during development, packaging, and CI. Never on a user's machine after install.
- **D. Host-side runtime.** Tools the user has installed on their machine that Alcatrazer invokes via `subprocess`.
- **E. Inner-container components.** Things pulled and installed *inside* the user-built container at user `docker build` time, not redistributed by Alcatrazer.

Each category has different licence-compatibility rules. We walk through each below.

## A. Bundled in the released wheel

The released artifact (`uv build` produces a `.whl` and `.tar.gz`) contains:

- `src/alcatrazer/*.py` — Alcatrazer's own Python source
- `src/alcatrazer/scripts/resolve_python.sh` — Alcatrazer's own bash
- `src/alcatrazer/container/entrypoint.sh` — Alcatrazer's own bash
- `src/alcatrazer/tests/seed_alcatraz.sh` — Alcatrazer's own bash
- `pyproject.toml`, `README.md`, `LICENSE`, `CHANGELOG.md`

**Zero third-party code is vendored.** Every byte in the wheel is content Alcatrazer authored.

Verification:

```
# 1. Confirm pyproject.toml declares no runtime dependencies
grep -A 3 '^\[project\]' pyproject.toml | grep -E '^(dependencies|requires-python)'
# → only `requires-python = ">=3.11"`; no `dependencies = [...]` at all.

# 2. Confirm Python imports are stdlib-only
grep -h "^import \|^from " src/alcatrazer/*.py | sort -u
# → only stdlib modules + `from alcatrazer …`. No third-party packages.

# 3. Confirm the wheel contents
uv build && unzip -l dist/alcatrazer-*.whl
# → only files under alcatrazer/ + dist-info metadata.
```

**Apache-2.0 implications:** because everything in the wheel is Alcatrazer-authored, Apache-2.0 covers it as the project's primary licence without any cross-licence combination concerns.

## B. Python standard library

At runtime, Alcatrazer's Python code uses these standard-library modules: `argparse`, `contextlib`, `dataclasses`, `datetime`, `fnmatch`, `hashlib`, `json`, `logging`, `logging.handlers`, `os`, `pathlib`, `random`, `re`, `secrets`, `shutil`, `signal`, `subprocess`, `sys`, `threading`, `time`, `tomllib`, `abc`, `io`. All Python 3.11+ standard library.

| Component | Licence | Apache-2.0 compatible? |
|---|---|---|
| Python ≥3.11 standard library | **PSF-2.0** (Python Software Foundation Licence v2) | **Yes.** Explicitly compatible per the Apache Foundation, FSF, and OSI. Tens of thousands of Apache-2.0 Python projects use it. |

Setting `requires-python = ">=3.11"` in `pyproject.toml` requires the user to have a Python interpreter installed; it does not bundle Python into the wheel and does not create a derivative-work relationship with Python.

## C. Dev-only tooling

These tools run during development, packaging, and CI. Users never install them when installing Alcatrazer.

| Tool | Used for | Licence | Apache-2.0 compatible? |
|---|---|---|---|
| `ruff` | linter / formatter | MIT | Yes |
| `build` | PEP-517 builder | MIT | Yes |
| `twine` | PyPI uploader | Apache-2.0 | Yes |
| `uv` | dev environment management (declared in `mise.toml`) | Apache-2.0 | Yes |
| `mise` | dev tool version management (declared in `mise.toml`) | MIT | Yes |
| GitHub Actions runners | CI infrastructure | various (Microsoft-managed) | Yes — CI infrastructure, not bundled with releases |

All licences are Apache-2.0-compatible. None of these tools are linked into Alcatrazer's wheel — they are operated as separate executables during development. The wheel does not contain ruff's source, twine's source, etc.

## D. Host-side runtime — invoked via subprocess

At runtime on the user's machine, Alcatrazer shells out to several tools that the user must have installed. This is invocation across a process boundary, not linking.

| Tool | Licence | Apache-2.0 interference? |
|---|---|---|
| Python ≥3.11 (interpreter) | PSF-2.0 | None — the user runs `alcatrazer` via their installed Python. |
| Bash | **GPLv3+** | **None.** `subprocess.run("bash")` is a process invocation, not a licence-combining event. |
| Docker / Docker Engine | Apache-2.0 | None. |
| `git` | **GPLv2-only** | **None.** Process boundary. Same legal posture as IDEs and CI tools that have shelled out to git for decades. |
| `curl` | curl licence (BSD/MIT-style) | None. |
| `mise` (optional, host-side) | MIT | None. Used by `resolve_python.sh` Tier 2/3 fallback to install Python 3.11+ for users who don't already have it. |

The two GPL tools — bash and git — deserve a brief note because some readers worry about "GPL contamination":

- **Subprocess invocation is not "linking" or "combining."** This is the canonical "safe" pattern for GPL interaction and has been since the GPL was written. The Free Software Foundation's own [GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html) confirms this. The Apache Foundation publishes Apache-2.0 software that invokes GPL tools (the `Apache HTTP Server` itself can run CGI scripts written in any language, including bash); this is a settled question.
- **Git's GPLv2-only licence is irrelevant here.** Apache-2.0's GPLv2 incompatibility only matters when you want to *combine source code* from an Apache-2.0 project with source from a GPLv2-only project into one binary. Alcatrazer never does this. It runs `git` as a separate executable.

## E. Inner-container components — built by the user, but partially specified by Alcatrazer

When the user runs `alcatrazer start`, Alcatrazer generates a Dockerfile and the user's Docker daemon builds an image from it. Bytes flow `upstream vendor → user's local storage`, never through Alcatrazer's distribution channel.

The Dockerfile has three stages, and they differ in *who chose what gets installed*:

- **Stage 1 — hardcoded by Alcatrazer.** Ubuntu base, fixed apt packages, mise.
- **Stage 2 — hardcoded by Alcatrazer (MVP).** Claude Code. The source comment in `docker_prison.py` flags this as MVP scope and notes the plan to make it user-selectable via an `[ai]` section in `coding-environment.toml`. Until that ships, every user gets Claude regardless of preference.
- **Stage 3 — user-declared.** Language runtimes and OS packages from the user's `coding-environment.toml`.

| Component | Pulled from | Licence | Authored by |
|---|---|---|---|
| `ubuntu:24.04` base image | Docker Hub (Canonical) | various per-package | Alcatrazer-hardcoded (Stage 1) |
| `git`, `curl`, `ca-certificates`, `gosu` (apt) | Ubuntu archives | GPLv2-only / curl / MPL-2.0+MIT-equiv / Apache-2.0 | Alcatrazer-hardcoded (Stage 1) |
| `mise` (`curl https://mise.run \| sh`) | mise.run | MIT | Alcatrazer-hardcoded (Stage 1) |
| Claude Code CLI (`curl https://claude.ai/install.sh \| bash`) | Anthropic | **Proprietary** (Anthropic ToS) | **Alcatrazer-hardcoded (Stage 2, MVP)** |
| User-declared language runtimes (Python, Node, Bun, …) | mise / aqua | various per language | User-declared (Stage 3) |

### Is "Alcatrazer-hardcoded install" a form of redistribution?

**No, in the legal sense.** Alcatrazer's wheel contains the text `RUN curl -fsSL https://claude.ai/install.sh | bash`, not the Claude binary itself. The text is Apache-2.0-covered Alcatrazer source; the binary flows from Anthropic to the user's Docker daemon at build time. Same legal posture as a Homebrew formula, an `apt` package definition, or any Dockerfile that says `apt-get install mongodb` — each upstream's licence governs that upstream's binary; Alcatrazer's licence governs Alcatrazer's own bytes. Apache-2.0 covers the arrangement cleanly.

**But "automatic installation" is a separate transparency concern, which is why this section is here.** A user running `alcatrazer start` today gets Claude Code installed in their inner container whether or not they asked for it. The generated `.alcatrazer/Dockerfile` is fully readable so users can see what gets pulled before they `docker build`. The current opt-out is *"don't run `alcatrazer start`"* or *"fork and edit `docker_prison.py`."* The planned change makes Stage 2 user-selectable so the only fully-hardcoded portion will be Stage 1's security primitives — which is the appropriate place for hardcoding, since those *are* Alcatrazer's identity as a security tool.

## How to verify this yourself

If you want to reproduce this analysis on your own checkout, the commands are simple:

```bash
# 1. Inspect declared dependencies
cat pyproject.toml | sed -n '/\[project\]/,/^\[/p'
# → Look for `dependencies = [...]`. Currently absent (no runtime deps).

# 2. Inspect actual Python imports
grep -h "^import \|^from " src/alcatrazer/*.py | sort -u

# 3. Inspect the wheel contents (after building)
uv build
unzip -l dist/alcatrazer-*.whl

# 4. Inspect everything the generated Dockerfile pulls in
# (search for FROM, apt-get, curl-pipe-sh, RUN)
grep -nE "^FROM |apt-get |curl .*\| (sh|bash)" src/alcatrazer/docker_prison.py

# 5. Verify each tool's licence
gh api repos/astral-sh/uv --jq '.license.spdx_id'      # Apache-2.0
gh api repos/astral-sh/ruff --jq '.license.spdx_id'    # MIT
gh api repos/jdx/mise --jq '.license.spdx_id'          # MIT
gh api repos/tianon/gosu --jq '.license.spdx_id'       # Apache-2.0
gh api repos/pypa/twine --jq '.license.spdx_id'        # Apache-2.0
gh api repos/pypa/build --jq '.license.spdx_id'        # MIT
```

For the OS-level tools (bash, git, curl, Python), the licences are well-known and widely documented; they have not changed in decades.

## Edge cases worth noting

A few situations where someone might ask "but what about…":

1. **`license` field in `pyproject.toml`.** Currently `license = {text = "MIT"}`. When the licence flips, this field needs updating to `license = "Apache-2.0"` (the modern PEP 639 SPDX form) so the metadata published to PyPI matches reality. Pure paperwork, same release.
2. **Bash scripts shipped inside the wheel.** `resolve_python.sh`, `entrypoint.sh`, and `seed_alcatraz.sh` are Alcatrazer-authored. They are *inputs to bash*, not derivative works of bash — the same way a Python script is not a derivative of CPython. Apache-2.0 covers them as Alcatrazer's own source.
3. **PSF-licensed Python.** The PSF licence is BSD-style and explicitly compatible with Apache-2.0 redistribution. Nothing about Alcatrazer's use of stdlib creates any constraint on Alcatrazer's own licence.
4. **Anthropic's proprietary Claude Code.** Installed at the user's request inside the user's container at the user's build time. Not redistributed by Alcatrazer. Anthropic's Terms of Service govern its use; Alcatrazer's licence governs Alcatrazer's use. The two are independent.
5. **Pre-licence-change contributions.** Any commit made before the Apache-2.0 switch was authored under the MIT grant. Because MIT is permissively compatible with Apache-2.0 redistribution, the project can re-licence going forward without invalidating prior contributions. Each pre-change commit remains under its original MIT grant in the git history; future releases ship under Apache-2.0.

## When this document needs updating

This analysis is accurate as of the date of the licence-change release. It should be re-examined if any of the following happen:

- **A runtime dependency is added.** Today `pyproject.toml` declares zero runtime dependencies. If that changes — for any reason — the new dependency's licence must be checked against Apache-2.0 compatibility.
- **The Dockerfile-generation logic adds a new bundled tool.** If a future version of `docker_prison.py` starts COPY-ing a binary into the image (rather than apt-installing it or curl-pipe-sh-installing it), that binary's redistribution licence becomes relevant.
- **Vendored code arrives.** If we ever copy third-party source files into `src/alcatrazer/`, the vendored code's licence must be added to a `NOTICE` file and reviewed for Apache-2.0 compatibility.
- **The release process bundles additional artifacts.** Today the wheel contains only Alcatrazer-authored content. If a future release includes a binary blob, a model file, a precompiled extension, etc., the analysis above must be updated.

If you encounter any of these situations and this document hasn't been updated, that is a documentation bug. Please open an issue.

## Further reading

- [`licence_change_reasoning.md`](./licence_change_reasoning.md) — companion document explaining the MIT → Apache-2.0 change in plain English with concrete scenarios.
- [Apache License 2.0 — full text](https://www.apache.org/licenses/LICENSE-2.0)
- [Apache Software Foundation FAQ on Apache-2.0](https://www.apache.org/foundation/license-faq.html)
- [GNU — Various Licenses and Comments on Them](https://www.gnu.org/licenses/license-list.en.html) — the canonical compatibility matrix
- [Python Software Foundation License](https://docs.python.org/3/license.html)
- [SPDX Licence List](https://spdx.org/licenses/) — SPDX identifiers for every licence mentioned above

## Questions or concerns

Open a GitHub issue. Compatibility analysis is checkable; if something here is unclear or wrong, fixing it is in everyone's interest.