"""Docker smoke tests for Alcatrazer — CI path.

Drives `alcatrazer.start._first_time_setup` against a fresh temp project
(with the interactive wizards stubbed), then runs three tiers of
invariants against the resulting running Alcatraz:

- Security invariants  — from `alcatrazer.selftest._AlcatrazSecurityInvariants`;
  also what `alcatrazer start --run-selftest` runs against the user's live
  install. Read-only probes only.
- Tooling availability — smoke-only mixin here. Verifies
  ai-base + dev layer tooling (python / node / mise / claude / mise-managed
  runtimes) exists and responds to --version.
- Workflow invariants  — smoke-only mixin here. Genuinely-intrusive
  checks (branch + merge, code execution) that need write state, so they
  run in a scratch /tmp git repo with per-test setUp / tearDown.

The tier split + non-intrusiveness discipline is documented in
install_method.md under "Three-tier test organization" and "Non-
intrusiveness discipline for selftest".

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
from alcatrazer.alcatraz import Alcatraz
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.selftest import _AlcatrazSecurityInvariants

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


# ── Smoke-only mixins: tooling + workflow ─────────────────────────────


class _AlcatrazToolingInvariants(unittest.TestCase):
    """ai-base + dev layer tooling sanity — not security, just "did the image
    build actually produce working binaries for what we declared?" Smoke-
    test only; `--run-selftest` deliberately skips this tier."""

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


class _AlcatrazWorkflowInvariants(unittest.TestCase):
    """Intrusive workflow checks — commits, branches, code execution. Each
    test uses a fresh scratch git repo at /tmp/alcatraz-workflow-scratch
    (INSIDE the container, NOT bind-mounted). Smoke-test only; `--run-
    selftest` deliberately skips this tier because these probes write."""

    prison: Alcatraz = None  # type: ignore[assignment]

    SCRATCH = "/tmp/alcatraz-workflow-scratch"

    def setUp(self):
        # Fresh scratch git repo before every workflow test.
        self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    rm -rf {self.SCRATCH} &&
                    git init -q -b main {self.SCRATCH} &&
                    cd {self.SCRATCH} &&
                    git config --local user.name "workflow-test" &&
                    git config --local user.email "workflow@test.local" &&
                    echo initial > a &&
                    git add a &&
                    git commit -qm initial
                """,
            ]
        )

    def tearDown(self):
        self.prison.query(["rm", "-rf", self.SCRATCH])

    def test_branching_and_merging_works(self):
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    cd {self.SCRATCH} &&
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
        """Sanity: commits honor the local user.name/user.email (not some
        mysterious system default). Uses the scratch repo's intentionally-
        set workflow-test identity; no coupling to the workspace's agent
        identity."""
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    cd {self.SCRATCH} &&
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
        result = self.prison.query(
            [
                "bash",
                "-c",
                f"""
                    echo 'print("hello from python")' > {self.SCRATCH}/hello.py &&
                    python {self.SCRATCH}/hello.py
                """,
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello from python", result.stdout)

    def test_node_can_execute_inline_code(self):
        result = self.prison.query(["node", "-e", "console.log('hello from node')"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello from node", result.stdout)


# ── Three-tier CI smoke test ──────────────────────────────────────────


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestAlcatrazSmokeCI(
    _AlcatrazSecurityInvariants,
    _AlcatrazToolingInvariants,
    _AlcatrazWorkflowInvariants,
):
    """Full end-to-end CI path: bring up a fresh Alcatraz, run all three
    tiers against it, tear down."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
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
            rc = start_mod._first_time_setup(cls.project_dir)
        if rc != 0:
            raise RuntimeError(f"alcatrazer first-time setup failed (rc={rc})")

        cls.prison = DockerPrison(cls.project_dir)

        alcatraz_dir = cls.project_dir / ".alcatrazer"
        identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
        cls.expected = {
            "uid": (alcatraz_dir / "uid").read_text().strip(),
            "name": identity_lines[0],
            "email": identity_lines[1],
        }

    @classmethod
    def tearDownClass(cls):
        try:
            cls.prison.stop()
            cls.prison.remove()
        except Exception:
            pass
        cls._tmp.cleanup()


# ── Orthogonal "zero-branding inside the Alcatraz" grep check ─────────


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestZeroAlcatrazFootprint(unittest.TestCase):
    """Grep for 'alcatraz' across env / git config / hostname / mountinfo.

    Shares its Alcatraz with TestAlcatrazSmokeCI above — setUpClass there
    is expected to have already booted the prison. Uses the canonical
    container name so the query targets the same box.
    """

    @classmethod
    def setUpClass(cls):
        cls.prison = DockerPrison(Path("/"))  # project_dir unused for query

    def _footprint_grep(self, command: str) -> str:
        result = self.prison.query(["bash", "-c", command])
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
