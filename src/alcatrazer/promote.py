#!/usr/bin/env python3
"""
Promote commits from a source (alcatraz) git repo to a target (outer) git repo.
Rewrites author/committer identity while preserving full branch and merge topology.

Uses git fast-export / fast-import with incremental mark files so only new
commits are transferred on subsequent runs.

Author identity priority (lowest to highest):
  1. git config (local first, then global — same as git does)
  2. .alcatrazer/config.toml [promotion] section
  3. --author-name / --author-email CLI flags

Usage:
    .alcatrazer/python -m alcatrazer.promote \\
        --source <path-to-source-repo> \\
        --target <path-to-target-repo> \\
        [--author-name "Your Name"] \\
        [--author-email "your@email.com"] \\
        [--marks-dir <dir>] \\
        [--dry-run]

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

import argparse
import fnmatch
import json
import os
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path


def resolve_branches(source: Path, branches_config) -> list[str]:
    """Resolve branches config to a list of git refs for fast-export.

    branches_config can be:
      - "all"         → returns ["--all"]
      - "main"        → returns ["refs/heads/main"]
      - ["main", "feature/*"] → glob-matched against actual branches
    """
    if branches_config == "all":
        return ["--all"]

    # Get all branch names from the source repo
    result = subprocess.run(
        ["git", "-C", str(source), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    all_branches = result.stdout.strip().splitlines() if result.stdout.strip() else []

    # Normalize to list of patterns
    patterns = [branches_config] if isinstance(branches_config, str) else list(branches_config)

    # Match patterns against actual branches
    matched = set()
    for pattern in patterns:
        for branch in all_branches:
            if fnmatch.fnmatch(branch, pattern):
                matched.add(branch)

    return [f"refs/heads/{b}" for b in sorted(matched)]


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


def rewrite_identity(stream: bytes, name: str, email: str) -> bytes:
    """Rewrite author/committer lines in a fast-export stream.

    Operates on raw bytes because fast-export embeds blob content
    inline and git histories can contain non-UTF-8 bytes (images,
    archives, etc.). Anchoring on the trailing `<timestamp> <tz>` shape
    keeps the regex from matching text that merely starts with
    "author " or "committer " inside a data section.
    """
    repl = b"\\1 " + name.encode("utf-8") + b" <" + email.encode("utf-8") + b"> \\2"
    stream = re.sub(
        rb"^(author) .+ <.+> (.+)$",
        repl,
        stream,
        flags=re.MULTILINE,
    )
    stream = re.sub(
        rb"^(committer) .+ <.+> (.+)$",
        repl,
        stream,
        flags=re.MULTILINE,
    )
    return stream


def rewrite_from_header(stream: bytes, name: str, email: str) -> bytes:
    """Substitute the author `From: ` header line in an mbox-format
    patch stream with `From: <name> <<email>>`.

    Operates on raw bytes — `git format-patch --binary` emits binary
    file diffs that must pass through untouched.

    Per change_promotion_machinery.md Phase 2 (Step 2.2). Companion
    primitive to `rewrite_identity` which operates on fast-export
    streams; this one operates on `git format-patch` mbox streams used
    by the new patch-stream-based promotion pipeline.
    """
    # Input (one mbox message from `git format-patch --stdout`):
    #   From <40hex> Mon Sep 17 00:00:00 2001          <- mbox separator
    #   From: <author name> <<author email>>           <- TARGET
    #   Date: <RFC 2822 timestamp>
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
    # `From: ` in the patch content. So we capture the separator line
    # in group 1 and replace only the line that directly follows it,
    # preserving the separator unchanged.
    #
    # The 40-hex anchor + the literal `Mon Sep 17 00:00:00 2001`
    # (git's stable mbox "From " separator timestamp) makes this
    # match resilient: the only realistic way to mis-match would be
    # if a commit body contained a forged line with that exact shape
    # — and even then the *next* line would still need to start with
    # `From: ` for the pattern to fire.
    pattern = re.compile(
        rb"(?m)^(From [0-9a-f]{40} Mon Sep 17 00:00:00 2001\n)From: [^\n]*",
    )
    replacement = (
        b"\\1From: " + name.encode("utf-8") + b" <" + email.encode("utf-8") + b">"
    )
    return pattern.sub(replacement, stream)


def format_patch_stream(source: Path, since_sha: str) -> bytes:
    """Return an mbox-format patch stream for commits in
    `<since_sha>..refs/heads/main` of the `source` workspace.

    Wraps `git format-patch --stdout --binary --keep-subject
    --first-parent <since>..refs/heads/main`:

    - `--stdout` — emit a single mbox stream
    - `--binary` — include GIT binary patch sections for binary files
      (otherwise they're silently skipped)
    - `--keep-subject` — don't prepend "[PATCH]" to Subject
    - `--first-parent` — linearize merges; the outer repo receives
      agent work as a chain on its pinned branch, not as a merged
      topology

    The `since_sha` boundary is exclusive — when the caller passes the
    workspace's `inner_root` (the Initial commit, recorded by Phase 1
    in state.json), `inner_root` itself does not appear in the stream;
    only its descendants do.

    Per change_promotion_machinery.md Phase 2 Step 2.4.
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
            "--first-parent",
            f"{since_sha}..refs/heads/main",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


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


