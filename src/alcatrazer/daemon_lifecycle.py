"""CLI-side helpers for orchestrating the sync daemon process.

Daemon's own code (`alcatrazer.daemon`) handles polling, promotion, and
graceful shutdown. This module handles the *other* side of the fence:
how `alcatrazer start` spawns it, how `alcatrazer stop` / `clear`
terminates it cleanly and surfaces the outcome.

Scope for Step 5.7:
- `launch_sync_daemon(project_dir)` — spawn (or self-heal an existing
  alive daemon) and return a `DaemonLaunchInfo` for the CLI to print.
- `shutdown_sync_daemon(project_dir)` — state.json flip + SIGTERM +
  wait + log tail (to be added in Step 5.7d).

Kept separate from `alcatrazer.start` (the CLI command module) so the
command handlers stay focused on orchestration and the daemon-process
mechanics have a single home that tests can exercise in isolation.
"""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from alcatrazer import daemon


@dataclass(frozen=True)
class DaemonLaunchInfo:
    """What `launch_sync_daemon` reports to the CLI for the user-facing
    transparency block (see install_method.md 'Sync daemon launch')."""

    pid: int
    pid_file: Path
    log_file: Path
    config_file: Path
    interval: int
    was_already_running: bool


class DaemonLaunchError(RuntimeError):
    """Raised when the daemon fails to signal readiness (PID file never
    appears within the timeout). CLI surfaces this as a warning — does
    NOT fail `alcatrazer start`, since the Alcatraz itself is up."""


def _existing_daemon_pid(pid_file: Path) -> int | None:
    """Return the live daemon's PID, or None if no live daemon exists.

    Side effect: cleans up a stale PID file when the process is dead,
    so the self-heal path can safely re-spawn without a spurious "pid
    file already exists" collision.
    """
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return None
    except PermissionError:
        # Some other process we can't signal — treat as alive; CLI will
        # find out via the daemon log if something's off.
        return pid


def launch_sync_daemon(
    project_dir: Path,
    *,
    timeout: float = 2.0,
) -> DaemonLaunchInfo:
    """Spawn the sync daemon for `project_dir`, or return info about
    an already-running one (self-heal).

    Self-heal semantics:
    - PID file absent → spawn, wait for PID file, return info.
    - PID file present + process alive → no-op spawn, return existing info.
    - PID file present + process dead (stale) → clean up, spawn fresh,
      return new info.
    - PID file present + corrupt → clean up, spawn fresh.

    Raises `DaemonLaunchError` only when a fresh spawn fails to signal
    readiness within `timeout` seconds. Callers should catch this and
    treat it as a non-fatal warning — the Alcatraz itself is running;
    only the sync daemon failed to come up.
    """
    alcatraz_dir = (project_dir / ".alcatrazer").resolve()
    pid_file = alcatraz_dir / "promotion-daemon.pid"
    log_file = alcatraz_dir / "promotion-daemon.log"
    config_file = alcatraz_dir / "config.toml"

    interval = daemon.load_config(config_file).get("interval", daemon.DEFAULTS["interval"])

    existing_pid = _existing_daemon_pid(pid_file)
    if existing_pid is not None:
        return DaemonLaunchInfo(
            pid=existing_pid,
            pid_file=pid_file,
            log_file=log_file,
            config_file=config_file,
            interval=interval,
            was_already_running=True,
        )

    # Spawn detached — the daemon outlives the CLI process.
    # start_new_session=True puts the daemon in its own process group so
    # terminal signals to the CLI (e.g. Ctrl+C while `alcatrazer start`
    # is still printing) don't propagate to the daemon.
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "alcatrazer.daemon",
            "--project-dir",
            str(project_dir),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for the daemon to signal readiness by writing its PID file.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
            except (ValueError, OSError):
                # Partially-written or race — keep polling.
                time.sleep(0.05)
                continue
            return DaemonLaunchInfo(
                pid=pid,
                pid_file=pid_file,
                log_file=log_file,
                config_file=config_file,
                interval=interval,
                was_already_running=False,
            )
        time.sleep(0.05)

    raise DaemonLaunchError(f"Sync daemon failed to start within {timeout}s; see {log_file}")
