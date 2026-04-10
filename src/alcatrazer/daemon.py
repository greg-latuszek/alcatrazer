#!/usr/bin/env python3
"""
Auto-promotion daemon — watches workspace for new commits
and promotes them to the outer repo.

Runs on the host side, polling at a configurable interval.
Silent by default — writes to .alcatrazer/promotion-daemon.log.

Usage:
    .alcatrazer/python -m alcatrazer.daemon
    .alcatrazer/python -m alcatrazer.daemon [--alcatraz-dir DIR] [--project-dir DIR]

Requires Python 3.11+ (for tomllib).
"""

import sys

if sys.version_info < (3, 11):
    print(
        f"ERROR: Python 3.11+ required, got {sys.version}\n"
        "Run ./src/initialize_alcatraz.sh to set up the correct Python.",
        file=sys.stderr,
    )
    sys.exit(1)

import argparse
import logging
import logging.handlers
import os
import signal
import threading
import tomllib
from pathlib import Path

# Import promote module (same package)
from alcatrazer import promote as promote_mod


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
    alcatraz_dir = (args.alcatraz_dir or project_dir / ".alcatrazer").resolve()
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

    # --- Resolve promotion identity ---
    source_repo = alcatraz_dir / "workspace"
    target_repo = project_dir
    marks_dir = alcatraz_dir
    name, email = promote_mod.resolve_identity(target_repo, toml_file, "", "")

    # --- Set up logging with rotation ---
    log_file = alcatraz_dir / "promotion-daemon.log"
    max_log_bytes = config["max_log_size"] * 1024  # config is in KB
    log = logging.getLogger("promotion-daemon")
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        str(log_file),
        maxBytes=max_log_bytes,
        backupCount=1,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    branches = config["branches"]
    mode = config["mode"]
    paused_branches = promote_mod.load_paused_branches(marks_dir)
    log.info("Daemon started (PID %d, interval=%ds, branches=%s, mode=%s)",
             os.getpid(), interval, branches, mode)

    # --- Main polling loop ---
    try:
        while not shutdown_event.is_set():
            if shutdown_event.wait(timeout=interval):
                break
            try:
                if mode == "mirror":
                    # Check if any paused branches have been resolved
                    if paused_branches:
                        resolved = promote_mod.check_resolved_conflicts(
                            target_repo, marks_dir, paused_branches,
                        )
                        for branch in resolved:
                            paused_branches.discard(branch)
                            log.info("Conflict resolved on branch %s — resuming promotion", branch)
                        if resolved:
                            promote_mod.save_paused_branches(marks_dir, paused_branches)

                    results = promote_mod.promote_with_conflict_handling(
                        source_repo, target_repo, marks_dir,
                        name, email, branches=branches,
                        paused_branches=paused_branches,
                    )
                    for branch, status in results.items():
                        if status == "conflict":
                            log.warning("CONFLICT on branch %s — promoted state "
                                        "saved to conflict/resolve-* branch. "
                                        "Resolve manually.", branch)
                    promoted = [b for b, s in results.items() if s == "promoted"]
                    if promoted:
                        log.info("Promotion cycle complete: %s", ", ".join(promoted))
                elif mode == "alcatraz-tree":
                    promote_mod.promote(source_repo, target_repo, marks_dir,
                                        name, email, branches=branches,
                                        namespace="alcatraz")
                    log.info("Promotion cycle complete (alcatraz-tree)")
            except Exception as exc:
                log.error("Promotion failed: %s", exc)
    finally:
        log.info("Daemon stopped")
        remove_pid(pid_file)


if __name__ == "__main__":
    main()
