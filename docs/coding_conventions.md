# Alcatrazer — Coding Conventions

Implementation-level best practices for code inside `src/alcatrazer/**`.
These are **how to write the code**, not **what to build** (design
decisions live in [`design_principles.md`](design_principles.md)) and
not **how the system is structured** (architecture lives in
[`architecture.md`](architecture.md)).

---

## Regex parsing must be preceded by an input-example comment

Any regex pattern that parses, matches, or substitutes structured byte
or text streams must be preceded by a multi-line comment showing a
representative example of the input it operates on. The comment is
load-bearing: without it, the pattern's correctness — especially its
boundary handling — cannot be reviewed.

### Why

Regexes are easy to write, hard to verify. A pattern that **looks**
correct can over-match on surprising inputs. The example comment forces
the author to articulate the input shape they're targeting, and lets a
reviewer (or future maintainer) confirm the boundaries by comparing the
example against adjacent (non-matching) shapes.

This rule was discovered while reviewing Phase 2 Step 2.2's
`rewrite_from_header`: the original pattern `^From: .+$` (MULTILINE)
over-matched on any commit-message body line starting with `From: `,
corrupting bodies that quote emails or contain example data. The
example comment plus a tighter anchor on the mbox separator fixes the
bug **and** makes the pattern's intent visible to future readers.

### How to apply

Before each regex pattern, comment:

1. **The input shape** — 1–3 lines of representative example data.
2. **The boundary** — what distinguishes a match from a near-miss.
3. **The anchor rationale** — why this anchor (or set of anchors)
   suffices to exclude the near-misses.

### Example template

```python
# Input (one mbox message from `git format-patch --stdout` — see
# docs/git_patch_example.log for a real captured sample):
#   From <40hex commit-sha> Mon Sep 17 00:00:00 2001
#                           ^^^^^^^^^^^^^^^^^^^^^^^^^
#                           git mbox-format SENTINEL DATE — emitted
#                           verbatim by `git format-patch` for every
#                           patch, regardless of the commit's real
#                           date. Stable in git source for 20+ years.
#                           The real commit date lives in the `Date:`
#                           header below.
#   From: <author name> <<author email>>          <- TARGET
#   Date: <real commit date — RFC 2822>
#   Subject: [PATCH] <subject line>
#
#   <commit body, possibly containing lines like "From: x@y" that
#    must NOT match — that's why we anchor on the mbox separator
#    above, not just on the `From: ` literal>
#   ---
#   <diff content>
#
# Anchor: the `From: ` line we want is the one IMMEDIATELY after the
# `From <40hex> Mon Sep 17 00:00:00 2001` separator. Capture the
# separator, replace only the line that follows it.
pattern = re.compile(
    rb"^(From [0-9a-f]{40} Mon Sep 17 00:00:00 2001\n)From: [^\n]*",
    re.MULTILINE,
)
```

> **Why this works in practice** — see `docs/git_patch_example.log`
> for a 3-patch sample. All three patches show *different* real dates
> in the `Date:` headers but the *identical* `Mon Sep 17 00:00:00 2001`
> in the `From <sha>` separator. That's the git protocol invariant the
> anchor relies on — git's own man page describes it as a *"fixed"*
> datestamp used as a marker so tools like `file(1)` can recognize
> the byte stream as git-format-patch output. The constancy is a
> documented contract, not an accident of stability. See the
> DESCRIPTION section of
> [git-format-patch(1)](https://git-scm.com/docs/git-format-patch).

### Scope

This rule binds **the runtime trust surface** (`src/alcatrazer/**`,
including tests). Dev tooling regex (in `_bmad/`, `.github/workflows/`)
and toolbox content (`.claude/`) are out of scope — different review
audience, different code lifecycle.

**Exception** — simple sanitizers whose input shape is trivially
obvious from the surrounding context (e.g.,
`docker_prison._BASENAME_INVALID_CHARS = re.compile(r"[^a-z0-9.-]+")`
applied to a path basename) do not require this treatment. The bar is:
*can a maintainer infer the input shape from the immediate context
without the comment?* If yes, no comment needed. If no — write the
comment.

### Existing regex sites in the runtime trust surface

Retrofitted with input-example comments as part of the Phase 2 BLUE
refactor:

- `promote.rewrite_from_header` — mbox `From:` header rewrite
- `promote.rewrite_identity` — fast-export `author` / `committer` line
  rewrite
- `promote.rewrite_refs` — fast-export `commit` / `reset` ref rewrite

Future regex additions in `src/alcatrazer/**` must include the
input-example comment from the start.

---

## User-facing strings speak the user's language

Any string that reaches the user — CLI stdout/stderr, error messages,
log entries they tail (`.alcatrazer/promotion-daemon.log`), status
surfaces, help text — must use **git's vocabulary** (branch,
repository, working tree, conflict, commit, stash, merge) and
**general programming** concepts. It must **not** use Alcatrazer's
internal machinery vocabulary.

