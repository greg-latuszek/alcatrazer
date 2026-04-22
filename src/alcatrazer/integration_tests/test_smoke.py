"""Docker smoke tests for Alcatrazer — CI path.

Drives `alcatrazer.start._first_time_setup` against a fresh temp project
(with the interactive wizards stubbed), then runs the shared security
invariants from `alcatrazer.selftest._AlcatrazSecurityInvariants` against
the resulting running Alcatraz.

The per-run install + teardown happens here (setUpClass / tearDownClass).
The actual assertion list lives in `alcatrazer.selftest` so that
`alcatrazer start --run-selftest` (cmd_selftest) reuses the exact same
invariants against an already-running Alcatraz — single source of truth
for "what must hold for a running Alcatraz to be trusted."

Requires Docker; skipped from the default `alcatrazer test`. Run with
`alcatrazer test --smoke`.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import start as start_mod
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.selftest import (
    SELFTEST_SCRIPT,
    _AlcatrazSecurityInvariants,
)

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


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestAlcatrazSmokeCI(_AlcatrazSecurityInvariants):
    """CI path: bring a fresh Alcatraz up end-to-end, run the shared
    security invariants against it, tear it down."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        # Drive the full first-time pipeline, with interactive prompts
        # stubbed so CI never blocks on stdin.
        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Ghost Agent", "ghost@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod._first_time_setup(cls.project_dir)
        if rc != 0:
            raise RuntimeError(f"alcatrazer first-time setup failed (rc={rc})")

        cls.prison = DockerPrison(cls.project_dir)
        cls.container_name = cls.prison.container_name

        alcatraz_dir = cls.project_dir / ".alcatrazer"
        identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
        cls.expected = {
            "uid": (alcatraz_dir / "uid").read_text().strip(),
            "name": identity_lines[0],
            "email": identity_lines[1],
        }

        result = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "agent",
                cls.container_name,
                "bash",
                "-c",
                SELFTEST_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        cls.output = result.stdout + result.stderr

    @classmethod
    def tearDownClass(cls):
        try:
            cls.prison.stop()
            cls.prison.remove()
        except Exception:
            pass
        cls._tmp.cleanup()


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestZeroAlcatrazFootprint(unittest.TestCase):
    """Grep for 'alcatraz' across env / git config / hostname / mountinfo.

    Shares its Alcatraz with TestAlcatrazSmokeCI above — setUpClass there
    is expected to have already booted the prison. Uses the canonical
    container name so the exec targets the same box.
    """

    @classmethod
    def setUpClass(cls):
        cls.prison = DockerPrison(Path("/"))  # project_dir unused for exec

    def _footprint_grep(self, command: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "agent",
                self.prison.container_name,
                "bash",
                "-c",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout + result.stderr

    def test_no_alcatraz_in_alcatraz(self):
        output = self._footprint_grep(
            "{ env; git config --global --list; "
            "git -C /workspace config --local --list; "
            "hostname; } "
            "| grep -i alcatraz || echo CLEAN"
        )
        self.assertIn(
            "CLEAN",
            output,
            f"Alcatraz footprint detected inside Alcatraz: {output}",
        )

    @unittest.skipIf(
        os.environ.get("CI") == "true",
        "Skipped in CI — host path contains repo name 'alcatrazer'",
    )
    def test_no_alcatraz_in_alcatraz_mount_points(self):
        output = self._footprint_grep("cat /proc/self/mountinfo | grep -i alcatraz || echo CLEAN")
        self.assertIn(
            "CLEAN",
            output,
            f"Alcatraz footprint in mount points: {output}",
        )


if __name__ == "__main__":
    unittest.main()
