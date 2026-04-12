"""
Docker smoke tests for Alcatrazer container isolation.

Verifies that the container is properly isolated from the host:
- Runs as a phantom UID (no matching host user)
- No access to host credentials, SSH keys, or signing keys
- Only explicitly passed environment variables are visible
- All development tools are available (Python, Node, Bun, Git, Tmux, etc.)
- Agents can commit and branch inside the workspace
- Docker socket is not accessible
- No git remotes are configured
- Zero alcatraz footprint inside the container

Prerequisites:
    ./src/alcatrazer/scripts/initialize_alcatraz.sh
    alcatrazer.toml must exist in project root
    docker compose build

These tests require Docker and a built container image.
Skipped by default in 'alcatrazer test' — run with 'alcatrazer test --smoke'.
"""

import os
import re
import subprocess
import unittest
from pathlib import Path


def project_dir() -> Path:
    """Project root is 3 levels up from tests/."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _load_expected_values():
    """Load expected UID and agent identity from .alcatrazer/."""
    root = project_dir()
    uid_file = root / ".alcatrazer" / "uid"
    identity_file = root / ".alcatrazer" / "agent-identity"
    if not uid_file.exists() or not identity_file.exists():
        return None
    uid = uid_file.read_text().strip()
    lines = identity_file.read_text().strip().split("\n")
    return {"uid": uid, "name": lines[0], "email": lines[1]}


# Prefer project-root docker-compose.yml (generated with correct paths),
# fall back to the template in src/alcatrazer/container/.
_root_compose = project_dir() / "docker-compose.yml"
_template_compose = project_dir() / "src" / "alcatrazer" / "container" / "docker-compose.yml"
COMPOSE_FILE = str(_root_compose if _root_compose.exists() else _template_compose)

# Bash script that runs inside the container to collect all test data
# in delimiter-separated sections for reliable parsing.
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
cat ~/.gitconfig
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
echo "===SECTION:BUN==="
bun --version 2>&1
echo "===SECTION:GIT==="
git --version 2>&1
echo "===SECTION:MISE==="
mise --version 2>&1
echo "===SECTION:TMUX==="
tmux -V 2>&1
echo "===SECTION:RIPGREP==="
rg --version 2>&1 | head -1
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
git log --oneline --graph --all 2>&1
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
echo "===SECTION:CLEANUP==="
cd /workspace
git checkout main 2>/dev/null
git branch -d smoke-test/feature 2>/dev/null || true
git rm -f _smoke_test.py _smoke_feature.txt 2>/dev/null
git commit -m "smoke test: cleanup" 2>/dev/null
git reset --hard HEAD~3 2>/dev/null
echo "CLEANED"
echo "===SECTION:END==="
"""


def _docker_available() -> bool:
    """Check if Docker is available and compose file exists."""
    if not Path(COMPOSE_FILE).exists():
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


