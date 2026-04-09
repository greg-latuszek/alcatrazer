#!/usr/bin/env python3
"""
Live log viewer for the promotion daemon.

Tails .alcatrazer/promotion-daemon.log so you can watch promotion
activity in real time from a separate terminal.

Usage:
    .alcatrazer/python src/inspect_promotion.py
    .alcatrazer/python src/inspect_promotion.py --alcatraz-dir <dir>
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
        print("Start it with: .alcatrazer/python src/watch_alcatraz.py")
        sys.exit(1)

    tail_follow(log_file)


if __name__ == "__main__":
    main()