def dry_run(
    source: Path, marks_dir: Path, name: str, email: str, branches: str | list = "all"
) -> None:
    """Show what would be promoted without modifying anything."""
    export_marks = marks_dir / "promote-export-marks"
    refs = resolve_branches(source, branches)

    cmd = ["git", "-C", str(source), "fast-export", *refs]
    if export_marks.exists():
        cmd.append(f"--import-marks={export_marks}")

    result = subprocess.run(cmd, capture_output=True)
    stream = result.stdout

    commits = re.findall(rb"^commit (.+)$", stream, re.MULTILINE)
    commit_count = len(commits)

    if commit_count == 0:
        print("Nothing to promote — target is up to date.")
        return
    branches = sorted({c.decode("utf-8", errors="replace") for c in commits})

    print(f"Dry run: {commit_count} commit(s) would be promoted")
    print("Branches affected:")
    for branch in branches:
        print(f"  {branch}")
    print()
    print(f"Author/committer will be rewritten to: {name} <{email}>")


def rewrite_refs(stream: bytes, namespace: str) -> bytes:
    """Rewrite ref names in a fast-export stream to add a namespace prefix.

    refs/heads/main -> refs/heads/<namespace>/main
    """
    repl = b"\\1 refs/heads/" + namespace.encode("utf-8") + b"/\\2"
    return re.sub(
        rb"^(commit|reset) refs/heads/(.+)$",
        repl,
        stream,
        flags=re.MULTILINE,
    )


def promote(
    source: Path,
    target: Path,
    marks_dir: Path,
    name: str,
    email: str,
    branches: str | list = "all",
    namespace: str = "",
) -> None:
    """Run fast-export | rewrite identity | fast-import pipeline.

    If namespace is set, branch names are prefixed: main -> <namespace>/main.
    """
    marks_dir.mkdir(parents=True, exist_ok=True)
    export_marks = marks_dir / "promote-export-marks"
    import_marks = marks_dir / "promote-import-marks"
    refs = resolve_branches(source, branches)

    # Build fast-export command
    export_cmd = ["git", "-C", str(source), "fast-export", *refs]
    if export_marks.exists():
        export_cmd.append(f"--import-marks={export_marks}")
    export_cmd.append(f"--export-marks={export_marks}")

    # Build fast-import command
    import_cmd = ["git", "-C", str(target), "fast-import", "--force", "--quiet"]
    if import_marks.exists():
        import_cmd.append(f"--import-marks={import_marks}")
    import_cmd.append(f"--export-marks={import_marks}")

    # Run pipeline: fast-export | rewrite identity (+ namespace) | fast-import.
    # Must stay in bytes mode — fast-export embeds blob content inline and
    # git histories routinely contain non-UTF-8 bytes (images, archives).
    export_proc = subprocess.run(export_cmd, capture_output=True, check=True)
    stream = rewrite_identity(export_proc.stdout, name, email)
    if namespace:
        stream = rewrite_refs(stream, namespace)
    subprocess.run(import_cmd, input=stream, check=True)

    print(f"Promotion complete: {source} -> {target}")


# --- Conflict detection for mirror mode ---


