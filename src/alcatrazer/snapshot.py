"""
Snapshot extraction from outer repo into workspace.

Copies the current state of the outer repo's main branch into
.alcatrazer/workspace/ as a flat snapshot (files only, no history).
"""

import re
import subprocess
import sys
from pathlib import Path

from alcatrazer import state
from alcatrazer.git_runner import run_git_command


class NotAGitRepoError(Exception):
    """Raised when the target directory is not inside a git repository."""


class AmbiguousBranchError(Exception):
    """Raised when both main and master exist and origin/HEAD is not set."""


def require_git_repo(path: str) -> Path:
    """Verify path is inside a git working tree. Return the repo root.

    Raises NotAGitRepoError if not inside a git repository.
    """
    result = run_git_command(["-C", path, "rev-parse", "--show-toplevel"])
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
    result = run_git_command(["-C", repo, "rev-parse", "HEAD"])
    if result.returncode != 0:
        return None

    # Tier 1: origin/HEAD
    result = run_git_command(["-C", repo, "symbolic-ref", "refs/remotes/origin/HEAD"])
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]

    # Tier 2: existence check
    has_main = (
        run_git_command(["-C", repo, "rev-parse", "--verify", "refs/heads/main"]).returncode == 0
    )
    has_master = (
        run_git_command(["-C", repo, "rev-parse", "--verify", "refs/heads/master"]).returncode == 0
    )

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


def is_detached_head(repo: str) -> bool:
    """True iff `repo` is a git repo with commits but no branch checked
    out (`git symbolic-ref --short HEAD` fails while `git rev-parse HEAD`
    succeeds).

    Distinguishes detached HEAD from two adjacent states that also lack
    a branch but are NOT detached:
    - non-git directory (`rev-parse HEAD` fails) — different problem
    - empty git repo (`rev-parse HEAD` fails because no commits)
      — greenfield, snapshot can still produce an empty workspace

    Used by `start.cmd_start` to refuse before snapshot runs (Step 1.8 of
    change_promotion_machinery.md): promotion is bound to a starting
    branch, and detached HEAD has no branch to bind to.
    """
    has_head = run_git_command(["-C", repo, "rev-parse", "HEAD"]).returncode == 0
    if not has_head:
        return False
    on_branch = run_git_command(["-C", repo, "symbolic-ref", "--short", "HEAD"]).returncode == 0
    return not on_branch


