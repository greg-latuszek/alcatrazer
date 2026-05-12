#!/usr/bin/env python3
"""
Promote commits from a source (alcatraz workspace) git repo to a target
(outer) git repo, rewriting author/committer identity to the outer user.

Pipeline (per change_promotion_machinery.md):

    git -C source format-patch --stdout --binary --keep-subject \\
        <since>..refs/heads/main
      | rewrite_from_header(name, email)            # author rewrite
      | git -C target am --committer-date-is-author-date \\
            --keep-non-patch --whitespace=nowarn --empty=drop
                                                    # committer rewrite via env

`git am` appends patches to outer's current branch and updates the
working tree atomically. The pin check (`check_pin`) gates the cycle
on outer being on the workspace's start branch; non-OK pins result in
HELD (no apply, no state change) until the user restores the branch.

Author identity priority (lowest to highest):
  1. git config (local first, then global — same as git does)
  2. .alcatrazer/config.toml [promotion] section
  3. CLI flags (still accepted by `resolve_identity` callers)

Requires Python 3.11+ (for tomllib).
"""

import sys

if sys.version_info < (3, 11):
    print(
        f"ERROR: Python 3.11+ required, got {sys.version}\n"
        "Run `alcatrazer start` to set up the correct Python.",
        file=sys.stderr,
    )
    sys.exit(1)

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from alcatrazer import snapshot, state


def git(repo: Path, *args: str) -> str:
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def resolve_identity(
    target_repo: Path, toml_file: Path, cli_name: str, cli_email: str
) -> tuple[str, str]:
    """Resolve author identity via the three-layer priority chain."""
    # Layer 1: git config (local > global, same as git does)
    name = git(target_repo, "config", "user.name")
    email = git(target_repo, "config", "user.email")

    # Layer 2: .alcatrazer/config.toml [promotion] section
    if toml_file.exists():
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)
        promo = data.get("promotion", {})
        if "name" in promo:
            name = promo["name"]
        if "email" in promo:
            email = promo["email"]

    # Layer 3: CLI flags (highest priority)
    if cli_name:
        name = cli_name
    if cli_email:
        email = cli_email

    if not name or not email:
        print(
            "ERROR: Could not determine promotion identity.\n"
            "Set it in .alcatrazer/config.toml [promotion], git config, "
            "or --author-name/--author-email flags.",
            file=sys.stderr,
        )
        sys.exit(1)

    return name, email


def rewrite_from_header(stream: bytes, name: str, email: str) -> bytes:
    """Substitute the author `From: ` header line in an mbox-format
    patch stream with `From: <name> <<email>>`.

    Operates on raw bytes — `git format-patch --binary` emits binary
    file diffs that must pass through untouched.

    Per change_promotion_machinery.md Phase 2 (Step 2.2). Used by
    `apply_patch_stream` to rewrite each patch's author before piping
    into `git am`; committer is rewritten separately via env-vars
    because `format-patch` carries no committer field.
    """
    # Input (one mbox message from `git format-patch --stdout` —
    # docs/git_patch_example.log has 3 real captured samples):
    #   From <40hex commit-sha> Mon Sep 17 00:00:00 2001
    #                           ^^^^^^^^^^^^^^^^^^^^^^^^^
    #                           git mbox-format SENTINEL — emitted
    #                           verbatim for every patch regardless
    #                           of the commit's real date.
    #                           Documented as a "fixed" datestamp in
    #                           git-format-patch(1)'s DESCRIPTION
    #                           section, used as a marker so file(1)
    #                           and similar tools can recognize a
    #                           git-format-patch byte stream. See
    #                           https://git-scm.com/docs/git-format-patch.
    #                           The real commit date lives in the
    #                           `Date:` header below.
    #   From: <author name> <<author email>>            <- TARGET
    #   Date: <real commit date — RFC 2822 format>
    #   Subject: [PATCH] <subject>
    #
    #   <commit body, free-form text — may legitimately contain lines
    #    starting with "From: ", e.g. an email quoted in the body or
    #    a config-file example. Those MUST NOT match this regex.>
    #   ---
    #   <diff/patch content, possibly binary>
    #
    # Boundary: the legitimate target is the `From: ` line that
    # appears IMMEDIATELY AFTER the `From <40hex> Mon Sep 17 ...`
    # mbox separator line. A naive `^From: ` anchor (with colon) is
    # not strict enough — it matches body lines and any other
    # `From: ` text. So we capture the separator in group 1 and
    # replace only the line that directly follows it, preserving the
    # separator unchanged via the \1 backref.
    #
    # The 40-hex + literal `Mon Sep 17 00:00:00 2001` anchor makes
    # this resilient: the only realistic way to mis-match would be
    # if a commit body contained a forged line of that exact shape
    # AND the *next* line started with `From: ` — a degree of
    # attacker control outside our threat model.
    pattern = re.compile(
        rb"^(From [0-9a-f]{40} Mon Sep 17 00:00:00 2001\n)From: [^\n]*",
        re.MULTILINE,
    )
    replacement = b"\\1From: " + name.encode("utf-8") + b" <" + email.encode("utf-8") + b">"
    return pattern.sub(replacement, stream)


