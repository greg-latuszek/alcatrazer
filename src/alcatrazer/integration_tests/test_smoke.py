"""Docker smoke tests for Alcatrazer — CI path.

Drives `alcatrazer.start.cmd_init` + `cmd_start` against a fresh temp
project (with the interactive wizards stubbed), then runs three tiers of
invariants against the resulting running Alcatraz:

- Security invariants  — from `alcatrazer.selftest._AlcatrazSecurityInvariants`;
  also what `alcatrazer start --run-selftest` runs against the user's live
  install. Read-only probes only.
- Tooling availability — smoke-only mixin here. Verifies
  ai-base + dev layer tooling (python / node / mise / claude / mise-managed
  runtimes) exists and responds to --version.
- Workflow invariants  — smoke-only mixin here. Genuinely-intrusive
  checks (branch + merge, code execution) that need write state, so they
  run in a scratch /tmp location with per-test addCleanup — /tmp inside
  the container is NOT bind-mounted to the host, so these stay isolated.

The tier split + non-intrusiveness discipline is documented in
install_method.md under "Three-tier test organization" and "Non-
intrusiveness discipline for selftest".

Mixin pattern — the three `_Alcatraz*Invariants` classes are plain
classes, NOT `unittest.TestCase` subclasses. They rely on their concrete
subclass (`TestAlcatrazSmokeCI` here) to bring in `unittest.TestCase`
via MRO. This prevents `unittest discover` from running them standalone
(where `cls.prison` is None) on either side of the import boundary.

Requires Docker; skipped from the default `alcatrazer test`. Run with
`alcatrazer test --smoke`.
"""

import contextlib
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import start as start_mod
from alcatrazer.alcatraz import Alcatraz
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.selftest import _AlcatrazSecurityInvariants

# ── How CODING_ENV plumbs down to the running container ─────────────
#
# Dev-base (git, mise, gosu, the `agent` user) and ai-base (claude) are
# hardcoded in the Dockerfile constants in docker_prison.py — nothing
# surprising about testing those.
#
# What IS non-trivial: how the `[languages.*]` declaration below ends
# up as working binaries inside the container, and how the two
# interactive wizards get stubbed out so CI never blocks on stdin.
#
#   CODING_ENV = {languages: {python, node}}
#        │   (ask_coding_environment patched → returns this)
#        ▼
#   cmd_init(project_dir)
#        │
#        └─ prison.generate_prison(coding_env)
#              └─ .alcatrazer/Dockerfile Stage 3 `dev` ends up with
#                   RUN mise use --global python@3.12 && \
#                       mise use --global node@22
#
#   cmd_start(project_dir)      (image not built yet → first-run branch)
#        │
#        ├─ prison.build()     docker build runs it → mise installs the
#        │                     runtimes + puts shims on PATH (dev-base
#        │                     sets the PATH env to include the shims)
#        │
#        └─ prison.start()     container up; node/python resolve via
#                              the mise shims
#
# Then tests reach into it:
#     prison.query(["node", "--version"])
#   → docker exec -u agent workspace node --version
#   → shim → Node 22 → exit 0, stdout "v22.x.y"
#
# ask_promotion_identity is patched the same way — returns a fixed
# Ghost Agent rather than prompting for name/email.

CODING_ENV = {
    "languages": {
        "python": {"version": "3.12"},
        "node": {"version": "22"},
    },
}


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _seed_project(project_dir: Path) -> None:
    """Bootstrap a minimal outer git repo in the tempdir."""
    g = ["git", "-C", str(project_dir)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(project_dir)], check=True)
    subprocess.run([*g, "config", "user.name", "CI Bootstrap"], check=True)
    subprocess.run([*g, "config", "user.email", "ci@example.com"], check=True)
    (project_dir / "README.md").write_text("# smoke test project\n")
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "initial"], check=True)


def _nuke_phantom_uid_files(path: Path) -> None:
    """Remove files owned by the phantom UID via a disposable alpine.

    The container's entrypoint `chown -R agent:agent /workspace`
    propagates through the bind mount, so files inside the workspace dir
    end up owned by the phantom UID on the host. The CI user (non-root)
    cannot unlink them directly. Alpine runs as root inside its own
    container, so it can delete anything on the mounted target. Mirrors
    the pattern the old init.py's handle_reset used.
    """
    # Best-effort — if docker is gone or the mount fails, let Python's
    # rmtree try; it may succeed for CI-owned entries.
    with contextlib.suppress(Exception):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{path}:/target",
                "alpine:3",
                "sh",
                "-c",
                "find /target -mindepth 1 -delete",
            ],
            capture_output=True,
            timeout=60,
        )