def is_unborn_head(repo: str) -> bool:
    """True if `repo` is on a branch that has no commit yet — an *unborn*
    branch (`git symbolic-ref --short HEAD` succeeds while `git rev-parse
    --verify HEAD` fails).

    The greenfield sibling of is_detached_head — the two predicates name
    the two HEAD anomalies along opposite axes:
    - unborn   = a branch name, but no commit (a fresh `git init`)
    - detached = a commit, but no branch name

    A non-git directory is neither (symbolic-ref fails too). Used by
    extract_snapshot to skip archiving when there is no tree to archive.
    """
    on_branch = (
        run_git_command(["-C", repo, "symbolic-ref", "--quiet", "--short", "HEAD"]).returncode == 0
    )
    if not on_branch:
        return False
    head_is_born = (
        run_git_command(["-C", repo, "rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 0
    )
    return not head_is_born


def current_branch(repo: str) -> str | None:
    """Return the currently checked-out branch name, or None when HEAD is
    not on a branch at all (detached HEAD, or a non-git directory).

    A freshly-`git init`'d repo with no commits yet (an *unborn* branch)
    still has a branch name — HEAD is a symbolic ref pointing at
    `refs/heads/<init.defaultBranch>` — and that name IS returned.
    Promotion pins to this name, so the greenfield case must report `main`
    (the branch the first commit will create), not None. The earlier
    implementation discarded it via a `rev-parse HEAD` guard, which is what
    produced the dead `pinned_branch=None` workspace. The unborn-vs-born
    distinction, when a caller needs it, lives in is_unborn_head.
    """
    result = run_git_command(["-C", repo, "symbolic-ref", "--quiet", "--short", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def extract_snapshot(repo: str, branch: str | None, workspace: str) -> None:
    """Extract files from repo's branch into workspace via git archive.

    No-op when there is no tree to archive: either branch is None (detached
    HEAD, rejected upstream) or the branch is unborn — a greenfield repo
    that carries a branch name but no commit yet, where `git archive
    <branch>` would fail. Excludes .alcatrazer/ and .env even if tracked.
    """
    if branch is None or is_unborn_head(repo):
        return

    # git archive exports tracked files, piped to tar for extraction
    archive = run_git_command(
        ["-C", repo, "archive", branch],
        check=True,
        text=False,
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
    run_git_command(["-C", workspace, "add", "-A"])
    run_git_command(["-C", workspace, "commit", "--allow-empty", "-m", "Initial commit"])


def snapshot_workspace(outer_repo: str, workspace: str, alcatraz_dir: str | None = None) -> None:
    """Full snapshot flow: validate repo, detect branch, extract, filter, commit.

    Called by `alcatrazer.start.create_workspace` after `git init` + identity
    are configured on the workspace.

    When `alcatraz_dir` is provided, records two fields in
    `<alcatraz_dir>/state.json` (one atomic write):

    - `inner_root` — SHA of the workspace's `Initial commit`. Format-patch
      range boundary for every later promotion cycle.
    - `pinned_branch` — outer's currently-checked-out branch name at
      snapshot time. A greenfield outer (no commits yet) still pins to its
      unborn branch name (`main`); only detached HEAD yields None, and that
      case is preempted in `start.cmd_start` (Step 1.8) so the snapshot
      never runs on detached HEAD in production.

    Both are written once at workspace creation, never updated — promotion
    is bound to this branch and origin commit for the workspace's life
    (per change_promotion_machinery.md "Recording state"). Older callers
    that pass only `(outer_repo, workspace)` get the legacy no-state-write
    behavior so this addition is non-breaking per Phase 1's "additive, no
    breakage" rule.
    """
    require_git_repo(outer_repo)
    # Snapshot from whatever branch outer currently has checked out
    # — this is the "starting branch" the workspace is bound to. The
    # prior "default branch only" rule is retired per
    # change_promotion_machinery.md (Step 1.6); detached-HEAD is
    # rejected upstream in cmd_start (Step 1.8) so by the time we get
    # here, current_branch returns a real branch name. For a greenfield
    # outer (no commits) that name is the unborn branch (`main`), and
    # extract_snapshot no-ops because there is no tree to archive.
    branch = current_branch(outer_repo)
    extract_snapshot(outer_repo, branch, workspace)
    filter_gitignore(workspace)
    create_initial_commit(workspace)

    if alcatraz_dir is not None:
        inner_root_sha = run_git_command(["-C", workspace, "rev-parse", "HEAD"]).stdout.strip()
        pinned_branch = current_branch(outer_repo)
        state.update_state(
            Path(alcatraz_dir),
            inner_root=inner_root_sha,
            pinned_branch=pinned_branch,
        )


def count_unpromoted_commits(workspace: str, marks_dir: str) -> int:
    """Count commits in workspace that haven't been promoted.

    Uses the same fast-export + marks logic as promote.py --dry-run.
    Returns 0 if workspace doesn't exist, has no commits, or all promoted.
    """
    workspace_git = Path(workspace) / ".git"
    if not workspace_git.is_dir():
        return 0

    # Check if repo has any commits
    result = run_git_command(["-C", workspace, "rev-parse", "HEAD"])
    if result.returncode != 0:
        return 0

    export_marks = Path(marks_dir) / "promote-export-marks"
    if not export_marks.exists():
        # No marks = never promoted = all commits are unpromoted
        result = run_git_command(["-C", workspace, "rev-list", "--count", "--all"])
        return int(result.stdout.strip()) if result.returncode == 0 else 0

    # With marks, fast-export only outputs commits not yet exported
    result = run_git_command(
        ["-C", workspace, "fast-export", "--all", f"--import-marks={export_marks}"]
    )
    commits = re.findall(r"^commit (.+)$", result.stdout, re.MULTILINE)
    return len(commits)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <outer-repo> <workspace>", file=sys.stderr)
        sys.exit(1)
    snapshot_workspace(sys.argv[1], sys.argv[2])