def format_patch_stream(source: Path, since_sha: str) -> bytes:
    """Return an mbox-format patch stream for the non-merge commits
    in `<since_sha>..refs/heads/main` of the `source` workspace.

    Wraps `git format-patch --stdout --binary --keep-subject
    <since>..refs/heads/main`:

    - `--stdout` — emit a single mbox stream
    - `--binary` — include GIT binary patch sections for binary files
      (otherwise they're silently skipped)
    - `--keep-subject` — don't prepend "[PATCH]" to Subject

    The `since_sha` boundary is exclusive — when the caller passes
    the workspace's `inner_root` (the Initial commit, recorded by
    Phase 1 in state.json), `inner_root` itself does not appear in
    the stream; only its descendants do.

    **Merge handling:** `git format-patch` is fundamentally designed
    for non-merge commits — under any flag combination tested
    (`--first-parent`, `-m`, `--merges`, `--diff-merges=first-parent`,
    `--cc`, `-c`), it will NOT emit a patch for a merge commit. So
    when inner has merges (e.g. parallel-agent workflows where each
    agent commits on a side branch then merges to main), the merge
    commit itself isn't represented in the outer; its constituent
    side-branch commits ARE, as individual patches. This is the
    revised behavior per change_promotion_machinery.md Phase 3
    Step 3.6 (originally written with an `--first-parent` flag that
    didn't do what the spec assumed; revised to use default
    walking). The design is also product-better: individual atomic
    commits are reviewable by the developer before push (the core
    "review before push" promise of Alcatrazer); a single giant
    squash-per-merge would defeat it.

    Per change_promotion_machinery.md Phase 2 Step 2.4 (revised in
    Phase 3 Step 3.6 — dropped `--first-parent`).
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "format-patch",
            "--stdout",
            "--binary",
            "--keep-subject",
            f"{since_sha}..refs/heads/main",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


class PinStatus(Enum):
    """Classification of outer's current HEAD against the workspace's
    recorded `pinned_branch`. Drives promote_once's decision to apply
    patches (OK) or hold (OFF_PIN / DETACHED / PIN_DELETED).
    """

    OK = "ok"
    OFF_PIN = "off_pin"
    DETACHED = "detached"
    PIN_DELETED = "pin_deleted"


def check_pin(target: Path, pinned_branch: str) -> PinStatus:
    """Classify outer's HEAD against the recorded pin.

    Precedence (most-specific first):
    1. `pinned_branch` no longer exists in target → `PIN_DELETED`
    2. HEAD is detached (independent of whether `pinned_branch`
       exists) → `DETACHED`
    3. Current branch equals `pinned_branch` → `OK`
    4. Current branch is something else → `OFF_PIN`

    Step (1) wins over (2) when both apply because PIN_DELETED is
    the more actionable diagnosis: the user can recover from
    detached HEAD by checking out the pin; if the pin itself is
    gone, there's nothing to check out and the workspace needs
    different remediation.

    Per change_promotion_machinery.md Phase 3 Step 3.2 (L854-855).
    """
    # 1. Does the pinned branch still exist?
    pin_exists = (
        subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "rev-parse",
                "--verify",
                f"refs/heads/{pinned_branch}",
            ],
            capture_output=True,
        ).returncode
        == 0
    )
    if not pin_exists:
        return PinStatus.PIN_DELETED

    # 2. Detached HEAD? (reuses snapshot's narrow detector)
    if snapshot.is_detached_head(str(target)):
        return PinStatus.DETACHED

    # 3 / 4. On a branch — which one?
    current = snapshot.current_branch(str(target))
    if current == pinned_branch:
        return PinStatus.OK
    return PinStatus.OFF_PIN


class PromotionConflictError(Exception):
    """Raised by apply_patch_stream when `git am` fails to apply the
    stream (typically a working-tree / history divergence between
    outer and the patch's expected base). The aborted patch series
    is rolled back via `git am --abort` before this is raised, so
    outer's HEAD and working tree are byte-identical to the pre-call
    state.
    """


def apply_patch_stream(target: Path, stream: bytes, name: str, email: str) -> None:
    """Apply an mbox-format patch stream to `target` via `git am`,
    rewriting both author and committer identity to (name, email).

    - Author is rewritten via `rewrite_from_header` on the input stream
      (substitutes the `From: ` line).
    - Committer is rewritten via `GIT_COMMITTER_NAME` /
      `GIT_COMMITTER_EMAIL` env vars passed to `git am`.

    The asymmetric channels reflect git's design: `git format-patch`
    carries author in the `From:` header but emits NO committer
    info — committer is dropped at the patch boundary — and
    `git am` has no flag to override the patch's author. So author
    rewrite must happen on the stream, and committer rewrite at
    apply-time via env. Even when the agent commits with SPLIT
    author/committer inside the workspace (via GIT_AUTHOR_*/
    GIT_COMMITTER_* env vars), only the author survives
    format-patch, and our env override sets committer correctly.
    See `test_inner_split_author_committer_collapsed_to_outer_identity`.

    `git am` flags:
    - `--committer-date-is-author-date` — committer timestamp equals
      author timestamp (no time drift across promotion)
    - `--keep-non-patch` — keep Subject content even if not patch-shaped
    - `--whitespace=nowarn` — don't reject patches with whitespace
      issues; we control both sides of the pipeline
    - `--empty=drop` — silently skip e-mails with no diff (empty
      commits) rather than failing or pausing

    On non-zero exit, runs `git am --abort` to clean up the partial
    apply, then raises PromotionConflictError with captured stderr.

    Per change_promotion_machinery.md Phase 2 Step 2.10.
    """
    rewritten = rewrite_from_header(stream, name, email)

    env = os.environ.copy()
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email

    result = subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "am",
            "--committer-date-is-author-date",
            "--keep-non-patch",
            "--whitespace=nowarn",
            "--empty=drop",
        ],
        input=rewritten,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        # Roll back the partial `am` so outer's HEAD + tree return to
        # the pre-call state. `--abort` is itself best-effort: failure
        # to abort is rare but if it happens we still raise the
        # original conflict so the caller knows the operation failed.
        subprocess.run(
            ["git", "-C", str(target), "am", "--abort"],
            capture_output=True,
        )
        raise PromotionConflictError(
            f"git am failed (exit {result.returncode}): "
            + result.stderr.decode("utf-8", errors="replace")
        )


class PromotionOutcome(Enum):
    """Outcome of a single promote_once cycle. Drives daemon logging
    and `alcatrazer status` rendering (Phase 5)."""

    PROMOTED = "promoted"  # patches applied (commit_count may be 0 = no-op)
    HELD = "held"  # pin check failed; no apply attempted, no state change
    PAUSED = "paused"  # apply raised PromotionConflictError; paused state recorded


@dataclass(frozen=True)
class PromotionResult:
    """Structured result of a promote_once cycle. Fields are
    populated based on the outcome:

    - outcome=PROMOTED: commit_count = N patches applied (0 = no-op)
    - outcome=HELD:     pin_status = why (OFF_PIN / DETACHED / PIN_DELETED)
    - outcome=PAUSED:   conflict_message = stderr from `git am`
    """

    outcome: PromotionOutcome
    commit_count: int = 0
    pin_status: PinStatus | None = None
    conflict_message: str = ""


# Re-used in promote_once for counting patches in a format-patch stream.
# Input shape is the same as rewrite_from_header's — see that function's
# input-example comment. Anchored on the mbox-format constant separator.
_MBOX_SEPARATOR_PATTERN = re.compile(
    rb"^From [0-9a-f]{40} Mon Sep 17 00:00:00 2001",
    re.MULTILINE,
)


def promote_once(
    source: Path,
    target: Path,
    alcatraz_dir: Path,
    name: str,
    email: str,
) -> PromotionResult:
    """Run one promotion cycle against an Alcatrazer workspace.

    Reads state (`pinned_branch`, `inner_root`, `last_promoted`) from
    `alcatraz_dir/state.json`, checks the outer's pin, and either:

    - **OK pin** — format-patches the range `<since>..refs/heads/main`
      (where `since = last_promoted or inner_root`), applies via
      apply_patch_stream, advances state (`last_promoted`,
      `last_promotion_time`, `paused=None`), returns PROMOTED.
    - **Non-OK pin** — returns HELD with the specific PinStatus.
      No `git am`, no state mutation. The agent keeps committing
      inside while we wait for the user to fix the outer state.
    - **Apply conflict** — catches PromotionConflictError, writes
      `paused={"reason": ...}`, leaves `last_promoted` /
      `last_promotion_time` untouched (they only reflect SUCCESSFUL
      promotions), returns PAUSED.

    No-op steady state (no new agent commits since last_promoted) is
    PROMOTED with commit_count=0. State is not advanced because
    `last_promoted` is already at inner's tip.

    Per change_promotion_machinery.md Phase 3 Step 3.8 (L875-876).
    """
    state_data = state.load_state(alcatraz_dir)
    pinned_branch = state_data.get("pinned_branch")
    inner_root = state_data.get("inner_root")
    last_promoted = state_data.get("last_promoted") or inner_root

    # 1. Pin check — anything but OK puts us on hold (no work, no state change).
    pin = check_pin(target, pinned_branch)
    if pin is not PinStatus.OK:
        return PromotionResult(outcome=PromotionOutcome.HELD, pin_status=pin)

    # 2. Format patches for inner's main since last_promoted (or inner_root).
    stream = format_patch_stream(source, last_promoted)
    commit_count = len(_MBOX_SEPARATOR_PATTERN.findall(stream))

    # 3. Steady state — nothing new to promote.
    if commit_count == 0:
        return PromotionResult(outcome=PromotionOutcome.PROMOTED, commit_count=0)

    # 4. Apply. On conflict, record paused state and return early.
    try:
        apply_patch_stream(target, stream, name, email)
    except PromotionConflictError as exc:
        state.update_state(alcatraz_dir, paused={"reason": str(exc)})
        return PromotionResult(
            outcome=PromotionOutcome.PAUSED,
            conflict_message=str(exc),
        )

    # 5. Success — advance state. last_promoted moves to inner's tip;
    # last_promotion_time stamped UTC ISO 8601; paused cleared.
    inner_tip = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "refs/heads/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    state.update_state(
        alcatraz_dir,
        last_promoted=inner_tip,
        last_promotion_time=datetime.now(UTC).isoformat(),
        paused=None,
    )

    return PromotionResult(
        outcome=PromotionOutcome.PROMOTED,
        commit_count=commit_count,
    )
