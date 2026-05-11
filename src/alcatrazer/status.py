#!/usr/bin/env python3
"""
Status surface for an Alcatrazer workspace.

Today this module provides a live log viewer that tails
`.alcatrazer/promotion-daemon.log` so you can watch promotion
activity in real time from a separate terminal.

Phase 5 of change_promotion_machinery.md expands this module to back
the `alcatrazer status` CLI command, rendering the active / held /
paused state from `state.json` (plus, optionally, the log tail this
module already does). The module is named `status` (renamed from
the original `inspect` in Phase 3) to anticipate that consolidation.

The historical name `inspect` was retired because it shadowed
Python's stdlib `inspect` module whenever any code in
`src/alcatrazer/` ran as a script (Python puts the script's
directory at the head of `sys.path`). That shadow broke any code
path that hit `dataclasses.@dataclass`, which internally calls
`inspect.get_annotations`. Renaming the file removes the shadow
permanently.

Usage:
    .alcatrazer/python -m alcatrazer.status
    .alcatrazer/python -m alcatrazer.status --alcatraz-dir <dir>
"""

import sys

if sys.version_info < (3, 11):
    print(
        f"ERROR: Python 3.11+ required, got {sys.version}",
        file=sys.stderr,
    )
    sys.exit(1)

import argparse
import time
from pathlib import Path


def tail_follow(path: Path) -> None:
    """Tail -f implementation: print new lines as they appear."""
    with open(path) as f:
        # Start from the end of file
        f.seek(0, 2)
        print(f"--- Tailing {path} (Ctrl+C to stop) ---")
        print()
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n--- Stopped ---")


def main():
    script_dir = Path(__file__).resolve().parent
    default_project_dir = script_dir.parent

    parser = argparse.ArgumentParser(description="View promotion daemon log")
    parser.add_argument("--alcatraz-dir", type=Path, default=None)
    parser.add_argument("--project-dir", type=Path, default=default_project_dir)
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    alcatraz_dir = (args.alcatraz_dir or project_dir / ".alcatrazer").resolve()
    log_file = alcatraz_dir / "promotion-daemon.log"

    if not log_file.exists():
        print(f"No log file found at {log_file}")
        print()
        print("The promotion daemon hasn't written any logs yet.")
        print("Start it with: .alcatrazer/python -m alcatrazer.daemon")
        print("Then watch via:  .alcatrazer/python -m alcatrazer.status")
        sys.exit(1)

    tail_follow(log_file)


if __name__ == "__main__":
    main()
