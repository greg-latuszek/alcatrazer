#!/usr/bin/env python3
"""
Auto-promotion daemon — watches .alcatraz/workspace/ for new commits
and promotes them to the outer repo using promote.sh.

Runs on the host side, polling at a configurable interval.
Silent by default — writes to .alcatraz/promotion-daemon.log.

Usage:
    src/watch_alcatraz.py [--alcatraz-dir DIR] [--project-dir DIR]

Requires Python 3.11+ (for tomllib).
"""

import argparse
import os
import signal
import sys
import threading
import tomllib
from pathlib import Path


# --- Default config ---

DEFAULTS = {
    "interval": 5,
    "branches": "all",
    "mode": "mirror",
    "verbosity": "normal",
    "max_log_size": 512,
}


def load_config(toml_path: Path) -> dict:
    """Load [promotion-daemon] config from alcatrazer.toml, with defaults."""
    config = dict(DEFAULTS)
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        daemon_section = data.get("promotion-daemon", {})
        config.update(daemon_section)
    return config


def check_workspace(alcatraz_dir: Path) -> None:
    """Exit if workspace/.git doesn't exist."""
    workspace_git = alcatraz_dir / "workspace" / ".git"
    if not workspace_git.is_dir():
        print(
            f"ERROR: No workspace found at {workspace_git}\n"
            "Run ./src/initialize_alcatraz.sh first to create the workspace.",
            file=sys.stderr,
        )
        sys.exit(1)


def check_pid(pid_file: Path) -> None:
    """Single-instance guard. Exit if another daemon is running."""
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            # Check if process is still alive
            os.kill(existing_pid, 0)
            # Process exists — refuse to start
            print(
                f"ERROR: Daemon already running (PID {existing_pid}).\n"
                f"Stop it first or remove {pid_file} if stale.",
                file=sys.stderr,
            )
            sys.exit(1)
        except (ProcessLookupError, PermissionError):
            # Stale PID file — process is dead
            pid_file.unlink(missing_ok=True)
        except ValueError:
            # Corrupt PID file
            pid_file.unlink(missing_ok=True)


def write_pid(pid_file: Path) -> None:
    """Write current process PID to file."""
    pid_file.write_text(str(os.getpid()) + "\n")


def remove_pid(pid_file: Path) -> None:
    """Remove PID file if it exists."""
    pid_file.unlink(missing_ok=True)


def main():
    script_dir = Path(__file__).resolve().parent
    default_project_dir = script_dir.parent

    parser = argparse.ArgumentParser(description="Alcatrazer promotion daemon")
    parser.add_argument("--alcatraz-dir", type=Path, default=None)
    parser.add_argument("--project-dir", type=Path, default=default_project_dir)
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    alcatraz_dir = (args.alcatraz_dir or project_dir / ".alcatraz").resolve()
    pid_file = alcatraz_dir / "promotion-daemon.pid"
    toml_file = project_dir / "alcatrazer.toml"

    # --- Startup checks ---
    check_workspace(alcatraz_dir)
    check_pid(pid_file)
    write_pid(pid_file)

    # --- Signal handling for clean shutdown ---
    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # --- Load config ---
    config = load_config(toml_file)
    interval = config["interval"]

    # --- Main polling loop ---
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=interval)
    finally:
        remove_pid(pid_file)


if __name__ == "__main__":
    main()
