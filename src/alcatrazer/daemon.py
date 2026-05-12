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
from alcatrazer import identity, snapshot, state
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


def _run_cycle_mirror(
    source: Path,
    target: Path,
    alcatraz_dir: Path,
    name: str,
    email: str,
    log: logging.Logger,
    last_logged_status,
):
    """Run one mirror-mode promotion cycle and emit transition-only
    log entries. Returns the resulting `PromotionOutcome` so the
    caller can pass it as `last_logged_status` on the next call.

    Log messages use git's vocabulary and the actual branch names —
    see docs/coding_conventions.md "User-facing strings speak the
    user's language". Sample lines as the user sees them in
    `.alcatrazer/promotion-daemon.log` (and via
    `python -m alcatrazer.status`):

      - PROMOTED with N > 0 and no transition:
            "Applied 3 agent commit(s) to branch 'feat/X'."
      - HELD -> PROMOTED transition:
            "Resumed: back on branch 'feat/X'. Applying 3 agent commit(s)."
      - PAUSED -> PROMOTED transition:
            "Resumed: conflict on branch 'feat/X' resolved."
      - entering HELD (OFF_PIN — most common):
            "Held: your repository is on branch 'main' but Alcatrazer
            was started on 'feat/X'. Switch back to 'feat/X' to resume."
      - entering HELD (DETACHED):
            "Held: your repository has a detached HEAD. Check out
            branch 'feat/X' to resume."
      - entering HELD (PIN_DELETED):
            "Held: branch 'feat/X' no longer exists in your
            repository. Recreate it (e.g. `git branch feat/X`)
            to resume."
      - entering PAUSED:
            "Paused: your working tree on branch 'feat/X' overlaps
            with an agent commit. Commit or stash your changes and
            Alcatrazer will resume."
      - HELD -> HELD / PAUSED -> PAUSED / steady-state PROMOTED with
        N == 0: silent (transition-only).

    Per change_promotion_machinery.md Phase 4 Step 4.4 (L915-919).
    """
    state_data = state.load_state(alcatraz_dir)
    pinned_branch = state_data.get("pinned_branch", "<unknown>")

    result = promote_mod.promote_once(source, target, alcatraz_dir, name, email)
    outcome = result.outcome
    PO = promote_mod.PromotionOutcome

    if outcome is PO.PROMOTED:
        if last_logged_status is PO.HELD:
            log.info(
                "Resumed: back on branch %r. Applying %d agent commit(s).",
                pinned_branch,
                result.commit_count,
            )
        elif last_logged_status is PO.PAUSED:
            log.info(
                "Resumed: conflict on branch %r resolved.",
                pinned_branch,
            )
        elif result.commit_count > 0:
            log.info(
                "Applied %d agent commit(s) to branch %r.",
                result.commit_count,
                pinned_branch,
            )
        # else: PROMOTED with no transition + no work — silent steady-state poll
    elif outcome is PO.HELD:
        if last_logged_status is not PO.HELD:
            pin = result.pin_status
            if pin is promote_mod.PinStatus.DETACHED:
                log.info(
                    "Held: your repository has a detached HEAD. Check out branch %r to resume.",
                    pinned_branch,
                )
            elif pin is promote_mod.PinStatus.PIN_DELETED:
                log.info(
                    "Held: branch %r no longer exists in your repository. "
                    "Recreate it (e.g. `git branch %s`) to resume.",
                    pinned_branch,
                    pinned_branch,
                )
            else:  # OFF_PIN — most common held case
                current = snapshot.current_branch(str(target)) or "<unknown>"
                log.info(
                    "Held: your repository is on branch %r but Alcatrazer "
                    "was started on %r. Switch back to %r to resume.",
                    current,
                    pinned_branch,
                    pinned_branch,
                )
    elif outcome is PO.PAUSED:
        if last_logged_status is not PO.PAUSED:
            log.warning(
                "Paused: your working tree on branch %r overlaps with an "
                "agent commit. Commit or stash your changes and Alcatrazer "
                "will resume.",
                pinned_branch,
            )

    return outcome


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

    # Mirror-mode cycles track the previous outcome to enable
    # transition-only logging via _run_cycle_mirror. alcatraz-tree
    # mode is unchanged (retired in Phase 6).
    last_logged_status = None

    # Shared between main-loop polls and the final-sync on shutdown —
    # extracted so the shutdown path doesn't duplicate branch-paused /
    # conflict-handling / marks-update logic.
    def run_cycle() -> list[str]:
        """Run one promote cycle. Returns the list of newly-promoted
        branch names (empty on alcatraz-tree mode or no-op poll). Logs
        conflicts as WARNING. Errors bubble up for the caller to log."""
        nonlocal last_logged_status
        if mode == "mirror":
            # Phase 4: new pin-based mirror cycle via promote_once.
            # All logging happens inside _run_cycle_mirror; the
            # main loop's per-cycle "Promotion cycle complete: ..."
            # log no longer fires for mirror because the structured
            # transition log entries (Promoted N / Held / Paused /
            # Resumed) replace it.
            last_logged_status = _run_cycle_mirror(
                source=source_repo,
                target=target_repo,
                alcatraz_dir=marks_dir,
                name=name,
                email=email,
                log=log,
                last_logged_status=last_logged_status,
            )
            return []
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
                run_cycle()
                if mode == "alcatraz-tree":
                    log.info("Promotion cycle complete (alcatraz-tree)")
                # mirror mode logs its own transitions in _run_cycle_mirror.
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
            run_cycle()
            if mode == "mirror":
                log.info("Final sync (%s) complete", prefix)
            else:
                log.info("Final sync (%s): alcatraz-tree cycle complete", prefix)
        except Exception as exc:
            log.error("Final sync (%s) failed: %s", prefix, exc)

        log.info("Daemon stopped")
        remove_pid(pid_file)


if __name__ == "__main__":
    main()