# ── Smoke-only mixins: tooling + workflow (plain classes, see docstring) ──


class _AlcatrazToolingInvariants:
    """ai-base + dev layer tooling sanity — not security, just "did the image
    build actually produce working binaries for what we declared?" Smoke-
    test only; `--run-selftest` deliberately skips this tier.

    Plain class (not TestCase). See module docstring for the rationale."""

    prison: Alcatraz = None  # type: ignore[assignment]

    def test_python_responds_to_version(self):
        result = self.prison.query(["python", "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.strip() or result.stderr.strip())

    def test_node_responds_to_version(self):
        result = self.prison.query(["node", "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.strip())

    def test_git_responds_to_version(self):
        result = self.prison.query(["git", "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("git", result.stdout.lower())

    def test_mise_responds_to_version(self):
        result = self.prison.query(["mise", "--version"])
        self.assertEqual(result.returncode, 0)

    def test_claude_responds_to_version(self):
        result = self.prison.query(["claude", "--version"])
        self.assertEqual(result.returncode, 0)

    def test_mise_manages_python(self):
        result = self.prison.query(["mise", "ls"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("python", result.stdout)

    def test_mise_manages_node(self):
        result = self.prison.query(["mise", "ls"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("node", result.stdout)


class _AlcatrazWorkflowInvariants:
    """Intrusive workflow checks — commits, branches, code execution. Each
    test that needs write state owns its own scratch area in /tmp INSIDE
    the container (NOT bind-mounted to the host, so these tests never
    leak state into the user's /workspace). Cleanup registered per-test
    via `self.addCleanup` rather than `setUp`/`tearDown` so the cost
    stays scoped to tests that actually need it.

    Smoke-test only; `--run-selftest` deliberately skips this tier
    because these probes write. Plain class (not TestCase) for the same
    reason as the tooling mixin."""

    prison: Alcatraz = None  # type: ignore[assignment]

    SCRATCH_REPO = "/tmp/alcatraz-workflow-scratch-repo"
    SCRATCH_PY = "/tmp/alcatraz-workflow-scratch.py"

    def _setup_scratch_repo(self) -> None:
        """Create a fresh git repo at SCRATCH_REPO; cleanup auto-registered."""
        self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    rm -rf {self.SCRATCH_REPO} &&
                    git init -q -b main {self.SCRATCH_REPO} &&
                    cd {self.SCRATCH_REPO} &&
                    git config --local user.name "workflow-test" &&
                    git config --local user.email "workflow@test.local" &&
                    echo initial > a &&
                    git add a &&
                    git commit -qm initial
                """,
            ]
        )
        self.addCleanup(self.prison.query, ["rm", "-rf", self.SCRATCH_REPO])

    def test_branching_and_merging_works(self):
        self._setup_scratch_repo()
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    cd {self.SCRATCH_REPO} &&
                    git checkout -qb feature &&
                    echo feature-change > b &&
                    git add b &&
                    git commit -qm feature &&
                    git checkout -q main &&
                    git merge --no-edit feature
                """,
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_new_commit_records_its_author_identity(self):
        """Sanity: commits honor the local user.name/user.email. Uses the
        scratch repo's intentionally-set workflow-test identity; no
        coupling to the workspace's agent identity."""
        self._setup_scratch_repo()
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    cd {self.SCRATCH_REPO} &&
                    echo more > c &&
                    git add c &&
                    git commit -qm more &&
                    git log -1 --format='%an|%ae'
                """,
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "workflow-test|workflow@test.local")

    def test_python_can_execute_a_file(self):
        self.addCleanup(self.prison.query, ["rm", "-f", self.SCRATCH_PY])
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    echo 'print("hello from python")' > {self.SCRATCH_PY} &&
                    python {self.SCRATCH_PY}
                """,
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello from python", result.stdout)

    def test_node_can_execute_inline_code(self):
        # No scratch — `node -e` runs inline without a file.
        result = self.prison.query(["node", "-e", "console.log('hello from node')"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello from node", result.stdout)


# ── Three-tier CI smoke test ──────────────────────────────────────────


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestAlcatrazSmokeCI(
    _AlcatrazSecurityInvariants,
    _AlcatrazToolingInvariants,
    _AlcatrazWorkflowInvariants,
    unittest.TestCase,
):
    """Full end-to-end CI path: bring up a fresh Alcatraz, run all three
    tiers against it, tear down.

    Inheritance order: mixins first (for method resolution on plain
    classes) then `unittest.TestCase` last so `self.assertXxx` and
    `self.addCleanup` resolve correctly via MRO."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Ghost Agent", "ghost@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod.cmd_init(cls.project_dir)
        if rc != 0:
            raise RuntimeError(f"alcatrazer init failed (rc={rc})")

        cls.prison = DockerPrison(cls.project_dir)
        rc = start_mod.cmd_start(cls.project_dir, prison=cls.prison)
        if rc != 0:
            raise RuntimeError(f"alcatrazer start failed (rc={rc})")

        alcatraz_dir = cls.project_dir / ".alcatrazer"
        identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
        cls.expected = {
            "uid": (alcatraz_dir / "uid").read_text().strip(),
            "name": identity_lines[0],
            "email": identity_lines[1],
        }

    @classmethod
    def tearDownClass(cls):
        with contextlib.suppress(Exception):
            cls.prison.stop()
        with contextlib.suppress(Exception):
            cls.prison.remove()
        # Nuke phantom-UID-owned files before Python's rmtree — the
        # workspace's chown at entrypoint time propagated through the
        # bind mount, so the CI user can't unlink them directly.
        _nuke_phantom_uid_files(cls.project_dir)
        with contextlib.suppress(Exception):
            cls._tmp.cleanup()


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestAlcatrazSmokeLifecycle(unittest.TestCase):
    """Step 5.7h — end-to-end sync-daemon lifecycle smoke.

    Single test method by design: the phases are stateful (each
    depends on the prior phase's effect on docker / daemon / inner
    repo), and on failure the line number tells you exactly which
    phase broke the chain. Splitting into independent test methods
    would mean each one reconstructs the preceding phase, slow and
    noisy.

    Phases:
      1. `cmd_start` (first run) — docker up, workspace snapshotted,
         daemon launched.
      2. Commit inside the Alcatraz workspace via `docker exec` —
         verify it propagates to the outer repo within a few daemon
         polls (end-to-end sync works).
      3. `cmd_stop` — daemon finalizes, docker stopped, writable
         layer AND Alcatraz workspace preserved on host.
      4. `cmd_start` (resume) — container resumed, daemon relaunched
         (daemon process doesn't survive stop; writable layer does).
      5. `cmd_clear` — daemon finalizes, docker rm'd, Alcatraz
         workspace STILL present on host but fully WIPED OUT.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        # Drive init only — start is exercised inside the test method so
        # each phase's effect is observable in sequence.
        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Ghost Agent", "ghost@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod.cmd_init(cls.project_dir)
        if rc != 0:
            raise RuntimeError(f"cmd_init failed (rc={rc})")

        cls.alcatraz_dir = cls.project_dir / ".alcatrazer"

        # Speed up daemon polling for the sync-latency assertion
        # (default is 5s; 1s keeps the test under ~15s total).
        config_path = cls.alcatraz_dir / "config.toml"
        config_path.write_text(config_path.read_text().replace("interval = 5", "interval = 1"))

        cls.workspace_name = (cls.alcatraz_dir / "workspace-dir").read_text().strip()
        cls.workspace = cls.project_dir / cls.workspace_name
        cls.prison = DockerPrison(cls.project_dir)

    @classmethod
    def tearDownClass(cls):
        # Defense-in-depth: the test's final phase (clear) should have
        # killed the daemon already, but a mid-test failure could leak
        # it. Reap any straggler before the container teardown.
        pid_file = cls.alcatraz_dir / "promotion-daemon.pid"
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError, OSError):
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, os.WNOHANG)
        with contextlib.suppress(Exception):
            cls.prison.stop()
        with contextlib.suppress(Exception):
            cls.prison.remove()
        _nuke_phantom_uid_files(cls.project_dir)
        with contextlib.suppress(Exception):
            cls._tmp.cleanup()

    # ── Helpers ────────────────────────────────────────────────────────

    def _daemon_pid(self) -> int | None:
        pid_file = self.alcatraz_dir / "promotion-daemon.pid"
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _daemon_alive(self) -> bool:
        """True if the daemon's PID file points at a live process.

        Handles the test-process-is-parent-of-daemon case by reaping
        any zombie we encounter (same logic as daemon_lifecycle's
        _wait_for_exit — in production the daemon is init-owned and
        this branch never fires)."""
        pid = self._daemon_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False
        except ChildProcessError:
            pass
        return True

    def _outer_log_subjects(self) -> list[str]:
        r = subprocess.run(
            ["git", "-C", str(self.project_dir), "log", "--all", "--format=%s"],
            capture_output=True,
            text=True,
        )
        return r.stdout.strip().splitlines() if r.returncode == 0 else []

    def _commit_in_alcatraz_workspace(self, msg: str, filename: str) -> None:
        """Commit via `docker exec` as the agent user — matches how an
        AI agent inside the sandbox would create commits, so the test
        exercises the real promotion path."""
        r = self.prison.query(
            [
                "bash",
                "-c",
                f"cd /workspace && echo {msg!r} > {filename} && "
                f"git add {filename} && git commit -qm {msg!r}",
            ]
        )
        self.assertEqual(r.returncode, 0, f"Commit inside Alcatraz failed: {r.stderr}")

    def _wait_for_outer_commit(self, msg: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msg in self._outer_log_subjects():
                return True
            time.sleep(0.5)
        return False

    def _wait_for_daemon_gone(self, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._daemon_alive():
                return True
            time.sleep(0.2)
        return False

    # ── The lifecycle ──────────────────────────────────────────────────

    def test_full_lifecycle(self):
        # Phase 1 — first start: docker up, workspace snapshotted,
        # daemon spawned alongside.
        rc = start_mod.cmd_start(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_start (first run) should succeed")
        self.assertTrue(self.prison.is_running(), "Alcatraz should be running after start")
        self.assertTrue(self._daemon_alive(), "Sync daemon should be running after start")
        self.assertTrue(self.workspace.is_dir(), "Alcatraz workspace dir should exist")
        self.assertTrue((self.workspace / ".git").is_dir(), "Inner git repo should exist")

        # Phase 2 — commit inside the Alcatraz workspace and verify
        # the daemon promotes it to the outer repo (the whole point
        # of having a daemon).
        self._commit_in_alcatraz_workspace("lifecycle smoke: phase 2 commit", "phase2.txt")
        self.assertTrue(
            self._wait_for_outer_commit("lifecycle smoke: phase 2 commit"),
            "Commit made inside the Alcatraz workspace did not reach the outer "
            "repo within the timeout — daemon sync pipeline is broken",
        )

        # Phase 3 — stop. Daemon finalizes (final sync against the
        # now-frozen inner repo), container stops, inner repo STAYS
        # on the host.
        rc = start_mod.cmd_stop(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_stop should succeed")
        self.assertTrue(
            self._wait_for_daemon_gone(),
            "Sync daemon should exit cleanly after stop",
        )
        self.assertFalse(self.prison.is_running(), "Alcatraz should be stopped")
        self.assertTrue(
            self.workspace.is_dir(),
            "Alcatraz workspace must survive stop (host-side preservation)",
        )
        self.assertTrue((self.workspace / ".git").is_dir())

        # Phase 4 — resume. Container comes back with writable layer
        # intact; daemon is relaunched (it didn't survive stop).
        rc = start_mod.cmd_start(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_start (resume) should succeed")
        self.assertTrue(self.prison.is_running(), "Alcatraz should be running after resume")
        self.assertTrue(self._daemon_alive(), "Sync daemon should be running again after resume")

        # Phase 5 — clear. Daemon finalizes, container is removed
        # entirely, Alcatraz workspace present on host but WIPED
        # (workspace no more needed since all commits promoted into outer repo)
        # (workspace wiped and chowned to host UID:GID to allow for fresh workspace snapshot).
        rc = start_mod.cmd_clear(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0, "cmd_clear should succeed")
        self.assertTrue(
            self._wait_for_daemon_gone(),
            "Sync daemon should exit cleanly after clear",
        )
        self.assertFalse(self.prison.exists(), "Alcatraz should be gone after clear")
        # THE design guarantee: clear throws the runtime away, keeps
        # the work (but only inside outer repo new commits). Inner repo is gone.
        self.assertTrue(
            self.workspace.is_dir(),
            "Alcatraz workspace MUST survive clear",
        )
        self.assertFalse(
            (self.workspace / ".git").is_dir(),
            "Inner git repo MUST BE GONE after clear",
        )
        self.assertEqual(
            list(self.workspace.iterdir()),
            [],
            "after clear, inner workspace contents must be wiped",
        )


if __name__ == "__main__":
    unittest.main()
