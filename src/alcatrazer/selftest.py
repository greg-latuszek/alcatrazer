"""Shared security-invariant assertions for any running Alcatraz sandbox.

Two callers consume this module:

- `integration_tests/test_smoke.py` — CI-mode smoke test. Its TestCase
  subclass creates a fresh tempdir project, drives `cmd_init` then
  `cmd_start` end-to-end, runs the assertions here plus its own tooling +
  workflow mixins, tears down.
- `alcatrazer.start.cmd_selftest` — `alcatrazer start --run-selftest`
  post-boot check on the user's live project. Uses
  `make_alcatraz_selftest_testcase(project_dir)` to build a TestCase
  bound to the already-running Alcatraz, then runs these same
  assertions against it.

`_AlcatrazSecurityInvariants` is therefore the single source of truth
for "what must hold for a running Alcatraz to be trusted." Everything
here is a **read-only probe** — see the "Non-intrusiveness discipline
for selftest" section in install_method.md for why.

Per-test independence: each assertion issues its own
`self.prison.query([...])` call rather than parsing a batched output
blob. Rationale in install_method.md under "Per-test independence (no
batch scripts)".

Naming: uses "Alcatraz" rather than "container" because the invariants
are backend-agnostic — a future PodmanPrison / SysboxPrison / VMPrison
would reuse the same assertions (only the `query` mechanics differ).
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from alcatrazer.alcatraz import Alcatraz
from alcatrazer.docker_prison import DockerPrison

# Whitelist of env-var name patterns that ARE allowed to carry secret-like
# values inside the sandbox (LLM API keys the user opted into via .env).
# Anything else matching the key/token/secret/pass broad pattern should not
# be present.
_ALLOWED_SECRET_ENV_PATTERN = re.compile(r"^(ANTHROPIC_API_KEY|OPENAI_API_KEY|MINIMAX_API_KEY)=")


class _AlcatrazSecurityInvariants:
    """Read-only security invariants for any running Alcatraz — MIXIN.

    Deliberately NOT a `unittest.TestCase` subclass. If it were, importing
    this name into another module (e.g. `test_smoke.py`) would make it a
    module-level `TestCase` under that module's namespace, which
    `unittest discover` then runs standalone — with no `setUpClass`
    populating `cls.prison`. Concrete classes MUST add `unittest.TestCase`
    to their bases (see `make_alcatraz_selftest_testcase` below and
    `TestAlcatrazSmokeCI` in test_smoke.py). MRO resolves `self.assertXxx`
    / `self.addCleanup` via TestCase at runtime.

    Subclasses populate two class attributes in `setUpClass` and inherit
    all the assertion methods:

      cls.prison   : Alcatraz    # for self.prison.query([...])
      cls.expected : dict        # {uid, name, email} — from .alcatrazer/
    """

    prison: Alcatraz = None  # type: ignore[assignment]
    expected: dict = None  # type: ignore[assignment]

    # --- 1. User identity (dev-base layer) ------------------------------

    def test_alcatraz_runs_as_phantom_uid(self):
        result = self.prison.query(["id"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"uid={self.expected['uid']}", result.stdout)

    def test_alcatraz_user_is_agent(self):
        result = self.prison.query(["whoami"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("agent", result.stdout)

    # --- 2. Host credential isolation (dev-base layer) ------------------

    def test_no_ssh_directory(self):
        # `test -d` returns 0 if directory exists; we want non-zero (absent).
        result = self.prison.query(["test", "-d", "/home/agent/.ssh"])
        self.assertNotEqual(result.returncode, 0)

    def test_no_gnupg_directory(self):
        result = self.prison.query(["test", "-d", "/home/agent/.gnupg"])
        self.assertNotEqual(result.returncode, 0)

    def test_global_git_config_no_alcatraz_branding(self):
        # cat ~/.gitconfig, tolerate missing file
        result = self.prison.query(["bash", "-c", "cat ~/.gitconfig 2>/dev/null || true"])
        self.assertNotIn(
            "alcatraz",
            result.stdout.lower(),
            f"Global git config contains 'alcatraz': {result.stdout}",
        )

    def test_no_host_signing_key_paths_in_global_git_config(self):
        result = self.prison.query(["bash", "-c", "cat ~/.gitconfig 2>/dev/null || true"])
        self.assertNotRegex(
            result.stdout,
            r"signingkey\s*=\s*/.",
            "Global git config leaks host signing-key path",
        )

    def test_global_signing_key_empty_or_unset(self):
        result = self.prison.query(["git", "config", "--global", "user.signingkey"])
        # Either unset (non-zero exit, empty stdout) or explicitly empty.
        self.assertEqual(result.stdout.strip(), "")

    def test_global_commit_signing_disabled(self):
        result = self.prison.query(["git", "config", "--global", "commit.gpgsign"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "false")

    # --- 3. Environment discipline (dev-base layer) ---------------------

    def test_no_leaked_secret_env_vars(self):
        result = self.prison.query(
            [
                "bash",
                "-c",
                "env | grep -iE 'key|token|secret|pass' | sort || true",
            ]
        )
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            self.assertRegex(
                line,
                _ALLOWED_SECRET_ENV_PATTERN,
                f"Unexpected secret-like env var: {line}",
            )

    # --- 4. Workspace git identity is the agent (dev-base anti-leak) ----

    def test_workspace_git_user_name_is_agent(self):
        result = self.prison.query(["git", "-C", "/workspace", "config", "--local", "user.name"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), self.expected["name"])

    def test_workspace_git_user_email_is_agent(self):
        result = self.prison.query(["git", "-C", "/workspace", "config", "--local", "user.email"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), self.expected["email"])

    def test_workspace_git_config_no_alcatraz_branding(self):
        result = self.prison.query(["git", "-C", "/workspace", "config", "--local", "--list"])
        self.assertNotIn(
            "alcatraz",
            result.stdout.lower(),
            f"Workspace git config contains 'alcatraz': {result.stdout}",
        )

    def test_workspace_initial_commit_authored_by_agent(self):
        """Read-only identity check against the existing initial commit
        (created by create_workspace at install time). No new commits."""
        result = self.prison.query(
            [
                "git",
                "-C",
                "/workspace",
                "log",
                "-1",
                "--format=%an|%ae|%cn|%ce",
            ]
        )
        self.assertEqual(result.returncode, 0)
        expected = (
            f"{self.expected['name']}|{self.expected['email']}|"
            f"{self.expected['name']}|{self.expected['email']}"
        )
        self.assertEqual(result.stdout.strip(), expected)

    # --- 5. Filesystem ownership (dev-base) -----------------------------

    def test_workspace_directory_owned_by_phantom_uid(self):
        """Read-only directory-ownership check — no file creation."""
        result = self.prison.query(["stat", "-c", "%u", "/workspace"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), self.expected["uid"])

    # --- 6. Attack surface (dev-base) -----------------------------------

    def test_docker_socket_not_mounted(self):
        result = self.prison.query(["test", "-e", "/var/run/docker.sock"])
        self.assertNotEqual(result.returncode, 0)

    def test_workspace_has_no_git_remotes(self):
        result = self.prison.query(["git", "-C", "/workspace", "remote"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    # --- 7. Zero "alcatraz" branding inside the Alcatraz ---------------

    def test_no_alcatraz_branding_in_environment(self):
        """Broad sweep across env / git config / hostname for any
        'alcatraz' string leaking inside the sandbox."""
        result = self.prison.query(
            [
                "bash",
                "-c",
                "{ env; git config --global --list; "
                "git -C /workspace config --local --list; "
                "hostname; } | grep -i alcatraz || echo CLEAN",
            ]
        )
        combined = result.stdout + result.stderr
        self.assertIn("CLEAN", combined, f"Alcatraz footprint detected: {combined}")

    @unittest.skipIf(
        os.environ.get("CI") == "true",
        "Skipped in CI — the runner's host path contains 'alcatrazer'",
    )
    def test_no_alcatraz_branding_in_mount_points(self):
        result = self.prison.query(
            [
                "bash",
                "-c",
                "cat /proc/self/mountinfo | grep -i alcatraz || echo CLEAN",
            ]
        )
        combined = result.stdout + result.stderr
        self.assertIn(
            "CLEAN",
            combined,
            f"Alcatraz footprint in mount points: {combined}",
        )


def make_alcatraz_selftest_testcase(project_dir: Path) -> type[unittest.TestCase]:
    """Build a TestCase bound to the already-running Alcatraz at `project_dir`.

    Closure capture of `project_dir` — no global state, and the class
    exists only for the caller (never picked up by `unittest discover`).
    Intended for `unittest.TestLoader().loadTestsFromTestCase(...)`.
    """

    class SelftestAlcatraz(_AlcatrazSecurityInvariants, unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            alcatraz_dir = project_dir / ".alcatrazer"
            identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
            cls.prison = DockerPrison(project_dir)
            cls.expected = {
                "uid": (alcatraz_dir / "uid").read_text().strip(),
                "name": identity_lines[0],
                "email": identity_lines[1],
            }

    return SelftestAlcatraz
