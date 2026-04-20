"""The `alcatrazer start` command — primary entry point for daily work.

Steps 3a-3b so far: route on `.alcatrazer/` presence, and in the first-time
branch verify we are at a git repo root. Remaining first-time logic lands
in Steps 3c-3k; subsequent-run lands in Step 4.
"""

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


def _subsequent_run(project_dir: Path) -> int:
    print("Alcatrazer setup detected.")
    print("Subsequent-run logic is not yet implemented.")
    return 0
