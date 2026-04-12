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
    .alcatrazer/python -m alcatrazer.init <project-dir> <alcatrazer-dir> --non-interactive
    .alcatrazer/python -m alcatrazer.init <project-dir> <alcatrazer-dir> --reset [--force]
"""

import contextlib
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
        capture_output=True,
        text=True,
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
    # Append — ensure file ends with newline first
    with open(env_file, "a") as f:
        if env_file.stat().st_size > 0:
            with open(env_file, "rb") as rb:
                rb.seek(-1, 2)
                if rb.read(1) != b"\n":
                    f.write("\n")
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


def resolve_workspace_dir(
    project_dir: Path, alcatrazer_dir: Path, *, non_interactive: bool = False
) -> Path:
    """Resolve workspace directory — read stored selection or prompt user."""
    stored = load_workspace_dir(str(alcatrazer_dir))
    if stored:
        workspace_name = stored
    else:
        choices = generate_workspace_choices(str(project_dir))
        if non_interactive:
            workspace_name = choices[0]
        else:
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


def init_workspace(project_dir: Path, alcatrazer_dir: Path, workspace_dir: Path) -> None:
    """Initialize git repo in workspace with random identity + snapshot."""
    if (workspace_dir / ".git").is_dir():
        print(f"Workspace git repo already exists at {workspace_dir}/.git")
        print("To reinitialize, run with --reset")
        return

    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Initialize a fresh git repo
    subprocess.run(
        ["git", "init", str(workspace_dir)],
        capture_output=True,
        check=True,
    )

    # Generate random agent identity (or reuse existing one)
    name, email = ensure_identity(str(alcatrazer_dir))

    _git(str(workspace_dir), "config", "--local", "user.name", name)
    _git(str(workspace_dir), "config", "--local", "user.email", email)

    # Disable commit signing — no access to host signing keys
    _git(str(workspace_dir), "config", "--local", "commit.gpgsign", "false")

    # Override signing key paths with empty values to prevent leaking host paths
    _git(str(workspace_dir), "config", "--local", "user.signingkey", "")
    _git(str(workspace_dir), "config", "--local", "gpg.ssh.allowedSignersFile", "")

    print()
    print(f"Workspace git repo initialized at: {workspace_dir}")

    # Snapshot outer repo into workspace
    snapshot_workspace(str(project_dir), str(workspace_dir))


def add_safe_directory(workspace_dir: Path) -> None:
    """Add workspace to git safe.directory so host git can read it."""
    workspace_abs = str(workspace_dir.resolve())
    result = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        capture_output=True,
        text=True,
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
    compose = "docker compose --env-file .env -f src/alcatrazer/container/docker-compose.yml"
    print("Next steps:")
    print("  1. Fill in API keys in .env")
    print(f"  2. Run: {compose} build")
    print(f"  3. Run: {compose} run --rm workspace")


def handle_reset(project_dir: Path, alcatrazer_dir: Path, force: bool = False) -> None:
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
            str(workspace_dir),
            str(alcatrazer_dir),
        )
        if unpromoted > 0:
            print()
            print(
                f"Warning: {unpromoted} commit(s) in workspace have not been promoted"
                " to outer repo."
            )
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
    # UID ownership chain:
    #   - The directory itself is created by the host user (e.g., UID 1000)
    #   - Files INSIDE are created by agents in Docker under the phantom UID (e.g., 1007)
    #   - The host user cannot delete phantom-UID files directly
    #   - We use a disposable alpine:3 container (runs as root, UID 0) to remove
    #     the contents — root can delete files regardless of ownership
    #   - After contents are removed, Python rmdir() removes the now-empty directory
    #     (owned by the host user, so no permission issue)
    if workspace_dir and workspace_dir.is_dir():
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{workspace_dir}:/workspace",
                "alpine:3",
                "sh",
                "-c",
                "rm -rf /workspace/* /workspace/.*",
            ],
            capture_output=True,
        )
        with contextlib.suppress(OSError):
            workspace_dir.rmdir()
        print("Workspace directory cleaned.")

    # Clean .alcatrazer/ — same ownership pattern as above
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{alcatrazer_dir}:/workspace",
            "alpine:3",
            "sh",
            "-c",
            "rm -rf /workspace/* /workspace/.*",
        ],
        capture_output=True,
    )
    with contextlib.suppress(OSError):
        alcatrazer_dir.rmdir()
    print("Alcatrazer directory cleaned.")


def main() -> None:
    """Entry point called by initialize_alcatraz.sh after Python resolution."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Post-Python initialization for Alcatrazer.",
    )
    parser.add_argument("project_dir", type=Path, help="Repository root directory")
    parser.add_argument(
        "alcatrazer_dir", type=Path, help="Alcatrazer state directory (.alcatrazer/)"
    )
    parser.add_argument("--reset", action="store_true", help="Reset workspace and reinitialize")
    parser.add_argument(
        "--force", action="store_true", help="Skip unpromoted work warning during reset"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Auto-pick defaults without prompting (for CI)",
    )

    args = parser.parse_args()
    project_dir = args.project_dir.resolve()
    alcatrazer_dir = args.alcatrazer_dir.resolve()

    if args.reset:
        handle_reset(project_dir, alcatrazer_dir, force=args.force)
        print("Re-running initialization...")
        print()

    workspace_dir = resolve_workspace_dir(
        project_dir, alcatrazer_dir, non_interactive=args.non_interactive
    )
    init_workspace(project_dir, alcatrazer_dir, workspace_dir)
    add_safe_directory(workspace_dir)
    print_summary(alcatrazer_dir, workspace_dir)


if __name__ == "__main__":
    main()
