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
        "Run `alcatrazer start` to set up the correct Python.",
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

# Import sibling modules
from alcatrazer import identity, state
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
    """Load [promotion-daemon] config from .alcatrazer/config.toml, with defaults."""
    config = dict(DEFAULTS)
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        daemon_section = data.get("promotion-daemon", {})
        config.update(daemon_section)
    return config


def resolve_workspace(project_dir: Path, alcatraz_dir: Path) -> Path:
    """Resolve the workspace path via the `.alcatrazer/workspace-dir`
    pointer written by `alcatrazer init`. Exit cleanly if the pointer
    is missing (user never ran init) or the pointed-at directory isn't
    a valid git repo.

    Returns the workspace Path on success.
    """
    workspace_name = identity.load_workspace_dir(str(alcatraz_dir))
    if workspace_name is None:
        print(
            f"ERROR: No workspace pointer at {alcatraz_dir}/workspace-dir.\n"
            "Run `alcatrazer init` then `alcatrazer start` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    workspace = project_dir / workspace_name
    if not (workspace / ".git").is_dir():
        print(
            f"ERROR: No workspace git directory at {workspace}/.git.\n"
            "Run `alcatrazer start` to recreate the workspace.",
            file=sys.stderr,
        )
        sys.exit(1)
    return workspace


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


def _default_project_dir() -> Path:
    """Best-effort default for `--project-dir` when nothing is passed.

    Dev checkout (`PYTHONPATH=src python -m alcatrazer.daemon`): __file__
    is `<repo>/src/alcatrazer/daemon.py`, so three parents up is the repo
    root.
    Installed layout (`<repo>/.alcatrazer/src/alcatrazer/daemon.py`):
    four parents up. Detect by whether `.alcatrazer` is in the path.

    Callers wired through `alcatrazer` CLI will always pass --project-dir
    explicitly (future lifecycle wiring phase). This default is only a
    fallback for `mise run start-promotion` during development.
    """
    script_dir = Path(__file__).resolve().parent
    parts = script_dir.parts
    if ".alcatrazer" in parts:
        # Walk up until we exit the .alcatrazer/ tree.
        idx = len(parts) - 1 - parts[::-1].index(".alcatrazer")
        return Path(*parts[:idx])
    # Dev layout: src/alcatrazer → src → repo root.
    return script_dir.parent.parent


def main():
    default_project_dir = _default_project_dir()

    parser = argparse.ArgumentParser(description="Alcatrazer promotion daemon")
    parser.add_argument("--alcatraz-dir", type=Path, default=None)
    parser.add_argument("--project-dir", type=Path, default=default_project_dir)
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    alcatraz_dir = (args.alcatraz_dir or project_dir / ".alcatrazer").resolve()
    pid_file = alcatraz_dir / "promotion-daemon.pid"
    # Per-developer config lives under .alcatrazer/ (install_method.md config
    # split). The public coding-environment.toml at the repo root has a
    # different schema — no [promotion] / [promotion-daemon] sections.
    toml_file = alcatraz_dir / "config.toml"

    # --- Startup checks ---
    workspace_path = resolve_workspace(project_dir, alcatraz_dir)
    check_pid(pid_file)
    write_pid(pid_file)

    # Tell git the inner-repo path is safe to operate on despite
    # ownership mismatch. The Alcatraz's entrypoint chowns /workspace
    # to the phantom UID so the agent user inside the container can
    # write to the bind-mounted workspace; that chown propagates to
    # the host, leaving the inner repo owned by a UID the host user
    # doesn't recognize. Git 2.35+ refuses to operate on such repos
    # by default ("dubious ownership"). Setting safe.directory for
    # this specific path via env-var config (additive to the user's
    # normal gitconfig — no persistent side-effect on ~/.gitconfig)
    # unblocks promote's fast-export without opening the gate for any
    # other path.
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_0"] = str(workspace_path)

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
    source_repo = workspace_path
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
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    branches = config["branches"]
    mode = config["mode"]
    paused_branches = promote_mod.load_paused_branches(marks_dir)
    log.info(
        "Daemon started (PID %d, interval=%ds, branches=%s, mode=%s)",
        os.getpid(),
        interval,
        branches,
        mode,
    )

    # Shared between main-loop polls and the final-sync on shutdown —
    # extracted so the shutdown path doesn't duplicate branch-paused /
    # conflict-handling / marks-update logic.
    def run_cycle() -> list[str]:
        """Run one promote cycle. Returns the list of newly-promoted
        branch names (empty on alcatraz-tree mode or no-op poll). Logs
        conflicts as WARNING. Errors bubble up for the caller to log."""
        if mode == "mirror":
            if paused_branches:
                resolved = promote_mod.check_resolved_conflicts(
                    target_repo,
                    marks_dir,
                    paused_branches,
                )
                for branch in resolved:
                    paused_branches.discard(branch)
                    log.info("Conflict resolved on branch %s — resuming promotion", branch)
                if resolved:
                    promote_mod.save_paused_branches(marks_dir, paused_branches)

            results = promote_mod.promote_with_conflict_handling(
                source_repo,
                target_repo,
                marks_dir,
                name,
                email,
                branches=branches,
                paused_branches=paused_branches,
            )
            for branch, status in results.items():
                if status == "conflict":
                    log.warning(
                        "CONFLICT on branch %s — promoted state "
                        "saved to conflict/resolve-* branch. "
                        "Resolve manually.",
                        branch,
                    )
            return [b for b, s in results.items() if s == "promoted"]
        if mode == "alcatraz-tree":
            promote_mod.promote(
                source_repo,
                target_repo,
                marks_dir,
                name,
                email,
                branches=branches,
                namespace="alcatraz",
            )
            return []
        return []

    # --- Main polling loop ---
    try:
        while not shutdown_event.is_set():
            if shutdown_event.wait(timeout=interval):
                break
            try:
                promoted = run_cycle()
                if mode == "mirror" and promoted:
                    log.info("Promotion cycle complete: %s", ", ".join(promoted))
                elif mode == "alcatraz-tree":
                    log.info("Promotion cycle complete (alcatraz-tree)")
            except Exception as exc:
                log.error("Promotion failed: %s", exc)
    finally:
        # Final sync — closes the race where an agent commit happened
        # between the last poll and SIGTERM. Docker is down by contract
        # (alcatrazer stop/clear order: docker first, then daemon
        # signal) so this is safe in the graceful case; in the
        # unexpected case it's best-effort and the marks-file eventual-
        # consistency guarantee catches any miss on the next start.
        # Only the log prefix branches on intent — behavior does not.
        shutdown_intent = state.load_state(alcatraz_dir).get("daemon_shutdown")
        prefix = "graceful shutdown" if shutdown_intent == "requested" else "unexpected shutdown"
        try:
            promoted = run_cycle()
            if mode == "mirror":
                log.info("Final sync (%s): %d commit(s) synced", prefix, len(promoted))
            else:
                log.info("Final sync (%s): alcatraz-tree cycle complete", prefix)
        except Exception as exc:
            log.error("Final sync (%s) failed: %s", prefix, exc)

        log.info("Daemon stopped")
        remove_pid(pid_file)


if __name__ == "__main__":
    main()
