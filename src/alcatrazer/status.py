"""Status surface for an Alcatrazer workspace.

Backs the `alcatrazer status` CLI command. Reads `.alcatrazer/state.json`
to render one of three steady states (active / held / paused) plus the
pending-commit count and last-sync time, and prints a one-line pointer
to `tail -f .alcatrazer/promotion-daemon.log` for the user who wants
the full event stream.

The module is named `status` (renamed from the original `inspect` in
Phase 3) because the original name shadowed Python's stdlib `inspect`
module whenever any code in `src/alcatrazer/` ran as a script. That
shadow broke any code path that hit `dataclasses.@dataclass`, which
internally calls `inspect.get_annotations`. Renaming the file removes
the shadow permanently.
"""

import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

from alcatrazer import identity, promote, snapshot, state


def _read_daemon_pid(pid_file: Path) -> int | None:
    """Return the daemon's PID iff the pid_file exists, parses as an
    int, AND that process is alive. Otherwise return None.

    Liveness is checked via `os.kill(pid, 0)` (signal 0 — no-op).
    The PermissionError branch returns the PID anyway: a process we
    can't signal is still alive; we just can't poke it.
    """
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid


def count_pending_commits(workspace: Path, last_promoted: str | None) -> int:
    """Count commits in workspace's main branch that are NOT yet
    reachable from `last_promoted`. Used by `alcatrazer status` to
    render the "Pending commits: N" line, and by `cmd_clear` to
    decide whether the four-case block applies.

    Falls back to 0 when:
      - the workspace doesn't exist or isn't a git repo
      - last_promoted is None (nothing to compare against — caller
        should pass inner_root if they have nothing better)
      - git rev-list errors for any reason
    """
    if last_promoted is None:
        return 0
    if not (workspace / ".git").exists():
        return 0
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-list", "--count", f"{last_promoted}..HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return 0


def _format_relative_time(iso_time: str | None) -> str:
    """Convert an ISO 8601 timestamp into a user-friendly relative
    time ("2 minutes ago", "3 days ago"). Returns "never" when
    iso_time is None / empty. Stdlib-only — no third-party humanize."""
    if not iso_time:
        return "never"
    try:
        then = datetime.fromisoformat(iso_time)
    except (ValueError, TypeError):
        return "unknown"
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - then
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''} ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _render_explanation_lines(message: str, indent: str = " " * 20, width: int = 58) -> list[str]:
    """Wrap a multi-sentence explanation into indented lines so it
    fits cleanly under the `Started from:` label."""
    return [indent + line for line in textwrap.wrap(message, width=width)]


def cmd_status(project_dir: Path) -> int:
    """Render the Alcatrazer sync daemon's current status.

    Reads `.alcatrazer/state.json` (single read; no new files per the
    Phase 5 Step 5.4 spec) plus the daemon's PID file, queries the
    outer repo for its current branch + pending commit count, and
    prints a block describing one of three states:

      - **active**: daemon running, repo on the started-on branch,
        no conflict in flight.
      - **on hold**: daemon running, repo on a different branch /
        detached HEAD / branch deleted — apply paused until you
        switch back.
      - **paused**: daemon running, last apply hit a working-tree
        conflict.

    When no daemon is running, prints a single line directing the
    user to `alcatrazer start`. Returns 0 unconditionally — `status`
    is a read-only diagnostic, never an error itself.

    Output strings use git vocabulary (branch, repository, working
    tree, conflict, commit, stash) per
    docs/coding_conventions.md "User-facing strings speak the user's
    language" — never project jargon (pin, promotion, outer, inner).

    Per change_promotion_machinery.md Phase 5 Step 5.4 (L934-935).
    """
    alcatraz_dir = project_dir / ".alcatrazer"
    pid_file = alcatraz_dir / "promotion-daemon.pid"

    # Phase 7 (change_promotion_machinery.md, Step 7.3): refuse
    # pre-v0.1.1 workspaces before reading state. Surfaces the upgrade
    # message and returns 1 so a script invoking `alcatrazer status`
    # can detect the incompat via exit code.
    try:
        state.require_compatible_workspace(alcatraz_dir)
    except state.UnsupportedStateSchemaVersionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # 1. Daemon alive?
    pid = _read_daemon_pid(pid_file)
    if pid is None:
        print("No sync daemon running. Run `alcatrazer start` to bring up the workspace.")
        return 0

    # 2. Read state (single load) — Phase 5 spec, no new state files.
    state_data = state.load_state(alcatraz_dir)
    pinned_branch = state_data.get("pinned_branch", "<unknown>")
    last_promoted = state_data.get("last_promoted")
    last_promotion_time = state_data.get("last_promotion_time")
    paused = state_data.get("paused")

    # 3. Determine state (priority: paused > held > active).
    explanation: str | None = None
    if paused:
        marker = "⚠ paused"
        explanation = (
            f"Your working tree on '{pinned_branch}' overlaps with an "
            f"agent commit. Commit or stash your changes and Alcatrazer "
            f"will resume."
        )
    else:
        pin = promote.check_pin(project_dir, pinned_branch)
        if pin is promote.PinStatus.OK:
            marker = "✓ active"
        else:
            marker = "⚠ on hold"
            if pin is promote.PinStatus.DETACHED:
                explanation = (
                    f"Your repository has a detached HEAD. "
                    f"Check out branch '{pinned_branch}' "
                    f"(`git checkout {pinned_branch}`) to resume."
                )
            elif pin is promote.PinStatus.PIN_DELETED:
                explanation = (
                    f"Branch '{pinned_branch}' no longer exists in your "
                    f"repository. Recreate it "
                    f"(`git branch {pinned_branch}`) to resume."
                )
            else:  # OFF_PIN
                current = snapshot.current_branch(str(project_dir)) or "<unknown>"
                explanation = (
                    f"Your repository is on branch '{current}' but "
                    f"Alcatrazer was started on '{pinned_branch}'. "
                    f"Switch back to '{pinned_branch}' "
                    f"(`git checkout {pinned_branch}`) to resume."
                )

    # 4. Pending count.
    workspace_name = identity.load_workspace_dir(str(alcatraz_dir))
    pending = 0
    if workspace_name:
        pending = count_pending_commits(project_dir / workspace_name, last_promoted)

    # 5. Last sync as relative time.
    last_sync = _format_relative_time(last_promotion_time)

    # 6. Render.
    print(f"Sync daemon running (PID {pid}).")
    print(f"  Started from:     '{pinned_branch}'  {marker}")
    if explanation:
        for line in _render_explanation_lines(explanation):
            print(line)
    print(f"  Pending commits:  {pending}")
    print(f"  Last sync:        {last_sync}")
    print()
    log_path = alcatraz_dir.relative_to(project_dir) / "promotion-daemon.log"
    print(f"Sync daemon logs live view:  tail -f {log_path}")
    return 0