def load_promoted_tips(marks_dir: Path) -> dict[str, str]:
    """Load last-promoted branch tips from JSON file."""
    tips_file = marks_dir / "promoted-tips.json"
    if tips_file.exists():
        return json.loads(tips_file.read_text())
    return {}


def save_promoted_tips(marks_dir: Path, tips: dict[str, str]) -> None:
    """Save branch tips after successful promotion."""
    tips_file = marks_dir / "promoted-tips.json"
    tips_file.write_text(json.dumps(tips, indent=2) + "\n")


def load_paused_branches(marks_dir: Path) -> set[str]:
    """Load paused branches from disk (persists across daemon restarts)."""
    paused_file = marks_dir / "paused-branches.json"
    if paused_file.exists():
        return set(json.loads(paused_file.read_text()))
    return set()


def save_paused_branches(marks_dir: Path, paused: set[str]) -> None:
    """Save paused branches to disk."""
    paused_file = marks_dir / "paused-branches.json"
    paused_file.write_text(json.dumps(sorted(paused), indent=2) + "\n")


def get_branch_tips(repo: Path, branches: list[str]) -> dict[str, str]:
    """Get current commit hashes for the given branches in a repo."""
    tips = {}
    for branch in branches:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            tips[branch] = result.stdout.strip()
    return tips


