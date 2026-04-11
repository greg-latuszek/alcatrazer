"""
Snapshot extraction from outer repo into workspace.

Copies the current state of the outer repo's main branch into
.alcatrazer/workspace/ as a flat snapshot (files only, no history).
"""

import re
import subprocess
from pathlib import Path


class NotAGitRepoError(Exception):
    """Raised when the target directory is not inside a git repository."""


class AmbiguousBranchError(Exception):
    """Raised when both main and master exist and origin/HEAD is not set."""


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the given repo."""
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
    )


def require_git_repo(path: str) -> Path:
    """Verify path is inside a git working tree. Return the repo root.

    Raises NotAGitRepoError if not inside a git repository.
    """
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Not a git repository: {path}\n"
            "Alcatrazer must be run from inside an existing git repository."
        )
    return Path(result.stdout.strip()).resolve()


def detect_default_branch(repo: str) -> str | None:
    """Detect the default branch of the outer repo.

    Three-tier priority:
    1. origin/HEAD (authoritative — set by GitHub/GitLab)
    2. Existence check: main, then master
    3. Raises AmbiguousBranchError if both exist without origin/HEAD

    Returns None if the repo has no commits (greenfield).
    """
    # Check if repo has any commits at all
    result = _git(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        return None

    # Tier 1: origin/HEAD
    result = _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]

    # Tier 2: existence check
    has_main = _git(repo, "rev-parse", "--verify", "refs/heads/main").returncode == 0
    has_master = _git(repo, "rev-parse", "--verify", "refs/heads/master").returncode == 0

    if has_main and has_master:
        raise AmbiguousBranchError(
            "Both 'main' and 'master' branches exist and origin/HEAD is not set.\n"
            "Cannot determine the default branch automatically."
        )

    if has_main:
        return "main"
    if has_master:
        return "master"

    # No main or master — shouldn't happen if repo has commits,
    # but handle gracefully
    return None


def extract_snapshot(repo: str, branch: str | None, workspace: str) -> None:
    """Extract files from repo's branch into workspace via git archive.

    If branch is None (greenfield/empty repo), this is a no-op.
    Excludes .alcatrazer/ and .env even if tracked.
    """
    if branch is None:
        return

    # git archive exports tracked files, piped to tar for extraction
    archive = subprocess.run(
        ["git", "-C", repo, "archive", branch],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["tar", "-xf", "-", "-C", workspace, "--exclude=.alcatrazer", "--exclude=.env"],
        input=archive.stdout,
        check=True,
    )


def filter_gitignore(workspace: str) -> None:
    """Remove .alcatrazer/ rule from .gitignore in workspace.

    Matches exactly '.alcatrazer/' or '.alcatrazer' (with optional trailing
    slash and whitespace). Does not match substrings like '.alcatrazer-tools/'.
    Removes the file entirely if empty after filtering.
    """
    gitignore = Path(workspace) / ".gitignore"
    if not gitignore.exists():
        return

    lines = gitignore.read_text().splitlines(keepends=True)
    filtered = [line for line in lines if not re.match(r"^\.alcatrazer/?\s*$", line)]

    if not filtered or all(line.strip() == "" for line in filtered):
        gitignore.unlink()
    else:
        gitignore.write_text("".join(filtered))


def create_initial_commit(workspace: str) -> None:
    """Stage all files and create the initial commit in workspace.

    Uses --allow-empty for greenfield repos (no files to commit).
    Commit identity comes from the workspace's git config.
    """
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "--allow-empty", "-m", "Initial commit")


def snapshot_workspace(outer_repo: str, workspace: str) -> None:
    """Full snapshot flow: validate repo, detect branch, extract, filter, commit.

    Called by initialize_alcatraz.sh after git init of the workspace.
    Expects the workspace to already have git init + identity configured.
    """
    require_git_repo(outer_repo)
    branch = detect_default_branch(outer_repo)
    extract_snapshot(outer_repo, branch, workspace)
    filter_gitignore(workspace)
    create_initial_commit(workspace)


def count_unpromoted_commits(workspace: str, marks_dir: str) -> int:
    """Count commits in workspace that haven't been promoted.

    Uses the same fast-export + marks logic as promote.py --dry-run.
    Returns 0 if workspace doesn't exist, has no commits, or all promoted.
    """
    workspace_git = Path(workspace) / ".git"
    if not workspace_git.is_dir():
        return 0

    # Check if repo has any commits
    result = _git(workspace, "rev-parse", "HEAD")
    if result.returncode != 0:
        return 0

    export_marks = Path(marks_dir) / "promote-export-marks"
    if not export_marks.exists():
        # No marks = never promoted = all commits are unpromoted
        result = _git(workspace, "rev-list", "--count", "--all")
        return int(result.stdout.strip()) if result.returncode == 0 else 0

    # With marks, fast-export only outputs commits not yet exported
    cmd = [
        "git",
        "-C",
        workspace,
        "fast-export",
        "--all",
        f"--import-marks={export_marks}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    commits = re.findall(r"^commit (.+)$", result.stdout, re.MULTILINE)
    return len(commits)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <outer-repo> <workspace>", file=sys.stderr)
        sys.exit(1)
    snapshot_workspace(sys.argv[1], sys.argv[2])
