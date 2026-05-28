"""Single funnel for every host-side git invocation in this codebase.

Every host-side `git` call should go through `run_git_command`. The
function autodetects which UID-safety setup to use, so a new contributor
neither has to know the difference nor remember to pick the right one —
they just write the git argv as they would on the shell and the funnel
routes correctly.

Two host execution models:

* Host process, host-owned repo (the user's outer git repo) — plain
  `git -C <repo> …`.
* Host process, the inner Alcatraz workspace — `git -C <repo>
  -c safe.directory=<repo> …`. The workspace dir is owned by the
  container's agent UID; on any host where that UID differs from the
  host user's, Git 2.35+ refuses to operate on it with "dubious
  ownership". The override bypasses that. Without it every read silently
  fails with non-zero exit and the caller sees an empty/zero result
  indistinguishable from a legitimate one (the `alcatrazer status`
  Pending=0 bug that motivated this funnel).

How the dispatch is decided: `run_git_command(args)` looks for `-C <path>`
in `args` and treats `<path>` as the target. It reads
`.alcatrazer/workspace-dir` (in `<path>.parent`) and compares its content
against `<path>.name`. Match → workspace → prepend
`-c safe.directory=<path>`. No match → ordinary host repo, plain git.

Why this lookup is NOT cached: the workspace-dir file is the single piece
of state the safety dispatch hinges on. Caching it introduces desync risk
in scenarios where the workspace is removed/recreated underneath us —
`alcatrazer clear` (wipes the workspace), a future quick switch/recreate
flow, or a future multi-prison project layout. A stale cache could miss
the workspace case, skip the `safe.directory` override, and put us back
in the silent-Pending-0 bug class this funnel exists to prevent. The
per-call cost is one `stat` + a tiny `read_text` — microseconds against
a git subprocess that takes milliseconds-to-hundreds-of-milliseconds.
Negligible compared to what it gates; not worth a class of bugs.

Convention: `args` is the git argv MINUS the literal `git` (so it always
starts with `-C <repo>` followed by the subcommand). This is the same
shape every existing wrapper in the codebase (`promote.py:git`,
`snapshot.py:_git`, `start.py:_wgit`) builds internally — keeping the
single source of truth for `<repo>` inside `args` (no duplicate `repo=`
parameter to keep in sync with `-C`).

Why a funnel that ENFORCES rather than asks: the obvious "single utility
with a `context=` tag" design just shifts the bug — instead of forgetting
`safe.directory`, callers forget to pass the right tag, and silent
Pending=0 (or worse) returns. Autodetecting from the `-C` target means
the caller can't pick wrong; they just write `git -C <repo> …` as they
would in the shell, and the funnel does the safety work.

What is deliberately NOT in scope here:

* `prison.query(["git", …])` calls (e.g. in selftest.py) — `prison.query`
  is the general "run any command inside the container" port; routing
  its git invocations through this funnel would break its universality
  and add nothing (the prison port handles UID setup internally via
  `docker exec` running as the agent user). Leave those calls on the
  port directly.
* `git init <path>` for workspace creation — runs before the container
  chowns the workspace; no UID-safety concern, and the no-`-C` argv
  shape doesn't fit this funnel anyway. One call site in `start.py`,
  commented to point back here.
* `git config --global …` reads of the user's ambient gitconfig — no
  repo target at all. One call site in `start.py`, commented to point
  back here.
"""

import subprocess
from pathlib import Path


def _is_workspace_path(target: Path) -> bool:
    """True if `target` is the inner Alcatraz workspace of the project it
    sits in. Reads `.alcatrazer/workspace-dir` in `target.parent` and
    compares its content to `target.name`. Per-call (not cached) — see
    module docstring."""
    wd_file = target.parent / ".alcatrazer" / "workspace-dir"
    if not wd_file.exists():
        return False
    return wd_file.read_text().strip() == target.name


def _find_C_target(args: list[str]) -> Path:
    """Extract the `-C <path>` argument from `args` — the repo the git
    command operates on. Raises if absent (the funnel relies on it for
    UID-safety autodetect)."""
    for i, arg in enumerate(args):
        if arg == "-C" and i + 1 < len(args):
            return Path(args[i + 1])
    raise ValueError(
        "run_git_command needs `-C <repo>` in args so the funnel can "
        f"autodetect UID-safety; got: {args!r}"
    )


def run_git_command(
    args: list[str],
    *,
    check: bool = False,
    text: bool = True,
    input: bytes | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a host-side git command, auto-routing UID-safety from the
    `-C <repo>` argument in `args`. See module docstring for the why.

    `args` is the git argv minus the literal `git` — so the typical shape
    is `["-C", str(repo), "<subcommand>", …]`. The kwargs forward to
    subprocess.run: `input=` pipes bytes/text into stdin (used by
    `git am` for patch streams), `env=` overrides the environment (used
    by `git am` to set GIT_COMMITTER_NAME/EMAIL), `text=False` for binary
    output (used by `git archive`), `check=True` to raise on non-zero
    exit.
    """
    repo = _find_C_target(args)
    cmd = ["git"]
    if _is_workspace_path(repo):
        cmd.extend(["-c", f"safe.directory={repo}"])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=text, check=check, input=input, env=env)
