"""Single funnel for every host-side git invocation in this codebase.

Every host-side `git` call should go through `run_git_command`. The
function unconditionally prepends `-c safe.directory='*'` so git never
refuses on "dubious ownership" — the inner Alcatraz workspace is owned
by the container's agent UID and on any host where that UID differs from
the host user's, Git 2.35+ would otherwise refuse the read with non-zero
exit, and callers would see an empty/zero result indistinguishable from
a legitimate one (the `alcatrazer status` Pending=0 bug that motivated
this funnel).

The wildcard is fine here because:

* It applies only to this invocation (via `-c`, not config) — no
  persistent change to ~/.gitconfig or any other state.
* Every caller is our own code passing known paths; the funnel is never
  fed user-controlled input. The dubious-ownership check exists to
  protect against operating on attacker-controlled repos, which is not
  the threat model inside our process.

Why this beats "autodetect whether the path is the inner workspace and
add safe.directory only then": the autodetect needs to walk up looking
for `.alcatrazer/workspace-dir` (per-call I/O, or a cache that can go
stale on `alcatrazer clear` / recreate / multi-workspace flows), and it
constrains every caller to write `-C <repo>` so the funnel has a target
to compare. The always-on `safe.directory='*'` removes the autodetect
entirely — callers write git argv exactly as they would on the shell
(`["init", "-b", "main", path]`, `["clone", remote, local]`,
`["-C", repo, "rev-parse", "HEAD"]`, …) and the funnel does its one
job (UID-safety bypass) for every shape.

What is deliberately NOT in scope here:

* `prison.query(["git", …])` calls (e.g. in selftest.py) — `prison.query`
  is the general "run any command inside the container" port; routing
  its git invocations through this funnel would break its universality
  and add nothing (the prison port handles UID setup internally via
  `docker exec` running as the agent user). Leave those calls on the
  port directly.
"""

import subprocess


def run_git_command(
    args: list[str],
    *,
    check: bool = False,
    text: bool = True,
    input: bytes | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a host-side git command, bypassing dubious-ownership for this
    invocation via `-c safe.directory='*'`. See module docstring.

    `args` is the git argv minus the literal `git` — the funnel emits
    `["git", "-c", "safe.directory=*", *args]`. Kwargs forward to
    subprocess.run: `input=` pipes bytes/text into stdin (used by
    `git am` for patch streams), `env=` overrides the environment (used
    by `git am` to set GIT_COMMITTER_NAME/EMAIL), `text=False` for binary
    output (used by `git archive`), `check=True` to raise on non-zero
    exit.
    """
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        capture_output=True,
        text=text,
        check=check,
        input=input,
        env=env,
    )
