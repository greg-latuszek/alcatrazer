"""
Snapshot extraction from outer repo into workspace.

Copies the current state of the outer repo's main branch into
.alcatraz/workspace/ as a flat snapshot (files only, no history).
"""

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
        capture_output=True, text=True,
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
