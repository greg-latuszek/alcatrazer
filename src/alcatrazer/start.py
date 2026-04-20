"""The `alcatrazer start` command — primary entry point for daily work.

Step 3a skeleton: decides whether this is a first-time setup or a
subsequent run by checking for `.alcatrazer/` in the project directory,
then delegates to the appropriate handler. Both handlers are placeholders
at this step; Steps 3b-3k fill in first-time setup, Step 4 fills in the
subsequent-run logic.
"""

from pathlib import Path


def cmd_start(project_dir: Path) -> int:
    """Route to first-time setup or subsequent-run based on project state."""
    if not (project_dir / ".alcatrazer").exists():
        return _first_time_setup(project_dir)
    return _subsequent_run(project_dir)


def _first_time_setup(project_dir: Path) -> int:
    print("No alcatrazer setup found in this repository.")
    print("First-time setup flow is not yet implemented.")
    return 0


def _subsequent_run(project_dir: Path) -> int:
    print("Alcatrazer setup detected.")
    print("Subsequent-run logic is not yet implemented.")
    return 0
