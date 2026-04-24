"""Tests for DockerPrison — the docker-backed Alcatraz adapter.

- generate_prison() — emits the three-stage Dockerfile + copies entrypoint.sh
  (Step 3h).
- build() — runs `docker build` with the generated Dockerfile, detects or
  reuses the phantom UID, raises PrisonBuildError on failure (Step 3i).
- start() — runs `docker run -d` with workspace + cache volumes + env-file
  + `sleep infinity`; raises PrisonStartError on failure (Step 3k).
- exec(command) — runs `docker exec -u agent`, streams output, returns
  exit code (Step 3k).
- query(command) — runs `docker exec -u agent`, captures stdout / stderr
  / exit, returns CompletedProcess for programmatic inspection (Step 7).
- image_exists(), is_running(), stop(), remove(), needs_rebuild() — the
  state-query and lifecycle methods the subsequent-run detection logic
  (Step 4) needs.
"""

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import docker_prison
from alcatrazer.alcatraz import PrisonBuildError, PrisonStartError
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
        with (
            patch.object(
                docker_prison.subprocess,
                "run",
                return_value=self._fail(stdout="build stdout", stderr="build stderr"),
            ),
            self.assertRaises(PrisonBuildError) as cm,
        ):
            DockerPrison(self.project_dir).build()
        self.assertIn("build stdout", cm.exception.stdout)
        self.assertIn("build stderr", cm.exception.stderr)

    def test_success_returns_none(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()):
            self.assertIsNone(DockerPrison(self.project_dir).build())


