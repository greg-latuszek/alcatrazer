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
> anchor relies on.

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
