"""Shared security-invariant assertions for any running Alcatraz sandbox.

Two callers consume this module:

- `integration_tests/test_smoke.py` — CI-mode smoke test. Its TestCase
  subclass creates a fresh tempdir project, drives `_first_time_setup`
  end-to-end, runs the assertions, tears down.
- `alcatrazer.start.cmd_selftest` — `alcatrazer start --run-selftest`
  post-boot check. Uses `make_alcatraz_selftest_testcase(project_dir)` to
  build a TestCase bound to the caller's already-running Alcatraz, then
  runs the same assertions against it.

The assertion list (`_AlcatrazSecurityInvariants`) is therefore the single
source of truth for "what must hold for a running Alcatraz to be trusted."
Naming uses "Alcatraz" rather than "container" because the invariants are
backend-agnostic — a future PodmanPrison / SysboxPrison / VMPrison would
reuse the same assertions (only the boot + exec mechanics differ).
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

SELFTEST_SCRIPT = r"""
echo "===SECTION:ID==="
id
echo "===SECTION:WHOAMI==="
whoami
echo "===SECTION:SSH==="
ls -d ~/.ssh 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GNUPG==="
ls -d ~/.gnupg 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GITCONFIG==="
cat ~/.gitconfig 2>/dev/null || echo ""
echo "===SECTION:SIGNINGKEY==="
git config user.signingkey || echo ""
echo "===SECTION:GPGSIGN==="
git config commit.gpgsign
echo "===SECTION:ENV_SECRETS==="
env | grep -iE "key|token|secret|pass" | sort
echo "===SECTION:PYTHON==="
python --version 2>&1
echo "===SECTION:NODE==="
node --version 2>&1
echo "===SECTION:GIT==="
git --version 2>&1
echo "===SECTION:MISE==="
mise --version 2>&1
echo "===SECTION:CLAUDE==="
claude --version 2>&1
echo "===SECTION:MISE_LS==="
mise ls 2>&1
echo "===SECTION:WORKSPACE_GIT_CONFIG==="
git -C /workspace config --local --list 2>&1
echo "===SECTION:COMMIT_TEST==="
cd /workspace
echo "print(\"hello from smoke test\")" > _smoke_test.py
git add _smoke_test.py
git commit -m "smoke test: verify agent can commit" 2>&1
git log -1 --format="%an|%ae|%cn|%ce" 2>&1
echo "===SECTION:BRANCH_TEST==="
git checkout -b smoke-test/feature 2>&1
echo "feature work" > _smoke_feature.txt
git add _smoke_feature.txt
git commit -m "smoke test: feature branch commit" 2>&1
git checkout main 2>&1
git merge smoke-test/feature --no-edit 2>&1
echo "===SECTION:PYTHON_EXEC==="
python _smoke_test.py 2>&1
echo "===SECTION:NODE_EXEC==="
node -e "console.log(\"hello from node\")" 2>&1
echo "===SECTION:FILE_OWNERSHIP==="
ls -ln /workspace/_smoke_test.py 2>&1
echo "===SECTION:DOCKER_SOCKET==="
ls -la /var/run/docker.sock 2>/dev/null && echo "EXISTS" || echo "MISSING"
echo "===SECTION:GIT_REMOTES==="
git remote -v 2>&1 || echo "NONE"
echo "===SECTION:END==="
"""


class _AlcatrazSecurityInvariants(unittest.TestCase):
    """Assertions that must hold for any running Alcatraz sandbox.

    Subclasses provide `setUpClass` that populates three class attributes
    and runs `SELFTEST_SCRIPT` inside the Alcatraz, capturing stdout+stderr
    into `cls.output`:

      cls.container_name : str          # exec target (DockerPrison default: "workspace")
      cls.expected       : dict         # keys: uid, name, email (from .alcatrazer/)
      cls.output         : str          # delimited script output
    """

    def _section(self, name: str) -> str:
        pattern = rf"===SECTION:{name}===\n(.*?)===SECTION:"
        match = re.search(pattern, self.output, re.DOTALL)
        return match.group(1).strip() if match else ""

    # --- 1. User identity ---

    def test_alcatraz_runs_as_phantom_uid(self):
        self.assertIn(f"uid={self.expected['uid']}", self._section("ID"))

    def test_alcatraz_user_is_agent(self):
        self.assertIn("agent", self._section("WHOAMI"))

    # --- 2. Host credential isolation ---

    def test_no_ssh_directory(self):
        self.assertIn("MISSING", self._section("SSH"))

    def test_no_gnupg_directory(self):
        self.assertIn("MISSING", self._section("GNUPG"))

    def test_global_git_config_no_alcatraz(self):
        section = self._section("GITCONFIG")
        self.assertNotIn(
            "alcatraz",
            section.lower(),
            f"Global git config contains 'alcatraz': {section}",
        )

    def test_no_host_signing_key_paths(self):
        section = self._section("GITCONFIG")
        self.assertNotRegex(
            section,
            r"signingkey\s*=\s*/.",
            "Global git config leaks host signing-key path",
        )

    def test_signing_key_empty(self):
        self.assertEqual(self._section("SIGNINGKEY").strip(), "")

    def test_commit_signing_disabled(self):
        self.assertIn("false", self._section("GPGSIGN"))

    # --- 3. Environment variables ---

    def test_no_leaked_secret_env_vars(self):
        for line in self._section("ENV_SECRETS").splitlines():
            if line.strip():
                self.assertRegex(
                    line,
                    r"ANTHROPIC_API_KEY|OPENAI_API_KEY|MINIMAX_API_KEY",
                    f"Unexpected secret-like env var: {line}",
                )

    # --- 4. Development tools (dev-base + ai-base + [languages]) ---

    def test_python_available(self):
        self.assertTrue(self._section("PYTHON").strip())

    def test_node_available(self):
        self.assertTrue(self._section("NODE").strip())

    def test_git_available(self):
        self.assertTrue(self._section("GIT").strip())

    def test_mise_available(self):
        self.assertTrue(self._section("MISE").strip())

    def test_claude_available(self):
        self.assertTrue(self._section("CLAUDE").strip())

    # --- 5. Mise runtime management ---

    def test_mise_manages_python(self):
        self.assertIn("python", self._section("MISE_LS"))

    def test_mise_manages_node(self):
        self.assertIn("node", self._section("MISE_LS"))

    # --- 6. Workspace git config ---

    def test_workspace_git_name_matches_identity(self):
        self.assertIn(
            f"user.name={self.expected['name']}",
            self._section("WORKSPACE_GIT_CONFIG"),
        )

    def test_workspace_git_email_matches_identity(self):
        self.assertIn(
            f"user.email={self.expected['email']}",
            self._section("WORKSPACE_GIT_CONFIG"),
        )

    def test_workspace_git_config_no_alcatraz(self):
        section = self._section("WORKSPACE_GIT_CONFIG")
        self.assertNotIn(
            "alcatraz",
            section.lower(),
            f"Workspace git config contains 'alcatraz': {section}",
        )

    # --- 7. Commit / branch / merge ---

    def test_commit_identity_matches(self):
        expected = f"{self.expected['name']}|{self.expected['email']}"
        self.assertIn(expected, self._section("COMMIT_TEST"))

    def test_commit_identity_no_alcatraz(self):
        section = self._section("COMMIT_TEST")
        self.assertNotIn(
            "alcatraz",
            section.lower(),
            f"Commit identity contains 'alcatraz': {section}",
        )

    def test_branching_and_merging_works(self):
        section = self._section("BRANCH_TEST")
        self.assertTrue(
            "smoke-test/feature" in section or "feature branch" in section,
            f"Branch/merge test failed: {section}",
        )

    # --- 8. Code execution ---

    def test_python_execution(self):
        self.assertIn("hello from smoke test", self._section("PYTHON_EXEC"))

    def test_node_execution(self):
        self.assertIn("hello from node", self._section("NODE_EXEC"))

    # --- 9. File ownership ---

    def test_files_owned_by_phantom_uid(self):
        uid = self.expected["uid"]
        self.assertRegex(self._section("FILE_OWNERSHIP"), rf"{uid}\s+{uid}")

    # --- 10. Attack surface ---

    def test_docker_socket_not_mounted(self):
        self.assertIn("MISSING", self._section("DOCKER_SOCKET"))

    def test_no_git_remotes(self):
        cleaned = self._section("GIT_REMOTES").replace("NONE", "").strip()
        self.assertEqual(cleaned, "")


def make_alcatraz_selftest_testcase(project_dir: Path) -> type[unittest.TestCase]:
    """Build a TestCase subclass bound to the already-running Alcatraz at
    `project_dir`.

    The returned class is local to this call — `project_dir` is captured by
    closure, so no global state and no risk of `unittest discover` picking
    it up by accident. Intended to be handed to
    `unittest.TestLoader().loadTestsFromTestCase(...)`.
    """
    from alcatrazer.docker_prison import DockerPrison

    class SelftestAlcatraz(_AlcatrazSecurityInvariants):
        @classmethod
        def setUpClass(cls):
            alcatraz_dir = project_dir / ".alcatrazer"
            identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
            prison = DockerPrison(project_dir)
            cls.container_name = prison.container_name
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

    return SelftestAlcatraz
