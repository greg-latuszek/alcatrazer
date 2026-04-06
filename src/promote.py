#!/usr/bin/env python3
"""
Promote commits from a source (alcatraz) git repo to a target (outer) git repo.
Rewrites author/committer identity while preserving full branch and merge topology.

Uses git fast-export / fast-import with incremental mark files so only new
commits are transferred on subsequent runs.

Author identity priority (lowest to highest):
  1. git config (local first, then global — same as git does)
  2. alcatrazer.toml [promotion] section
  3. --author-name / --author-email CLI flags

Usage:
    .alcatraz/python src/promote.py \\
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
        "Run ./src/initialize_alcatraz.sh to set up the correct Python.",
        file=sys.stderr,
    )
    sys.exit(1)

import argparse
import fnmatch
import re
import subprocess
import tomllib
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
        capture_output=True, text=True,
    )
    all_branches = result.stdout.strip().splitlines() if result.stdout.strip() else []

    # Normalize to list of patterns
    if isinstance(branches_config, str):
        patterns = [branches_config]
    else:
        patterns = list(branches_config)

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
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def resolve_identity(target_repo: Path, toml_file: Path,
                     cli_name: str, cli_email: str) -> tuple[str, str]:
    """Resolve author identity via the three-layer priority chain."""
    # Layer 1: git config (local > global, same as git does)
    name = git(target_repo, "config", "user.name")
    email = git(target_repo, "config", "user.email")

    # Layer 2: alcatrazer.toml [promotion] section
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
            "Set it in alcatrazer.toml [promotion], git config, "
            "or --author-name/--author-email flags.",
            file=sys.stderr,
        )
        sys.exit(1)

    return name, email


def rewrite_identity(stream: str, name: str, email: str) -> str:
    """Rewrite author/committer lines in a fast-export stream."""
    stream = re.sub(
        r"^(author) .+ <.+> (.+)$",
        rf"\1 {name} <{email}> \2",
        stream, flags=re.MULTILINE,
    )
    stream = re.sub(
        r"^(committer) .+ <.+> (.+)$",
        rf"\1 {name} <{email}> \2",
        stream, flags=re.MULTILINE,
    )
    return stream


def dry_run(source: Path, marks_dir: Path, name: str, email: str,
            branches: str | list = "all") -> None:
    """Show what would be promoted without modifying anything."""
    export_marks = marks_dir / "promote-export-marks"
    refs = resolve_branches(source, branches)

    cmd = ["git", "-C", str(source), "fast-export"] + refs
    if export_marks.exists():
        cmd.append(f"--import-marks={export_marks}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    stream = result.stdout

    commits = re.findall(r"^commit (.+)$", stream, re.MULTILINE)
    commit_count = len(commits)

    if commit_count == 0:
        print("Nothing to promote — target is up to date.")
        return
    branches = sorted(set(commits))

    print(f"Dry run: {commit_count} commit(s) would be promoted")
    print("Branches affected:")
    for branch in branches:
        print(f"  {branch}")
    print()
    print(f"Author/committer will be rewritten to: {name} <{email}>")


def promote(source: Path, target: Path, marks_dir: Path,
            name: str, email: str, branches: str | list = "all") -> None:
    """Run fast-export | rewrite identity | fast-import pipeline."""
    marks_dir.mkdir(parents=True, exist_ok=True)
    export_marks = marks_dir / "promote-export-marks"
    import_marks = marks_dir / "promote-import-marks"
    refs = resolve_branches(source, branches)

    # Build fast-export command
    export_cmd = ["git", "-C", str(source), "fast-export"] + refs
    if export_marks.exists():
        export_cmd.append(f"--import-marks={export_marks}")
    export_cmd.append(f"--export-marks={export_marks}")

    # Build fast-import command
    import_cmd = ["git", "-C", str(target), "fast-import", "--force", "--quiet"]
    if import_marks.exists():
        import_cmd.append(f"--import-marks={import_marks}")
    import_cmd.append(f"--export-marks={import_marks}")

    # Run pipeline: fast-export | rewrite | fast-import
    export_proc = subprocess.run(export_cmd, capture_output=True, text=True, check=True)
    rewritten = rewrite_identity(export_proc.stdout, name, email)
    subprocess.run(import_cmd, input=rewritten, text=True, check=True)

    print(f"Promotion complete: {source} -> {target}")


def main():
    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parent
    toml_file = project_dir / "alcatrazer.toml"

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
    marks_dir = (args.marks_dir or project_dir / ".alcatraz").resolve()
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