### Why

The user knows git. They don't know our `pinned_branch`,
`promote_once`, `mirror mode`, `alcatraz-tree mode`, or the
`outer`/`inner` direction names. Strings written in tool jargon
turn the log file from a diagnostic surface into a translation
exercise; the user stops reading.

### Forbidden tool-internal vocabulary in user-facing strings

| Forbidden | Why it's tool-internal | Use instead |
|---|---|---|
| `pin`, `pinned`, `pinned_branch` | a state.json field name | `branch '<name>'` |
| `promotion`, `promoted`, `promote_once` | an internal operation | `applying`, `applied` |
| `outer`, `outer repo` | direction of view from Alcatrazer | `your repository`, `your branch` |
| `inner`, `inner repo`, `inner_root` | direction of view from Alcatrazer | `the workspace`, `the agent` |
| `mirror mode`, `alcatraz-tree mode` | internal daemon modes | never surface — invisible to user |
| `last_promoted`, `last_promotion_time` | state.json field names | `last applied commit`, `last application time` |
| `state.json`, `marks file` | implementation details | omit; or `Alcatrazer's state file` if forced |
| `git am --abort`, `format-patch` | git internals we wrap | describe what HAPPENED ("the commit could not be applied") |

### Preferred (allowed)

- `branch '<name>'` — naming the actual branch (concrete > abstract)
- `working tree`, `conflict`, `commit` (verb + noun), `stash`, `merge`
- `repository`, `your branch`
- `agent` — proxy for the AI process inside the workspace
- `workspace` — Alcatrazer-native but maps naturally; OK to surface

### Anti-pattern vs. correct

```python
# Bad — tool jargon, abstract status code
log.info("Held: outer state not aligned with pin (status=%s)", pin_status.value)

# Good — git vocabulary, names actual branches, gives the user
# something they can act on
current = snapshot.current_branch(str(target)) or "<unknown>"
log.info(
    "Held: your repository is on branch %r but Alcatrazer was "
    "started on %r. Switch back to %r to resume.",
    current, pinned_branch, pinned_branch,
)
```

### How to apply

Before committing any user-facing string:

1. Read it aloud as if you were the user (a developer who knows git
   but not Alcatrazer internals).
2. Highlight every word that's tool-machinery vocabulary.
3. Replace with git / general vocabulary, or with the concrete value
   (the actual branch name, count, file path).