def find_conflict_branches(target: Path, branch: str) -> list[str]:
    """Find conflict/resolve-<branch>-* branches in the target repo."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "branch",
            "--format=%(refname:short)",
            "--list",
            f"conflict/resolve-{branch}-*",
        ],
        capture_output=True,
        text=True,
    )
    return [b.strip() for b in result.stdout.splitlines() if b.strip()]


def check_resolved_conflicts(target: Path, marks_dir: Path, paused_branches: set) -> set:
    """Check if any paused branch's conflict branch has been deleted/merged.

    Returns the set of branches that should be unpaused.
    """
    resolved = set()
    for branch in list(paused_branches):
        conflict_refs = find_conflict_branches(target, branch)
        if not conflict_refs:
            # Conflict branch is gone — user resolved it
            resolved.add(branch)
            # Update promoted-tips to current outer tip so we don't
            # re-detect divergence on the next cycle
            tips = load_promoted_tips(marks_dir)
            current = get_branch_tips(target, [branch])
            tips.update(current)
            save_promoted_tips(marks_dir, tips)
    return resolved


def detect_diverged_branches(target: Path, marks_dir: Path, branch_names: list[str]) -> set[str]:
    """Detect branches where the outer repo has diverged from last promotion.

    A branch has diverged if its current tip in the target repo differs from
    what we recorded after the last promotion.
    """
    promoted_tips = load_promoted_tips(marks_dir)
    current_tips = get_branch_tips(target, branch_names)
    diverged = set()
    for branch, current_tip in current_tips.items():
        last_tip = promoted_tips.get(branch)
        if last_tip is not None and current_tip != last_tip:
            diverged.add(branch)
    return diverged


def promote_with_conflict_handling(
    source: Path,
    target: Path,
    marks_dir: Path,
    name: str,
    email: str,
    branches: str | list = "all",
    paused_branches: set | None = None,
) -> dict[str, str]:
    """Promote branches, handling conflicts in mirror mode.

    Returns a dict of {branch: status} where status is:
      "promoted" — branch promoted successfully
      "conflict" — branch diverged, conflict branch created
      "paused"   — branch was already paused from a previous conflict
      "skipped"  — nothing new to promote on this branch

    Also updates promoted-tips.json for successfully promoted branches.
    """
    if paused_branches is None:
        paused_branches = set()

    marks_dir.mkdir(parents=True, exist_ok=True)

    # Resolve which branches to promote
    refs = resolve_branches(source, branches)
    if refs == ["--all"]:
        # Get actual branch names from source
        result = subprocess.run(
            ["git", "-C", str(source), "branch", "--format=%(refname:short)"],
            capture_output=True,
            text=True,
        )
        branch_names = result.stdout.strip().splitlines() if result.stdout.strip() else []
    else:
        branch_names = [r.removeprefix("refs/heads/") for r in refs]

    # Detect diverged branches
    diverged = detect_diverged_branches(target, marks_dir, branch_names)

    # Separate into promotable and conflicting
    to_promote = [b for b in branch_names if b not in diverged and b not in paused_branches]
    results = {}

    # Mark paused branches
    for b in branch_names:
        if b in paused_branches:
            results[b] = "paused"

    # Handle diverged branches — create conflict branches
    for b in diverged:
        if b in paused_branches:
            continue
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        conflict_ref = f"conflict/resolve-{b}-{timestamp}"
        try:
            _promote_single_branch(
                source, target, marks_dir, name, email, b, target_ref=conflict_ref
            )
            results[b] = "conflict"
            paused_branches.add(b)
        except Exception:
            results[b] = "conflict"
            paused_branches.add(b)

    # Promote non-conflicting branches
    if to_promote:
        promote(source, target, marks_dir, name, email, branches=to_promote)
        # Update tips for successfully promoted branches
        new_tips = get_branch_tips(target, to_promote)
        old_tips = load_promoted_tips(marks_dir)
        old_tips.update(new_tips)
        save_promoted_tips(marks_dir, old_tips)
        for b in to_promote:
            results[b] = "promoted"

    # Persist paused state across daemon restarts
    save_paused_branches(marks_dir, paused_branches)

    return results


def _promote_single_branch(
    source: Path,
    target: Path,
    marks_dir: Path,
    name: str,
    email: str,
    branch: str,
    target_ref: str | None = None,
) -> None:
    """Promote a single branch, optionally to a different ref name."""
    marks_dir.mkdir(parents=True, exist_ok=True)
    export_marks = marks_dir / "promote-export-marks"
    import_marks = marks_dir / "promote-import-marks"

    export_cmd = ["git", "-C", str(source), "fast-export", f"refs/heads/{branch}"]
    if export_marks.exists():
        export_cmd.append(f"--import-marks={export_marks}")
    export_cmd.append(f"--export-marks={export_marks}")

    import_cmd = ["git", "-C", str(target), "fast-import", "--force", "--quiet"]
    if import_marks.exists():
        import_cmd.append(f"--import-marks={import_marks}")
    import_cmd.append(f"--export-marks={import_marks}")

    export_proc = subprocess.run(export_cmd, capture_output=True, check=True)
    stream = rewrite_identity(export_proc.stdout, name, email)

    # Rewrite the ref name if promoting to a different target (e.g. conflict branch)
    if target_ref:
        stream = re.sub(
            rb"^commit refs/heads/" + re.escape(branch.encode("utf-8")) + rb"$",
            b"commit refs/heads/" + target_ref.encode("utf-8"),
            stream,
            flags=re.MULTILINE,
        )

    subprocess.run(import_cmd, input=stream, check=True)


def _default_project_dir() -> Path:
    """Same rationale as `daemon._default_project_dir` — detect whether
    we're running from the installed layout (under `.alcatrazer/src/`)
    or a dev checkout (under `src/`), and walk up accordingly.
    """
    script_dir = Path(__file__).resolve().parent
    parts = script_dir.parts
    if ".alcatrazer" in parts:
        idx = len(parts) - 1 - parts[::-1].index(".alcatrazer")
        return Path(*parts[:idx])
    return script_dir.parent.parent


def main():
    project_dir = _default_project_dir()
    # Per-developer config lives under .alcatrazer/ (install_method.md config
    # split). The public coding-environment.toml at the repo root has a
    # different schema.
    toml_file = project_dir / ".alcatrazer" / "config.toml"

    parser = argparse.ArgumentParser(description="Promote alcatraz commits to outer repo")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--author-name", default="")
    parser.add_argument("--author-email", default="")
    parser.add_argument("--marks-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    marks_dir = (args.marks_dir or project_dir / ".alcatrazer").resolve()
    marks_dir.mkdir(parents=True, exist_ok=True)

    # Validate repos
    if not (source / ".git").is_dir():
        print(f"ERROR: {source} is not a git repository", file=sys.stderr)
        sys.exit(1)
    if not (target / ".git").is_dir():
        print(f"ERROR: {target} is not a git repository", file=sys.stderr)
        sys.exit(1)

    name, email = resolve_identity(target, toml_file, args.author_name, args.author_email)

    if args.dry_run:
        dry_run(source, marks_dir, name, email)
    else:
        promote(source, target, marks_dir, name, email)


if __name__ == "__main__":
    main()