@unittest.skipUnless(
    _docker_available() and _load_expected_values(),
    "Docker not available or alcatrazer not initialized",
)
class TestContainerIsolation(unittest.TestCase):
    """Verify container isolation via Docker smoke tests."""

    @classmethod
    def setUpClass(cls):
        """Run the container once, capture all section output."""
        cls.expected = _load_expected_values()
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                COMPOSE_FILE,
                "run",
                "--rm",
                "workspace",
                "bash",
                "-c",
                CONTAINER_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        cls.output = result.stdout + result.stderr

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
            "alcatraz", section.lower(), f"Global git config contains 'alcatraz': {section}"
        )

    def test_no_host_signing_key_paths(self):
        section = self._section("GITCONFIG")
        self.assertNotRegex(
            section, r"signingkey\s*=\s*/.", "Global git config leaks host signing key path"
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

    def test_alcatraz_uid_not_in_runtime_env(self):
        section = self._section("ENV_SECRETS")
        self.assertNotIn("ALCATRAZ_UID", section)

    # --- 4. Development tools ---

    def test_python_available(self):
        self.assertTrue(self._section("PYTHON").strip())

    def test_node_available(self):
        self.assertTrue(self._section("NODE").strip())

    def test_bun_available(self):
        self.assertTrue(self._section("BUN").strip())

    def test_git_available(self):
        self.assertTrue(self._section("GIT").strip())

    def test_mise_available(self):
        self.assertTrue(self._section("MISE").strip())

    def test_tmux_available(self):
        self.assertTrue(self._section("TMUX").strip())

    def test_ripgrep_available(self):
        self.assertTrue(self._section("RIPGREP").strip())

    def test_claude_available(self):
        self.assertTrue(self._section("CLAUDE").strip())

    # --- 5. Mise runtime management ---

    def test_mise_manages_python(self):
        self.assertIn("python", self._section("MISE_LS"))

    def test_mise_manages_node(self):
        self.assertIn("node", self._section("MISE_LS"))

    def test_mise_manages_bun(self):
        self.assertIn("bun", self._section("MISE_LS"))

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
            "alcatraz", section.lower(), f"Workspace git config contains 'alcatraz': {section}"
        )

    # --- 7. Git commit ---

    def test_commit_identity_matches(self):
        section = self._section("COMMIT_TEST")
        expected = f"{self.expected['name']}|{self.expected['email']}"
        self.assertIn(expected, section)

    def test_commit_identity_no_alcatraz(self):
        section = self._section("COMMIT_TEST")
        self.assertNotIn(
            "alcatraz", section.lower(), f"Commit identity contains 'alcatraz': {section}"
        )

    # --- 8. Branch and merge ---

    def test_branching_and_merging_works(self):
        section = self._section("BRANCH_TEST")
        self.assertTrue(
            "smoke-test/feature" in section or "feature branch" in section,
            f"Branch/merge test failed: {section}",
        )

    # --- 9. Code execution ---

    def test_python_execution(self):
        self.assertIn("hello from smoke test", self._section("PYTHON_EXEC"))

    def test_node_execution(self):
        self.assertIn("hello from node", self._section("NODE_EXEC"))

    # --- 10. File ownership ---

    def test_files_owned_by_phantom_uid(self):
        section = self._section("FILE_OWNERSHIP")
        uid = self.expected["uid"]
        self.assertRegex(section, rf"{uid}\s+{uid}")

    # --- 11. Docker socket ---

    def test_docker_socket_not_mounted(self):
        self.assertIn("MISSING", self._section("DOCKER_SOCKET"))

    # --- 12. No git remotes ---

    def test_no_git_remotes(self):
        section = self._section("GIT_REMOTES")
        cleaned = section.replace("NONE", "").strip()
        self.assertEqual(cleaned, "")

    # --- Cleanup ---

    def test_cleanup_succeeded(self):
        self.assertIn("CLEANED", self._section("CLEANUP"))


@unittest.skipUnless(
    _docker_available() and _load_expected_values(),
    "Docker not available or alcatrazer not initialized",
)
class TestDockerfileBuildGuard(unittest.TestCase):
    """Verify Dockerfile rejects build without USER_UID."""

    def test_dockerfile_rejects_empty_uid(self):
        dockerfile = str(project_dir() / "src" / "alcatrazer" / "container" / "Dockerfile")
        result = subprocess.run(
            ["docker", "build", "--build-arg", "USER_UID=", "-f", dockerfile, str(project_dir())],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertIn("USER_UID build arg is required", result.stdout + result.stderr)


@unittest.skipUnless(
    _docker_available() and _load_expected_values(),
    "Docker not available or alcatrazer not initialized",
)
class TestZeroAlcatrazFootprint(unittest.TestCase):
    """Catch-all: grep for 'alcatraz' across env, git config, hostname, mountinfo."""

    def _run_footprint_check(self, command: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                COMPOSE_FILE,
                "run",
                "--rm",
                "workspace",
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
        output = self._run_footprint_check(
            "{ env; git config --global --list; "
            "git -C /workspace config --local --list; "
            "hostname; } "
            "| grep -i alcatraz || echo CLEAN"
        )
        self.assertIn("CLEAN", output, f"Alcatraz footprint detected inside container: {output}")

    @unittest.skipIf(
        os.environ.get("CI") == "true",
        "Skipped in CI — host path contains repo name 'alcatrazer'",
    )
    def test_no_alcatraz_in_container_mount_points(self):
        output = self._run_footprint_check(
            "cat /proc/self/mountinfo | grep -i alcatraz || echo CLEAN"
        )
        self.assertIn("CLEAN", output, f"Alcatraz footprint in mount points: {output}")


if __name__ == "__main__":
    unittest.main()