class DockerPrisonQueryTests(unittest.TestCase):
    """query() captures stdout/stderr/exit for programmatic inspection —
    counterpart to exec() which streams to the terminal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_invokes_docker_exec_as_agent_with_container_name(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run:
            DockerPrison(self.project_dir).query(["id"])
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:5], ["docker", "exec", "-u", "agent", "workspace"])
        self.assertEqual(cmd[5:], ["id"])

    def test_captures_stdout_and_stderr_as_text(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run:
            DockerPrison(self.project_dir).query(["id"])
        kwargs = mock_run.call_args.kwargs
        self.assertTrue(kwargs.get("capture_output"))
        self.assertTrue(kwargs.get("text"))

    def test_returns_completed_process_for_inspection(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="uid=1007", stderr="")
        with patch.object(docker_prison.subprocess, "run", return_value=fake):
            result = DockerPrison(self.project_dir).query(["id"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "uid=1007")

    def test_nonzero_exit_is_returned_not_raised(self):
        """query surfaces failures via the result — callers decide."""
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch.object(docker_prison.subprocess, "run", return_value=fake):
            result = DockerPrison(self.project_dir).query(["false"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "boom")


class DockerPrisonStartTests(unittest.TestCase):
    """DockerPrison.start runs `docker run -d` with the workspace bind-mount,
    cache volumes, .env file, and `sleep infinity` as the long-lived CMD."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "workspace-dir").write_text(".devspace-abcd\n")
        (self.project_dir / ".devspace-abcd").mkdir()
        (self.project_dir / ".env").write_text("FOO=bar\n")

        # Isolate HOME so Claude credential presence is controlled per test.
        self.fake_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.fake_home.cleanup)
        env_patch = patch.dict(os.environ, {"HOME": self.fake_home.name})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _ok(self) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _fail(self, stdout="", stderr="") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)

    def test_invokes_docker_run_with_expected_skeleton(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:3], ["docker", "run", "-d"])
        self.assertIn("--name", cmd)
        self.assertIn("workspace", cmd)
        self.assertIn("alcatraz-workspace:local", cmd)
        self.assertEqual(cmd[-2:], ["sleep", "infinity"])

    def test_workspace_dir_bind_mounted_to_slash_workspace(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd = mock_run.call_args.args[0]
        expected = f"{self.project_dir / '.devspace-abcd'}:/workspace"
        self.assertIn(expected, cmd)

    def test_claude_credentials_mounted_readonly_when_present(self):
        claude = Path(self.fake_home.name) / ".claude"
        claude.mkdir()
        (claude / ".credentials.json").write_text("{}")
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd = mock_run.call_args.args[0]
        expected = f"{claude / '.credentials.json'}:/home/agent/.claude/.credentials.json:ro"
        self.assertIn(expected, cmd)

    def test_claude_credentials_skipped_when_missing(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd_str = " ".join(mock_run.call_args.args[0])
        self.assertNotIn(".credentials.json", cmd_str)

    def test_no_named_cache_volumes_attached(self):
        """Caches (mise, pip, npm) live in the container's writable overlay
        layer, not in named Docker volumes — per the "Ephemeral caches"
        section in install_method.md. Shared writable volumes would let a
        compromised agent in one Alcatraz poison every other Alcatraz on
        the laptop via a trojaned `python` binary in the mise cache. See
        memory feedback_workspace_name_markers.md for the parallel
        anti-leak discipline."""
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd_str = " ".join(mock_run.call_args.args[0])
        # No alcatraz-branded volumes (the original leak source).
        self.assertNotIn("alcatraz-mise-cache", cmd_str)
        self.assertNotIn("alcatraz-pip-cache", cmd_str)
        self.assertNotIn("alcatraz-npm-cache", cmd_str)
        # No mount into any of the three cache paths at all (belt + suspenders
        # — catches a hypothetical future "rename the volume" regression).
        self.assertNotIn("/home/agent/.local/share/mise", cmd_str)
        self.assertNotIn("/home/agent/.cache/pip", cmd_str)
        self.assertNotIn("/home/agent/.npm", cmd_str)

    def test_env_file_wired_when_present(self):
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        cmd = mock_run.call_args.args[0]
        self.assertIn("--env-file", cmd)
        self.assertIn(str(self.project_dir / ".env"), cmd)

    def test_env_file_skipped_when_missing(self):
        (self.project_dir / ".env").unlink()
        with patch.object(docker_prison.subprocess, "run", return_value=self._ok()) as mock_run:
            DockerPrison(self.project_dir).start()
        self.assertNotIn("--env-file", mock_run.call_args.args[0])

    def test_raises_prison_start_error_when_workspace_dir_missing(self):
        (self.alcatraz_dir / "workspace-dir").unlink()
        with (
            patch.object(docker_prison.subprocess, "run") as mock_run,
            self.assertRaises(PrisonStartError),
        ):
            DockerPrison(self.project_dir).start()
        mock_run.assert_not_called()

    def test_raises_prison_start_error_on_docker_failure(self):
        with (
            patch.object(
                docker_prison.subprocess,
                "run",
                return_value=self._fail(stdout="run stdout", stderr="run stderr"),
            ),
            self.assertRaises(PrisonStartError) as cm,
        ):
            DockerPrison(self.project_dir).start()
        self.assertIn("run stdout", cm.exception.stdout)
        self.assertIn("run stderr", cm.exception.stderr)


class DockerPrisonExecTests(unittest.TestCase):
    """DockerPrison.exec wraps `docker exec -u agent <container> <cmd>`,
    streams output to the caller's terminal, returns the exit code."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_invokes_docker_exec_with_agent_user(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mock_run:
            DockerPrison(self.project_dir).exec(["bash", "-c", "uv sync"])
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:5], ["docker", "exec", "-u", "agent", "workspace"])
        self.assertEqual(cmd[5:], ["bash", "-c", "uv sync"])

    def test_returns_the_exit_code(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=42),
        ):
            rc = DockerPrison(self.project_dir).exec(["false"])
        self.assertEqual(rc, 42)

    def test_does_not_capture_output(self):
        """Output streams to the user's terminal so long-running commands
        like `uv sync` show progress in real time."""
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mock_run:
            DockerPrison(self.project_dir).exec(["true"])
        kwargs = mock_run.call_args.kwargs
        self.assertNotIn("capture_output", kwargs)


class DockerPrisonImageExistsTests(unittest.TestCase):
    """image_exists() → `docker image inspect <tag>` returncode == 0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_true_when_docker_image_inspect_succeeds(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ):
            self.assertTrue(DockerPrison(self.project_dir).image_exists())

    def test_false_when_docker_image_inspect_fails(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=1),
        ):
            self.assertFalse(DockerPrison(self.project_dir).image_exists())

    def test_invokes_docker_image_inspect_with_image_tag(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mock_run:
            DockerPrison(self.project_dir).image_exists()
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd, ["docker", "image", "inspect", "alcatraz-workspace:local"])


class DockerPrisonIsRunningTests(unittest.TestCase):
    """is_running() uses `docker ps --filter name=^workspace$ --filter status=running`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_true_when_container_name_returned(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="workspace\n", stderr=""
            ),
        ):
            self.assertTrue(DockerPrison(self.project_dir).is_running())

    def test_false_when_output_empty(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ):
            self.assertFalse(DockerPrison(self.project_dir).is_running())

    def test_filters_by_exact_name_and_status(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run:
            DockerPrison(self.project_dir).is_running()
        cmd_str = " ".join(mock_run.call_args.args[0])
        # Anchored name filter so "workspace" doesn't match "my-workspace-2".
        self.assertIn("name=^workspace$", cmd_str)
        self.assertIn("status=running", cmd_str)


class DockerPrisonStopTests(unittest.TestCase):
    """stop() is idempotent: no-op if not running, else `docker stop <name>`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_noop_when_container_not_running(self):
        with (
            patch.object(DockerPrison, "is_running", return_value=False),
            patch.object(docker_prison.subprocess, "run") as mock_run,
        ):
            DockerPrison(self.project_dir).stop()
        mock_run.assert_not_called()

    def test_runs_docker_stop_when_container_running(self):
        with (
            patch.object(DockerPrison, "is_running", return_value=True),
            patch.object(
                docker_prison.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            DockerPrison(self.project_dir).stop()
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.args[0], ["docker", "stop", "workspace"])


class DockerPrisonResumeTests(unittest.TestCase):
    """resume() maps to `docker start <name>` — distinct from start()
    (which is `docker run` and always creates a fresh container).

    resume()'s whole point is to bring a stopped Alcatraz back up with its
    writable overlay layer (and caches) intact; start() would recreate it
    and lose that state. The new Step 4 lifecycle calls resume() on the
    "stopped, no recreate needed" branch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_runs_docker_start_with_container_name(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mock_run:
            DockerPrison(self.project_dir).resume()
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.args[0], ["docker", "start", "workspace"])

    def test_raises_prison_start_error_on_failure(self):
        from alcatrazer.alcatraz import PrisonStartError

        with (
            patch.object(
                docker_prison.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="no such container: workspace\n"
                ),
            ),
            self.assertRaises(PrisonStartError) as ctx,
        ):
            DockerPrison(self.project_dir).resume()
        self.assertIn("no such container", ctx.exception.stderr)


class DockerPrisonExistsTests(unittest.TestCase):
    """exists() — port-level check. True if a container matching the
    configured name exists in ANY state (running or stopped). Used by
    _subsequent_run to distinguish 'no container, fresh start' from
    'stopped container, resume candidate'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_true_when_container_name_returned(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="workspace\n", stderr=""
            ),
        ):
            self.assertTrue(DockerPrison(self.project_dir).exists())

    def test_false_when_output_empty(self):
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ):
            self.assertFalse(DockerPrison(self.project_dir).exists())

    def test_uses_ps_dash_a_so_stopped_containers_count(self):
        """Unlike is_running (which filters status=running), exists
        must see stopped containers too — that's exactly the case the
        resume-from-stopped lifecycle branch depends on."""
        with patch.object(
            docker_prison.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run:
            DockerPrison(self.project_dir).exists()
        args = mock_run.call_args.args[0]
        self.assertIn("-a", args)
        # No status=running filter (which would hide stopped containers).
        cmd_str = " ".join(args)
        self.assertNotIn("status=running", cmd_str)
        # Anchored name filter to avoid substring matches.
        self.assertIn("name=^workspace$", cmd_str)


class DockerPrisonRemoveTests(unittest.TestCase):
    """remove() is idempotent: no-op if container absent, else `docker rm -f`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_noop_when_container_does_not_exist(self):
        with (
            patch.object(DockerPrison, "exists", return_value=False),
            patch.object(docker_prison.subprocess, "run") as mock_run,
        ):
            DockerPrison(self.project_dir).remove()
        mock_run.assert_not_called()

    def test_runs_docker_rm_force_when_container_exists(self):
        with (
            patch.object(DockerPrison, "exists", return_value=True),
            patch.object(
                docker_prison.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            DockerPrison(self.project_dir).remove()
        # -f so running containers are also removed (belt + suspenders).
        self.assertEqual(mock_run.call_args.args[0], ["docker", "rm", "-f", "workspace"])


class DockerPrisonNeedsRebuildTests(unittest.TestCase):
    """needs_rebuild compares the would-be Dockerfile against the on-disk one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.project_dir / ".alcatrazer").mkdir()

    def test_true_when_no_dockerfile_on_disk(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        self.assertTrue(DockerPrison(self.project_dir).needs_rebuild(data))

    def test_false_when_dockerfile_matches_would_be(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        DockerPrison(self.project_dir).generate_prison(data)
        self.assertFalse(DockerPrison(self.project_dir).needs_rebuild(data))

    def test_true_when_dockerfile_differs(self):
        DockerPrison(self.project_dir).generate_prison(
            {"languages": {"python": {"version": "3.12"}}}
        )
        changed = {"languages": {"python": {"version": "3.13"}}}
        self.assertTrue(DockerPrison(self.project_dir).needs_rebuild(changed))


if __name__ == "__main__":
    unittest.main()