4. Where possible, end with an actionable next step ("Switch back to
   '<branch>' to resume.") instead of a bare state ("Held.").

### Allowed to improve over the feature doc

If the design spec uses jargon (and several do — e.g.
`change_promotion_machinery.md` L344-354's `outer`, `alcatraz started
from`), the **implementation should still apply this rule**. Update
the spec to match the better wording in the same commit. Specs are
starting points, not ceilings — clarity wins when they conflict.

### Out of scope (this rule does NOT apply to)

- Python identifiers (variable / function / type / module names)
  — `PinStatus`, `_run_cycle_mirror`, `pinned_branch` as a state.json
  key are fine in code.
- Code comments and docstrings (developer-facing).
- Test names and assertions (developer-facing).
- Feature docs in `docs/features/` (design-level — though clarity is
  still preferred where it doesn't cost ambiguity).

---

## Run `mise format` at the end of every coding phase

A "coding phase" is any [RED] / [GREEN] / [BLUE] cycle (or a
self-contained chunk of edits ready to commit). Before you commit
the phase's work, run:

```
mise format
```

This runs `ruff format src` followed by `ruff check --fix src`
(scoped to `src/`, which holds both the package and its tests at
`src/alcatrazer/tests/` — the tests ship with the wheel as
`alcatrazer test` validation for end users). The task must exit
green: `All checks passed!`.

### Why this rule exists

Per-file `ruff format <one-file.py>` runs done in the middle of an
edit catch the diff you just wrote, but they miss cross-file lint
debts that accumulate over a phase (unused imports left after a
refactor, `SIM102` nested-`if` regressions in an adjacent module,
`F841` unused locals left from a deleted code path). One
project-scoped `mise format` at the end catches them as a batch,
keeps `main` permanently lintable, and matches what CI enforces —
so you find the failure now, not after pushing.

### When `mise format` reports new fixable issues

If `ruff check --fix src` auto-fixed something, treat the fix as
**part of the phase's commit** when it's small + obviously
mechanical (whitespace, import sort, formatter quirks). When the
fix is a meaningful refactor (collapsing a nested `if`, removing
an unused variable that was visibly there for a reason), split it
into its own `[BLUE] format only refactoring` commit so the
behavior-bearing GREEN/RED commit stays scoped to its test.

### Scope (what `mise format` looks at)

`src/` only. Anything outside `src/` is either generated build
output, AI-tool scaffolding installed in the working tree, or
docs — none of which are ours to lint. If you find ruff
complaining about a path under `src/`, fix the code; if it's
complaining about a path outside `src/`, the task's scope is
wrong, not the code.

---

## Imports live at the top of the file

All imports — stdlib, third-party, and intra-package — go in a
single block at the top of the module, in the standard isort
order (stdlib → third-party → first-party `alcatrazer`). No
function-scoped imports, no method-scoped imports, no
`if __name__ == "__main__":`-scoped imports.

```python
# Good
import textwrap
from alcatrazer import promote
from alcatrazer.docker_prison import DockerPrison

def cmd_clear(project_dir, prison=None):
    if prison is None:
        prison = DockerPrison(project_dir)
    ...

# Bad — local imports clutter the function body, hide module
# dependencies from anyone scanning the top of the file, and
# break the "every dependency this file uses is visible
# immediately" contract.
def cmd_clear(project_dir, prison=None):
    if prison is None:
        from alcatrazer.docker_prison import DockerPrison
        prison = DockerPrison(project_dir)
    ...
```

### Why this rule exists

A file's import block is its dependency declaration — what
external code does this module reach into? When some of that
declaration is hidden inside function bodies, a reader
auditing the module (for circular dependencies, for the
trust surface, for what would have to change if a dependency
moves) has to grep the whole file instead of reading the top
fifteen lines. The hidden cost compounds in code review,
refactoring, and onboarding.

Lazy imports were sometimes argued as performance optimization
(don't pay the import cost until the function runs) — but at
this project's scale that's measuring noise, and the
readability cost is real.

### Narrow, intentional exceptions

These are the *only* cases where a non-top-level import is
acceptable, and each must carry a one-line comment naming
which exception applies:

1. **Breaking a real import cycle.** If `from alcatrazer.X
   import Y` at the top of `alcatrazer.A` actually fails with
   `ImportError`, a local import inside the function that
   needs `Y` is the standard workaround. Prefer redesigning
   to avoid the cycle.
2. **Optional dependency probing.** Importing an optional
   third-party package gated behind a `try: import …
   except ImportError:` pattern, where the calling code
   degrades gracefully if the package is absent. (Currently
   none in Alcatrazer — we're stdlib-only inside `src/`.)
3. **Avoiding a heavy side-effect at import time.** A module
   whose top-level import triggers a network call, a
   subprocess, or hundreds of MB of memory allocation. Rare
   and worth a comment explaining the specific cost.

`if __name__ == "__main__":` blocks are NOT an exception —
move their `import sys` etc. to the file's import block.

---

## Schema changes must land in `schemas.json` + CHANGELOG before release

Alcatrazer declares two versioned schemas:

- **`.alcatrazer/state.json`** — internal cooperation file, written by
  the daemon and the CLI.
- **`coding-environment.toml`** — user-facing build/runtime description,
  written by the wizard or hand-edited.

The history of both schemas lives at `src/alcatrazer/schemas.json` —
the single source of truth, language-neutral, machine-diffable. Any
code change that adds, removes, or renames a field in either schema
must ship as **two coordinated edits in the same PR**:

1. A new revision entry appended to the right array in
   `src/alcatrazer/schemas.json` (`state_schema` or `coding_env_schema`),
   with `version`, `release`, `summary`, and the field-level
   `fields_added` / `fields_removed` / `fields_changed` lists.
2. A `### Changed` / `### Added` / `### Removed` line in `CHANGELOG.md`
   under the upcoming release, naming the bumped schema explicitly so a
   reader searching CHANGELOG for "schema" finds it.

### Why

The two artifacts serve different audiences but must stay in lockstep:

- `schemas.json` is the **runtime** source. The upgrade-refusal helper
  (`state.validate_schema_version`) reads it to render the message a
  user sees when their `.alcatrazer/` was written by an older version
  — release name and summary both come from `schemas.json`.
- `CHANGELOG.md` is the **release-notes** source — what a user reads
  before upgrading. A schema bump without a CHANGELOG mention surfaces
  to the user as a refusal-at-first-run with no preparation.

JSON over Python for the source-of-truth: schema history is data, not
code, and is consumed by tools that should not parse Python AST —
pre-release CI checks that diff `schemas.json` between the previous
tag and `HEAD`, future non-Python clients (a rewrite to another
language, an Alcatrazer HTTP/MCP API). The Python loader at
`src/alcatrazer/schema.py` is one such consumer; it is *not* the
source.

### How to apply

When a PR adds, removes, or renames fields in either schema:

1. **Append an entry** to the appropriate array in
   `src/alcatrazer/schemas.json`:
   - `version` — previous entry's version + 1.
   - `release` — the upcoming release tag (e.g. `"v0.1.2"`); if the
     release number isn't decided yet, use the working name and adjust
     at release time.
   - `summary` — one sentence in prose, suitable for the upgrade-refusal
     message (the user reads this verbatim).
   - `fields_added` / `fields_removed` / `fields_changed` — exact field
     names. TOML keys use dotted form, e.g.
     `"[promotion-daemon].mode"`.
2. **Add a CHANGELOG entry** under the matching release section.
   Reference the schema name explicitly.

A cross-check test in `src/alcatrazer/tests/test_schema.py` catches a
forgotten `schemas.json` entry: `state.SCHEMA_VERSION` and
`start.CODING_ENV_SCHEMA_VERSION` are derived from `schemas.json`, so
any code that introduces a schema-version bump without a matching JSON
entry fails to import. The CHANGELOG side is human-checked at PR
review until automated CI enforcement ships.

### Pre-release checklist

Before tagging a release, run:

```bash
git diff <previous-tag> HEAD -- src/alcatrazer/schemas.json
```

If the diff is non-empty, confirm `CHANGELOG.md` carries a
matching `### Changed` / `### Added` / `### Removed` entry for each
affected schema under the upcoming release section.

### Scope

Applies to the two declared schemas: `.alcatrazer/state.json` and
`coding-environment.toml`. Other internal data structures (the
daemon's PID file shape, marks files when they existed, log line
formats) are not versioned in `schemas.json` and don't participate
in this rule.
