"""
Post-Python initialization logic.

Handles everything after Python 3.11+ is resolved:
- Workspace directory selection (random name, user picks from 3)
- Git init + random agent identity
- Snapshot outer repo into workspace
- safe.directory configuration
- Summary output

Called by initialize_alcatraz.sh after Step 3 (Python resolution).

Usage:
    .alcatrazer/python -m alcatrazer.init <project-dir> <alcatrazer-dir>
    .alcatrazer/python -m alcatrazer.init <project-dir> <alcatrazer-dir> --reset [--force]
"""

import os
import subprocess
import sys
from pathlib import Path

from alcatrazer.identity import (
    ensure_identity,
    generate_workspace_choices,
    load_workspace_dir,
    store_workspace_dir,
)
from alcatrazer.snapshot import count_unpromoted_commits, snapshot_workspace


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True,
    )


def _set_env_var(env_file: Path, key: str, value: str) -> None:
    """Set a key=value in the .env file (update if exists, append if not)."""
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                env_file.write_text("\n".join(lines) + "\n")
                return
    # Append
    with open(env_file, "a") as f:
        f.write(f"{key}={value}\n")


def _add_to_gitignore(project_dir: Path, entry: str) -> None:
    """Add an entry to .gitignore if not already present."""
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if f"{entry}/" in content.splitlines() or entry in content.splitlines():
            return
        with open(gitignore, "a") as f:
            f.write(f"{entry}/\n")
    else:
        gitignore.write_text(f"{entry}/\n")


def resolve_workspace_dir(project_dir: Path, alcatrazer_dir: Path) -> Path:
    """Resolve workspace directory — read stored selection or prompt user."""
    stored = load_workspace_dir(str(alcatrazer_dir))
    if stored:
        workspace_name = stored
    else:
        choices = generate_workspace_choices(str(project_dir))
        print()
        print("Choose a workspace directory name (this will be mounted into Docker):")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}. {choice}")
        print()
        try:
            pick = input("Choose [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            pick = "1"
        idx = int(pick) - 1 if pick in ("1", "2", "3") else 0
        workspace_name = choices[idx]
        store_workspace_dir(str(alcatrazer_dir), workspace_name)
        print(f"Workspace directory: {workspace_name}")

    workspace_dir = project_dir / workspace_name

    # Add to .env for docker-compose interpolation
    _set_env_var(project_dir / ".env", "WORKSPACE_DIR", workspace_name)

    # Add to .gitignore
    _add_to_gitignore(project_dir, workspace_name)

    return workspace_dir


def init_workspace(project_dir: Path, alcatrazer_dir: Path,
                   workspace_dir: Path) -> None:
    """Initialize git repo in workspace with random identity + snapshot."""
    if (workspace_dir / ".git").is_dir():
        print(f"Workspace git repo already exists at {workspace_dir}/.git")
        print("To reinitialize, run with --reset")
        return

    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Initialize a fresh git repo
    subprocess.run(
        ["git", "init", str(workspace_dir)],
        capture_output=True, check=True,
    )

    # Generate random agent identity (or reuse existing one)
    name, email = ensure_identity(str(alcatrazer_dir))

    _git(str(workspace_dir), "config", "user.name", name)
    _git(str(workspace_dir), "config", "user.email", email)

    # Disable commit signing — no access to host signing keys
    _git(str(workspace_dir), "config", "commit.gpgsign", "false")

    # Override signing key paths with empty values to prevent leaking host paths
    _git(str(workspace_dir), "config", "user.signingkey", "")
    _git(str(workspace_dir), "config", "gpg.ssh.allowedSignersFile", "")

    print()
    print(f"Workspace git repo initialized at: {workspace_dir}")

    # Snapshot outer repo into workspace
    snapshot_workspace(str(project_dir), str(workspace_dir))


def add_safe_directory(workspace_dir: Path) -> None:
    """Add workspace to git safe.directory so host git can read it."""
    workspace_abs = str(workspace_dir.resolve())
    result = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        capture_output=True, text=True,
    )
    existing = result.stdout.strip().splitlines()
    if workspace_abs in existing:
        print(f"safe.directory already configured for {workspace_abs}")
    else:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", workspace_abs],
            check=True,
        )
        print(f"Added {workspace_abs} to git safe.directory")


