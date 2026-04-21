"""Docker smoke tests for Alcatrazer container isolation.

Drives `alcatrazer.start._first_time_setup` against a fresh temp project
(with the interactive wizards stubbed), then shells the test-script into
the running container via `docker exec`. No docker-compose involved.

Covers:
- Runs as a phantom UID (no matching host user)
- No access to host credentials, SSH keys, signing keys
- Only explicitly passed environment variables are visible
- Tools from dev-base / ai-base / [languages] all resolve
- Workspace git identity is the random agent identity
- Agents can commit, branch, merge
- No docker socket, no git remotes
- Zero "alcatraz" footprint inside the container

These tests require Docker. Skipped by default in `alcatrazer test` —
run with `alcatrazer test --smoke`.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import start as start_mod
from alcatrazer.docker_prison import DockerPrison

CODING_ENV = {
    "languages": {
        "python": {"version": "3.12"},
        "node": {"version": "22"},
    },
}


CONTAINER_SCRIPT = r"""
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
class TestContainerIsolation(unittest.TestCase):
    """Bring the container up via the new installer, exercise isolation +
    tooling invariants, tear it down."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        # Drive the full first-time pipeline, with the interactive prompts
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

        # Keep a prison reference for exec + teardown (DockerPrison is
        # stateless apart from project_dir, so a fresh instance is fine).
        cls.prison = DockerPrison(cls.project_dir)

        alcatraz_dir = cls.project_dir / ".alcatrazer"
        identity_lines = (alcatraz_dir / "agent-identity").read_text().strip().split("\n")
        cls.expected = {
            "uid": (alcatraz_dir / "uid").read_text().strip(),
            "name": identity_lines[0],
            "email": identity_lines[1],
        }

        # Run the data-collection script inside the running container.
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "agent",
                cls.prison.container_name,
                "bash",
                "-c",
                CONTAINER_SCRIPT,
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

    def _section(self, name: str) -> str:
        """Extract a named section from the container output."""
        pattern = rf"===SECTION:{name}===\n(.*?)===SECTION:"
        match = re.search(pattern, self.output, re.DOTALL)
        return match.group(1).strip() if match else ""

    # --- 1. User identity ---

    def test_container_runs_as_phantom_uid(self):
        section = self._section("ID")
        self.assertIn(f"uid={self.expected['uid']}", section)

    def test_container_user_is_agent(self):
        section = self._section("WHOAMI")
        self.assertIn("agent", section)

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
        section = self._section("SIGNINGKEY")
        self.assertEqual(section.strip(), "")

    def test_commit_signing_disabled(self):
        self.assertIn("false", self._section("GPGSIGN"))

    # --- 3. Environment variables ---

    def test_no_leaked_secret_env_vars(self):
        section = self._section("ENV_SECRETS")
        for line in section.splitlines():
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
        section = self._section("WORKSPACE_GIT_CONFIG")
        self.assertIn(f"user.name={self.expected['name']}", section)

    def test_workspace_git_email_matches_identity(self):
        section = self._section("WORKSPACE_GIT_CONFIG")
        self.assertIn(f"user.email={self.expected['email']}", section)

    def test_workspace_git_config_no_alcatraz(self):
        section = self._section("WORKSPACE_GIT_CONFIG")
        self.assertNotIn(
            "alcatraz",
            section.lower(),
            f"Workspace git config contains 'alcatraz': {section}",
        )

    # --- 7. Commit / branch / merge ---

    def test_commit_identity_matches(self):
        section = self._section("COMMIT_TEST")
        expected = f"{self.expected['name']}|{self.expected['email']}"
        self.assertIn(expected, section)

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
        section = self._section("FILE_OWNERSHIP")
        uid = self.expected["uid"]
        self.assertRegex(section, rf"{uid}\s+{uid}")

    # --- 10. Attack surface ---

    def test_docker_socket_not_mounted(self):
        self.assertIn("MISSING", self._section("DOCKER_SOCKET"))

    def test_no_git_remotes(self):
        section = self._section("GIT_REMOTES")
        cleaned = section.replace("NONE", "").strip()
        self.assertEqual(cleaned, "")


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestZeroAlcatrazFootprint(unittest.TestCase):
    """Grep for 'alcatraz' across env / git config / hostname / mountinfo.

    Shares its container with TestContainerIsolation above — setUpClass is
    expected to have already booted the prison. Uses the canonical container
    name so the exec targets the same box.
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

    def test_no_alcatraz_in_container(self):
        output = self._footprint_grep(
            "{ env; git config --global --list; "
            "git -C /workspace config --local --list; "
            "hostname; } "
            "| grep -i alcatraz || echo CLEAN"
        )
        self.assertIn(
            "CLEAN",
            output,
            f"Alcatraz footprint detected inside container: {output}",
        )

    @unittest.skipIf(
        os.environ.get("CI") == "true",
        "Skipped in CI — host path contains repo name 'alcatrazer'",
    )
    def test_no_alcatraz_in_container_mount_points(self):
        output = self._footprint_grep("cat /proc/self/mountinfo | grep -i alcatraz || echo CLEAN")
        self.assertIn(
            "CLEAN",
            output,
            f"Alcatraz footprint in mount points: {output}",
        )


if __name__ == "__main__":
    unittest.main()
