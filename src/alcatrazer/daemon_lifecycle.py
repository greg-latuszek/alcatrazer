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

import contextlib
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from alcatrazer import daemon, identity, state


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

    # Spawn detached — the daemon outlives the CLI process. We use
    # os.posix_spawn (not subprocess.Popen) deliberately: Popen leaves a
    # child-tracking object behind, and because the daemon is long-lived
    # we never reap it, so its __del__ emits a spurious "subprocess is
    # still running" ResourceWarning when the GC collects it. posix_spawn
    # returns a bare pid with no such object. The real pid we report comes
    # from the daemon's own PID file (read below), so we discard this one.
    # setpgroup=0 puts the daemon in its own process group so terminal
    # signals to the CLI (e.g. Ctrl+C while `alcatrazer start` is still
    # printing) don't propagate to the daemon. file_actions points its
    # stdio at /dev/null inside the child (no parent fds leak).
    os.posix_spawn(
        sys.executable,
        [
            sys.executable,
            "-m",
            "alcatrazer.daemon",
            "--project-dir",
            str(project_dir),
        ],
        os.environ,
        file_actions=[
            (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
            (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
            (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
        ],
        setpgroup=0,
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


def print_launch_info(info: DaemonLaunchInfo, project_dir: Path) -> None:
    """User-facing output for the daemon-launch transparency block —
    what / when / PID / files — or a single-line "already running"
    note on the self-heal path. Format defined in install_method.md
    CLI section ("Sync daemon launch")."""
    if info.was_already_running:
        print(f"Sync daemon already running (PID {info.pid}).")
        return

    alcatraz_dir = (project_dir / ".alcatrazer").resolve()
    workspace_name = identity.load_workspace_dir(str(alcatraz_dir)) or "<workspace>"

    print()
    print("Sync daemon started.")
    print("  What:   one-way sync from the Alcatraz workspace → your repository")
    print(f"          (commits in {workspace_name}/ → commits in {project_dir})")
    print(f"  When:   polls every {info.interval}s (see {info.config_file} [promotion-daemon])")
    print(f"  PID:    {info.pid}  (written to {info.pid_file})")
    print(f"  Logs:   {info.log_file}")
    print()
    print("One-way: agent commits flow OUT, your commits do NOT flow in.")
    print("Your repository's default branch was snapshotted into the Alcatraz")
    print("workspace ONCE at creation (flat, no history). To replay a fresh")
    print("snapshot after external changes, use `alcatrazer clear` + `start`.")


def launch_daemon_and_print(project_dir: Path) -> DaemonLaunchInfo | None:
    """Convenience: launch + print, swallowing launch failures into a
    stderr warning. Used by cmd_start — a failed daemon launch must
    NOT fail `alcatrazer start`, since the Alcatraz itself is up and
    the user can retry via `stop` + `start` or investigate the log."""
    try:
        info = launch_sync_daemon(project_dir)
    except DaemonLaunchError as e:
        print(f"WARNING: {e}", file=sys.stderr)
        return None
    print_launch_info(info, project_dir)
    return info


# ── Shutdown ───────────────────────────────────────────────────────────

# Regexes against the daemon's own log strings (see alcatrazer.daemon's
# run_cycle + finally block). Tightly coupled; if the daemon's log format
# changes, both must update in lockstep.
_SYNCED_RE = re.compile(r"Final sync \([^)]+\): (\d+) commit\(s\) synced")
_CONFLICT_RE = re.compile(r"CONFLICT on branch (\S+)")
_FAILED_RE = re.compile(r"Final sync \([^)]+\) failed:")


@dataclass(frozen=True)
class ShutdownResult:
    """What `shutdown_sync_daemon` reports to the CLI for the user-facing
    shutdown block.

    `outcome` values:
    - `"no_daemon"` — no PID file / no live daemon when called.
    - `"synced"` — daemon exited cleanly, final sync completed (possibly
      synced zero commits — still "synced").
    - `"conflict"` — daemon exited but a conflict was logged during the
      shutdown cycle. `conflict_branches` lists them.
    - `"failed"` — daemon logged a final-sync failure (exception).
    - `"timeout"` — SIGTERM didn't stop the daemon within the timeout;
      SIGKILL was sent. Log parsing still attempted, but the outcome
      flag signals to the CLI that forcible termination happened.
    """

    outcome: str
    synced_count: int
    conflict_branches: list[str]


def _parse_shutdown_log(text: str) -> tuple[int, list[str], bool]:
    """Extract (synced_count, conflict_branches, had_failure) from a log
    tail. Takes the last `Final sync` line for synced count; collects
    distinct CONFLICT branches across the whole tail."""
    synced = 0
    for match in _SYNCED_RE.finditer(text):
        synced = int(match.group(1))  # keep the last match
    conflicts: list[str] = []
    seen: set[str] = set()
    for match in _CONFLICT_RE.finditer(text):
        branch = match.group(1)
        if branch not in seen:
            seen.add(branch)
            conflicts.append(branch)
    failed = bool(_FAILED_RE.search(text))
    return synced, conflicts, failed


def _pid_from_file(pid_file: Path) -> int | None:
    """Read and parse the daemon's PID file, returning None on any error.

    Does NOT check liveness; caller decides how to handle a still-present
    file (for shutdown, we SIGTERM and wait; for launch, we check via
    `os.kill(pid, 0)`)."""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """Poll until process `pid` is gone, up to `timeout` seconds.

    Handles two parent-child scenarios:
    - Production: the CLI process spawned the daemon, then exited; the
      daemon is re-parented to init. `os.kill(pid, 0)` returns
      `ProcessLookupError` cleanly when the daemon exits.
    - Test / same-process: the CLI-under-test IS the daemon's direct
      parent, so an exited daemon becomes a zombie and
      `os.kill(pid, 0)` keeps returning success until it's reaped.
      `os.waitpid(pid, os.WNOHANG)` reaps any zombie that's ours and
      raises `ChildProcessError` when the pid isn't our child (which
      is the production case).

    Returns True if the process exited (by either mechanism), False if
    still alive at deadline (caller falls back to SIGKILL).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # Reap a zombie if this process was the spawner (common in tests).
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return True
        except ChildProcessError:
            # Not our child — normal in production (daemon re-parented
            # to init after the spawning CLI exited).
            pass
        time.sleep(0.1)
    return False


def shutdown_sync_daemon(
    project_dir: Path,
    *,
    timeout: float = 10.0,
) -> ShutdownResult:
    """Cooperative daemon shutdown — four-step protocol per plan Step 5.7d:

    1. Flip `.alcatrazer/state.json` `daemon_shutdown` to `"requested"`
       so the daemon's shutdown handler sees graceful intent.
    2. Read PID, send SIGTERM, poll until exit (SIGKILL on timeout).
    3. Tail the daemon log, parse the final-sync outcome.
    4. Flip state.json to `"done"` — closes the cooperation cycle so a
       future `kill <daemon-pid>` from outside the CLI produces an
       "unexpected" log prefix rather than carrying a stale "requested".

    Steps 1 and 4 happen even when no daemon is running — a stale
    `"requested"` from a crashed prior cycle gets cleaned up.
    """
    alcatraz_dir = (project_dir / ".alcatrazer").resolve()
    pid_file = alcatraz_dir / "promotion-daemon.pid"
    log_file = alcatraz_dir / "promotion-daemon.log"

    # Step 1 — flip intent before signaling.
    state.update_state(alcatraz_dir, daemon_shutdown="requested")

    pid = _pid_from_file(pid_file)
    if pid is None:
        state.update_state(alcatraz_dir, daemon_shutdown="done")
        return ShutdownResult(outcome="no_daemon", synced_count=0, conflict_branches=[])

    # Step 2 — signal and wait.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Race: process died between our PID read and signal. Treat as
        # "no daemon" — the work we were going to ask for is already
        # not happening.
        state.update_state(alcatraz_dir, daemon_shutdown="done")
        return ShutdownResult(outcome="no_daemon", synced_count=0, conflict_branches=[])

    exited_cleanly = _wait_for_exit(pid, timeout)
    timed_out = False
    if not exited_cleanly:
        # Safety valve — a wedged daemon shouldn't block the CLI.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        _wait_for_exit(pid, timeout=5.0)
        timed_out = True

    # Step 3 — parse log tail.
    log_text = ""
    with contextlib.suppress(OSError):
        log_text = log_file.read_text()
    # Keep the last ~4KB — plenty for the shutdown section, bounded so
    # a years-old log doesn't balloon.
    log_tail = log_text[-4096:] if len(log_text) > 4096 else log_text
    synced_count, conflict_branches, had_failure = _parse_shutdown_log(log_tail)

    # Step 4 — close the cycle.
    state.update_state(alcatraz_dir, daemon_shutdown="done")

    if timed_out:
        outcome = "timeout"
    elif had_failure:
        outcome = "failed"
    elif conflict_branches:
        outcome = "conflict"
    else:
        outcome = "synced"
    return ShutdownResult(
        outcome=outcome,
        synced_count=synced_count,
        conflict_branches=conflict_branches,
    )


def print_shutdown_result(result: ShutdownResult) -> None:
    """User-facing output for the sync-daemon shutdown block.
    Called by cmd_stop / cmd_clear after `shutdown_sync_daemon`
    returns — picks a message per the `outcome` field.

    `no_daemon` is silent — no sync daemon was running, so there's
    nothing to tell the user. Every other outcome emits a
    `Sync daemon stopped.` line as a closing marker."""
    if result.outcome == "no_daemon":
        return
    if result.outcome == "synced":
        print(f"Sync daemon: synced {result.synced_count} commit(s) before exit.")
    elif result.outcome == "conflict":
        branches = ", ".join(result.conflict_branches)
        print(f"Sync daemon: synced {result.synced_count} commit(s); CONFLICT on {branches}.")
        print("  Unsynced commits remain in the Alcatraz workspace.")
        print("  Resolve in your repository, then run `alcatrazer start`")
        print("  to resume syncing (paused branches auto-resolve).")
    elif result.outcome == "failed":
        print("Sync daemon: final sync FAILED. See .alcatrazer/promotion-daemon.log for details.")
    elif result.outcome == "timeout":
        print("Sync daemon: did not shut down within the timeout — SIGKILL sent.")
    print("Sync daemon stopped.")
