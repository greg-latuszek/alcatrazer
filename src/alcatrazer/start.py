"""The `alcatrazer start` command — primary entry point for daily work.

Steps 3a-3c so far: route on `.alcatrazer/` presence; in the first-time
branch verify we are at a git repo root; helpers to read + prompt for the
promotion identity. Step 3e will orchestrate the helpers into the first-
time flow. Steps 3d, 3f-3k fill in the rest. Step 4 handles subsequent-run.
"""

import subprocess
import sys
from pathlib import Path


def cmd_start(project_dir: Path) -> int:
    """Route to first-time setup or subsequent-run based on project state."""
    if not (project_dir / ".alcatrazer").exists():
        return _first_time_setup(project_dir)
    return _subsequent_run(project_dir)


def _first_time_setup(project_dir: Path) -> int:
    if not (project_dir / ".git").exists():
        print("alcatrazer must be run from a git repository root.", file=sys.stderr)
        return 1
    print("No alcatrazer setup found in this repository.")
    print("First-time setup flow is not yet implemented.")
    return 0


def read_git_identity(project_dir: Path) -> tuple[str | None, str | None]:
    """Return (name, email) from git config — local first, global fallback, per field."""

    def get(scope: str, key: str) -> str | None:
        result = subprocess.run(
            ["git", "config", f"--{scope}", "--get", key],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    name = get("local", "user.name") or get("global", "user.name")
    email = get("local", "user.email") or get("global", "user.email")
    return name, email


def ask_promotion_identity(project_dir: Path) -> tuple[str, str]:
    """Prompt the user for the promotion identity, offering the detected one as default."""
    name, email = read_git_identity(project_dir)
    if name and email:
        print(f"Detected git identity: {name} <{email}>")
        answer = input("Use for promoted commits? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return name, email

    print("Please enter the identity to use for promoted commits.")
    entered_name = input("Name: ").strip()
    entered_email = input("Email: ").strip()
    return entered_name, entered_email


def _subsequent_run(project_dir: Path) -> int:
    print("Alcatrazer setup detected.")
    print("Subsequent-run logic is not yet implemented.")
    return 0