def print_summary(alcatrazer_dir: Path, workspace_dir: Path) -> None:
    """Print initialization summary."""
    python_path = alcatrazer_dir / "python"
    try:
        resolved = os.readlink(python_path)
    except OSError:
        resolved = "not resolved"

    uid_file = alcatrazer_dir / "uid"
    uid = uid_file.read_text().strip() if uid_file.exists() else "unknown"

    identity_file = alcatrazer_dir / "agent-identity"
    if identity_file.exists():
        lines = identity_file.read_text().strip().split("\n")
        agent_name, agent_email = lines[0], lines[1]
    else:
        agent_name, agent_email = "unknown", "unknown"

    print()
    print("Alcatrazer configuration:")
    print(f"  UID/GID:      {uid} (phantom — does not exist on host)")
    print(f"  Workspace:    {workspace_dir}")
    print(f"  Python:       {resolved}")
    print(f"  Git identity: {agent_name} <{agent_email}>")
    print()
    print("Local git config:")
    result = _git(str(workspace_dir), "config", "--local", "--list")
    print(result.stdout.rstrip())
    print()
    print("Next steps:")
    print("  1. Fill in API keys in .env")
    print("  2. Run: docker compose -f src/alcatrazer/container/docker-compose.yml build")
    print("  3. Run: docker compose -f src/alcatrazer/container/docker-compose.yml run --rm workspace")


def handle_reset(project_dir: Path, alcatrazer_dir: Path,
                 force: bool = False) -> None:
    """Handle --reset: check for unpromoted work, clean directories."""
    if not alcatrazer_dir.is_dir():
        print("No alcatrazer directory to clean.")
        return

    # Resolve workspace dir from stored selection
    workspace_dir = None
    ws_file = alcatrazer_dir / "workspace-dir"
    if ws_file.exists():
        ws_name = ws_file.read_text().strip()
        workspace_dir = project_dir / ws_name

    # Check for unpromoted work
    if not force and workspace_dir and (workspace_dir / ".git").is_dir():
        unpromoted = count_unpromoted_commits(
            str(workspace_dir), str(alcatrazer_dir),
        )
        if unpromoted > 0:
            print()
            print(f"Warning: {unpromoted} commit(s) in workspace have not been promoted to outer repo.")
            print("Proceeding with --reset will discard them.")
            print()
            print("  1. Proceed — discard workspace, re-snapshot, reinitialize")
            print("  2. Cancel — abort reset, no changes")
            print()
            try:
                choice = input("Choose [1/2]: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = "2"
            if choice != "1":
                print("Reset cancelled.")
                sys.exit(0)

    print("Resetting alcatrazer...")

    # Clean workspace directory (separate from .alcatrazer/)
    if workspace_dir and workspace_dir.is_dir():
        subprocess.run(
            ["docker", "run", "--rm",
             "-v", f"{workspace_dir}:/workspace",
             "ubuntu:24.04",
             "sh", "-c", "rm -rf /workspace/* /workspace/.*"],
            capture_output=True,
        )
        try:
            workspace_dir.rmdir()
        except OSError:
            pass
        print("Workspace directory cleaned.")

    # Clean .alcatrazer/
    subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{alcatrazer_dir}:/workspace",
         "ubuntu:24.04",
         "sh", "-c", "rm -rf /workspace/* /workspace/.*"],
        capture_output=True,
    )
    try:
        alcatrazer_dir.rmdir()
    except OSError:
        pass
    print("Alcatrazer directory cleaned.")


def main() -> None:
    """Entry point called by initialize_alcatraz.sh after Python resolution."""
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <project-dir> <alcatrazer-dir> [--reset] [--force]",
            file=sys.stderr,
        )
        sys.exit(1)

    project_dir = Path(sys.argv[1]).resolve()
    alcatrazer_dir = Path(sys.argv[2]).resolve()
    reset = "--reset" in sys.argv
    force = "--force" in sys.argv

    if reset:
        handle_reset(project_dir, alcatrazer_dir, force=force)
        print("Re-running initialization...")
        print()

    workspace_dir = resolve_workspace_dir(project_dir, alcatrazer_dir)
    init_workspace(project_dir, alcatrazer_dir, workspace_dir)
    add_safe_directory(workspace_dir)
    print_summary(alcatrazer_dir, workspace_dir)


if __name__ == "__main__":
    main()
