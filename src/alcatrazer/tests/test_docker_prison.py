"""Tests for DockerPrison — the docker-backed Alcatraz adapter.

- generate_prison() — emits the three-stage Dockerfile + copies entrypoint.sh
  (Step 3h).
- build() — runs `docker build` with the generated Dockerfile, detects or
  reuses the phantom UID, raises PrisonBuildError on failure (Step 3i).

Remaining operational methods (start, stop, exec, …) are still skeletons
asserted in test_alcatraz.py until their respective installer steps land.
"""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import docker_prison
from alcatrazer.alcatraz import PrisonBuildError
from alcatrazer.docker_prison import DockerPrison


class DockerPrisonDockerfileGenerationTests(unittest.TestCase):
    """generate_prison renders a three-stage Dockerfile from the coding-
    environment dict."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _generate(self, data: dict) -> str:
        DockerPrison(self.project_dir).generate_prison(data)
        dockerfile = self.project_dir / ".alcatrazer" / "Dockerfile"
        self.assertTrue(dockerfile.is_file())
        return dockerfile.read_text()

    def _slice(self, content: str, begin_marker: str, end_marker: str | None = None) -> str:
        begin = content.index(begin_marker)
        if end_marker is None:
            return content[begin:]
        end = content.index(end_marker, begin)
        return content[begin:end]

    def test_dockerfile_written_to_alcatrazer_dir(self):
        self._generate({"languages": {"python": {"version": "3.12"}}})
        self.assertTrue((self.project_dir / ".alcatrazer" / "Dockerfile").is_file())

    def test_three_stages_in_order_dev_base_ai_base_dev(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        idx_dev_base = content.find("FROM ubuntu:24.04 AS dev-base")
        idx_ai_base = content.find("FROM dev-base AS ai-base")
        idx_dev = content.find("FROM ai-base AS dev")
        self.assertGreaterEqual(idx_dev_base, 0)
        self.assertGreaterEqual(idx_ai_base, 0)
        self.assertGreaterEqual(idx_dev, 0)
        self.assertLess(idx_dev_base, idx_ai_base)
        self.assertLess(idx_ai_base, idx_dev)

    def test_dev_base_has_security_core(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        dev_base = self._slice(content, "FROM ubuntu:24.04 AS dev-base", "FROM dev-base AS ai-base")
        self.assertIn("ARG USER_UID", dev_base)
        self.assertIn("gosu", dev_base)
        self.assertIn("useradd --uid ${USER_UID}", dev_base)
        self.assertIn("curl https://mise.run", dev_base)
        self.assertIn("git config --global commit.gpgsign false", dev_base)

    def test_dev_base_omits_dev_ergonomics_packages(self):
        """tmux, ripgrep, build-essential, unzip belong in user's [os], not dev-base."""
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        dev_base = self._slice(content, "FROM ubuntu:24.04 AS dev-base", "FROM dev-base AS ai-base")
        for pkg in ("tmux", "ripgrep", "build-essential", "unzip"):
            self.assertNotIn(pkg, dev_base, f"{pkg!r} must not appear in dev-base")

    def test_dev_base_does_not_install_claude(self):
        """Claude install moved to ai-base. dev-base is agent-agnostic."""
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        dev_base = self._slice(content, "FROM ubuntu:24.04 AS dev-base", "FROM dev-base AS ai-base")
        self.assertNotIn("claude.ai", dev_base)

    def test_ai_base_installs_claude_code_cli(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        ai_base = self._slice(content, "FROM dev-base AS ai-base", "FROM ai-base AS dev")
        self.assertIn("claude.ai/install.sh", ai_base)

    def test_dev_stage_mise_use_per_language(self):
        content = self._generate(
            {
                "languages": {
                    "python": {"version": "3.12"},
                    "node": {"version": "22"},
                }
            }
        )
        dev = self._slice(content, "FROM ai-base AS dev")
        self.assertIn("mise use --global python@3.12", dev)
        self.assertIn("mise use --global node@22", dev)

    def test_dev_stage_mise_use_for_non_default_manager(self):
        content = self._generate({"languages": {"python": {"version": "3.12", "manager": "uv"}}})
        dev = self._slice(content, "FROM ai-base AS dev")
        self.assertIn("mise use --global uv", dev)

    def test_no_manager_line_when_language_uses_default(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        for mgr in ("uv", "poetry", "pnpm", "yarn"):
            self.assertNotIn(mgr, content)

    def test_os_packages_become_apt_install_in_dev_stage(self):
        content = self._generate(
            {
                "os": {"packages": ["build-essential", "libpq-dev"]},
                "languages": {"python": {"version": "3.12"}},
            }
        )
        dev = self._slice(content, "FROM ai-base AS dev")
        self.assertIn("apt-get update && apt-get install -y", dev)
        self.assertIn("build-essential", dev)
        self.assertIn("libpq-dev", dev)

    def test_no_apt_block_in_dev_when_os_absent(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        dev = self._slice(content, "FROM ai-base AS dev")
        self.assertNotIn("apt-get install", dev)

    def test_verify_block_always_chains_git_mise_claude(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        dev = self._slice(content, "FROM ai-base AS dev")
        self.assertIn("git --version", dev)
        self.assertIn("mise --version", dev)
        self.assertIn("claude --version", dev)

    def test_verify_block_uses_python_version_check(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        self.assertIn("python --version", content)

    def test_verify_block_uses_rustc_for_rust(self):
        content = self._generate({"languages": {"rust": {"version": "1.75"}}})
        self.assertIn("rustc --version", content)

    def test_verify_block_uses_go_version_subcommand_for_go(self):
        content = self._generate({"languages": {"go": {"version": "1.22"}}})
        self.assertIn("go version", content)

    def test_layer_order_within_dev_is_apt_mise_verify_entrypoint(self):
        content = self._generate(
            {
                "os": {"packages": ["libpq-dev"]},
                "languages": {"python": {"version": "3.12"}},
            }
        )
        idx_dev = content.find("FROM ai-base AS dev")
        idx_apt = content.find("apt-get install", idx_dev)
        idx_mise = content.find("mise use --global python", idx_dev)
        idx_verify = content.find("claude --version", idx_dev)
        idx_entrypoint = content.find("ENTRYPOINT")
        self.assertLess(idx_dev, idx_apt)
        self.assertLess(idx_apt, idx_mise)
        self.assertLess(idx_mise, idx_verify)
        self.assertLess(idx_verify, idx_entrypoint)

    def test_entrypoint_tail_present(self):
        content = self._generate({"languages": {"python": {"version": "3.12"}}})
        self.assertIn("COPY --chmod=755 entrypoint.sh", content)
        self.assertIn("WORKDIR /workspace", content)
        self.assertIn('ENTRYPOINT ["entrypoint.sh"]', content)
        self.assertIn('CMD ["/bin/bash"]', content)

    def test_startup_commands_are_not_baked_into_dockerfile(self):
        content = self._generate(
            {
                "languages": {"python": {"version": "3.12"}},
                "startup": {"commands": ["uv sync", "npm install"]},
            }
        )
        self.assertNotIn("uv sync", content)
        self.assertNotIn("npm install", content)


class DockerPrisonEntrypointGenerationTests(unittest.TestCase):
    """generate_prison also copies entrypoint.sh byte-exact from the package."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.minimal_data = {"languages": {"python": {"version": "3.12"}}}

    def test_writes_entrypoint_to_alcatrazer_dir(self):
        DockerPrison(self.project_dir).generate_prison(self.minimal_data)
        self.assertTrue((self.project_dir / ".alcatrazer" / "entrypoint.sh").is_file())

    def test_content_matches_package_template_bit_exact(self):
        source = Path(docker_prison.__file__).parent / "container" / "entrypoint.sh"
        DockerPrison(self.project_dir).generate_prison(self.minimal_data)
        target = self.project_dir / ".alcatrazer" / "entrypoint.sh"
        self.assertEqual(
            hashlib.sha256(target.read_bytes()).hexdigest(),
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )


class DockerPrisonBuildTests(unittest.TestCase):
    """DockerPrison.build runs `docker build` with the generated Dockerfile,
    a phantom-UID build arg, and the tight .alcatrazer/ build context. On
    non-zero exit it raises PrisonBuildError carrying the raw stdout/stderr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")
        # Pre-seed a persisted phantom UID unless a test overrides.
        (self.alcatraz_dir / "uid").write_text("1007\n")

    def _ok(self) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _fail(self, stdout="", stderr="") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)

    def test_invokes_docker_build_with_expected_arguments(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).build()
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "docker")
        self.assertEqual(cmd[1], "build")
        self.assertIn("--build-arg", cmd)
        self.assertIn("USER_UID=1007", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("alcatraz-workspace:local", cmd)
        self.assertIn("-f", cmd)
        self.assertIn(str(self.alcatraz_dir / "Dockerfile"), cmd)
        # Build context is tight (just .alcatrazer/), not the whole project.
        self.assertEqual(cmd[-1], str(self.alcatraz_dir))

    def test_captures_output_via_subprocess(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).build()
        # capture_output=True and text=True so build error messages are strings.
        kwargs = mock_run.call_args.kwargs
        self.assertTrue(kwargs.get("capture_output"))
        self.assertTrue(kwargs.get("text"))

    def test_reuses_persisted_uid_without_detecting(self):
        with (
            patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run,
            patch("alcatrazer.identity.detect_phantom_uid") as mock_detect,
        ):
            DockerPrison(self.project_dir).build()
        cmd = mock_run.call_args.args[0]
        self.assertIn("USER_UID=1007", cmd)
        mock_detect.assert_not_called()

    def test_detects_and_persists_uid_when_missing(self):
        (self.alcatraz_dir / "uid").unlink()
        with (
            patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run,
            patch("alcatrazer.identity.detect_phantom_uid", return_value=1042),
        ):
            DockerPrison(self.project_dir).build()
        cmd = mock_run.call_args.args[0]
        self.assertIn("USER_UID=1042", cmd)
        self.assertEqual((self.alcatraz_dir / "uid").read_text().strip(), "1042")

    def test_raises_prison_build_error_on_non_zero_exit(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=self._fail(stdout="build stdout", stderr="build stderr"),
        ), self.assertRaises(PrisonBuildError) as cm:
            DockerPrison(self.project_dir).build()
        self.assertIn("build stdout", cm.exception.stdout)
        self.assertIn("build stderr", cm.exception.stderr)

    def test_success_returns_none(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()):
            self.assertIsNone(DockerPrison(self.project_dir).build())


if __name__ == "__main__":
    unittest.main()
