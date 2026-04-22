"""Tests for the `alcatrazer start` CLI skeleton.

Step 3a — routing: `.alcatrazer/` presence decides first-time vs subsequent.
Step 3b — first-time flow aborts unless run at a git repo root.
Step 3c — promotion-identity read + interactive prompt helpers.
Step 3d — coding-environment wizard (languages, OS packages, startup).
Step 3e — config-file writers (coding-environment.toml, .alcatrazer/config.toml,
          .env.example).
Step 3f — .git/info/exclude writer (idempotent, preserves user content).
Step 3g — package-source extraction into .alcatrazer/src/alcatrazer/.

Step 3h (Dockerfile + entrypoint.sh) is tested in test_docker_prison.py, since
backend-specific artifact generation belongs to the sandbox adapter.
"""

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alcatrazer import __version__, cli, identity, languages, selftest, start
from alcatrazer.alcatraz import Alcatraz, PrisonBuildError

GIT_REPO_ROOT_ERROR = "alcatrazer must be run from a git repository root."


class CmdStartGuardTests(unittest.TestCase):
    """cmd_start requires `.alcatrazer/` to exist — init produces it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_errors_when_no_alcatrazer_dir(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_start(self.project_dir)
        self.assertNotEqual(rc, 0)
        self.assertIn("alcatrazer init", stderr.getvalue())

    def test_does_not_invoke_first_run_or_subsequent_when_missing(self):
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            start.cmd_start(self.project_dir)
        first.assert_not_called()
        subsequent.assert_not_called()


class CmdStartRoutingTests(unittest.TestCase):
    """With `.alcatrazer/` present, cmd_start routes on image_exists:
    image absent → first-run branch (3.5); image present → subsequent run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        (self.project_dir / ".alcatrazer").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _prison(self, image_exists: bool) -> Mock:
        p = Mock(spec=Alcatraz)
        p.image_exists.return_value = image_exists
        return p

    def test_no_image_routes_to_first_run_after_init(self):
        prison = self._prison(image_exists=False)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        first.assert_called_once_with(self.project_dir, prison=prison)
        subsequent.assert_not_called()
        self.assertEqual(rc, 0)

    def test_image_present_routes_to_subsequent_run(self):
        prison = self._prison(image_exists=True)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        subsequent.assert_called_once_with(self.project_dir, prison=prison)
        first.assert_not_called()
        self.assertEqual(rc, 0)

    def test_routing_propagates_handler_return_code(self):
        prison = self._prison(image_exists=False)
        with patch.object(start, "_first_run_after_init", return_value=7):
            rc = start.cmd_start(self.project_dir, prison=prison)
        self.assertEqual(rc, 7)


class CliVersionFlagTests(unittest.TestCase):
    """Standard `--version` / `-V` flag — the `alcatrazer version` subcommand
    (noun-as-verb wart) is replaced by the flag form."""

    def _run(self, argv: list[str]) -> tuple[str, str, int | None]:
        stdout, stderr = io.StringIO(), io.StringIO()
        exit_code = None
        try:
            with (
                patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                cli.main()
        except SystemExit as e:
            exit_code = e.code
        return stdout.getvalue(), stderr.getvalue(), exit_code

    def test_long_flag_prints_version(self):
        out, _, rc = self._run(["alcatrazer", "--version"])
        self.assertIn(__version__, out)
        # argparse's `action="version"` sys.exit(0)s after printing.
        self.assertEqual(rc, 0)

    def test_short_flag_prints_version(self):
        out, _, rc = self._run(["alcatrazer", "-V"])
        self.assertIn(__version__, out)
        self.assertEqual(rc, 0)

    def test_version_output_is_just_name_and_version(self):
        """Single line, easy to parse: `alcatrazer <version>`."""
        out, _, _ = self._run(["alcatrazer", "--version"])
        self.assertEqual(out.strip(), f"alcatrazer {__version__}")

    def test_version_subcommand_no_longer_recognized(self):
        """`alcatrazer version` was a noun-as-verb wart — retired in favor
        of the `--version` flag. argparse's "invalid choice" error handles
        the rejection; exit code 2 is the Unix convention for usage errors."""
        _, err, rc = self._run(["alcatrazer", "version"])
        self.assertIn("invalid choice", err)
        self.assertIn("version", err)
        self.assertNotEqual(rc, 0)


class CliIntegrationTests(unittest.TestCase):
    def test_cli_start_command_invokes_cmd_start(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start"]),
            patch.object(start, "cmd_start", return_value=0) as mock_start,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_start.assert_called_once()
        self.assertEqual(cm.exception.code, 0)

    def test_cli_start_propagates_nonzero_exit(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start"]),
            patch.object(start, "cmd_start", return_value=2),
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        self.assertEqual(cm.exception.code, 2)


class CmdInitGitRepoCheckTests(unittest.TestCase):
    """Step 3b: init must refuse to run outside a git repo root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run_init(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_init(self.project_dir)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_aborts_when_not_a_git_repo(self):
        rc, _, err = self._run_init()
        self.assertNotEqual(rc, 0)
        self.assertIn(GIT_REPO_ROOT_ERROR, err)


class ReadGitIdentityTests(unittest.TestCase):
    """Step 3c: read user.name + user.email from git config, local first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        self.fake_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.fake_home.cleanup)
        # Isolate global + system config so the host user's ~/.gitconfig
        # cannot leak into these tests.
        env_patch = patch.dict(
            os.environ,
            {
                "HOME": self.fake_home.name,
                "XDG_CONFIG_HOME": self.fake_home.name,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

        subprocess.run(["git", "init", "-q", str(self.project_dir)], check=True)

    def _set_local(self, key: str, value: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.project_dir), "config", "--local", key, value],
            check=True,
        )

    def _write_global(self, name: str | None = None, email: str | None = None) -> None:
        lines = ["[user]"]
        if name is not None:
            lines.append(f"    name = {name}")
        if email is not None:
            lines.append(f"    email = {email}")
        (Path(self.fake_home.name) / ".gitconfig").write_text("\n".join(lines) + "\n")

    def test_reads_local_config(self):
        self._set_local("user.name", "Local User")
        self._set_local("user.email", "local@example.com")
        name, email = start.read_git_identity(self.project_dir)
        self.assertEqual(name, "Local User")
        self.assertEqual(email, "local@example.com")

    def test_returns_none_when_nothing_configured(self):
        name, email = start.read_git_identity(self.project_dir)
        self.assertIsNone(name)
        self.assertIsNone(email)

    def test_falls_back_to_global(self):
        self._write_global(name="Global User", email="global@example.com")
        name, email = start.read_git_identity(self.project_dir)
        self.assertEqual(name, "Global User")
        self.assertEqual(email, "global@example.com")

    def test_local_takes_precedence_over_global_per_field(self):
        self._write_global(name="Global User", email="global@example.com")
        self._set_local("user.name", "Local User")
        # email only in global — should still be found
        name, email = start.read_git_identity(self.project_dir)
        self.assertEqual(name, "Local User")
        self.assertEqual(email, "global@example.com")


class AskPromotionIdentityTests(unittest.TestCase):
    """Step 3c: interactive prompt, Y/n default Y, manual fallback on decline."""

    def _run(self, detected, inputs):
        input_iter = iter(inputs)
        stdout = io.StringIO()
        prompts: list[str] = []

        def fake_input(prompt=""):
            prompts.append(prompt)
            return next(input_iter)

        with (
            patch.object(start, "read_git_identity", return_value=detected),
            patch("builtins.input", side_effect=fake_input),
            contextlib.redirect_stdout(stdout),
        ):
            result = start.ask_promotion_identity(Path("/"))
        return result, stdout.getvalue(), prompts

    def test_accepts_detected_with_enter(self):
        (name, email), _, _ = self._run(("Alice", "a@example.com"), [""])
        self.assertEqual((name, email), ("Alice", "a@example.com"))

    def test_accepts_detected_with_y(self):
        (name, email), _, _ = self._run(("Alice", "a@example.com"), ["y"])
        self.assertEqual((name, email), ("Alice", "a@example.com"))

    def test_accepts_detected_with_yes_uppercase(self):
        (name, email), _, _ = self._run(("Alice", "a@example.com"), ["YES"])
        self.assertEqual((name, email), ("Alice", "a@example.com"))

    def test_declines_and_enters_manual(self):
        result, _, _ = self._run(
            ("Alice", "a@example.com"),
            ["n", "Bob Builder", "bob@example.com"],
        )
        self.assertEqual(result, ("Bob Builder", "bob@example.com"))

    def test_prompts_manual_when_nothing_detected(self):
        result, _, _ = self._run((None, None), ["Bob", "bob@example.com"])
        self.assertEqual(result, ("Bob", "bob@example.com"))

    def test_prompts_manual_when_partial_detected(self):
        # Can't offer a Y/n on a half-identity — must ask for both.
        result, _, _ = self._run(("Alice", None), ["Bob", "bob@example.com"])
        self.assertEqual(result, ("Bob", "bob@example.com"))

    def test_prompt_message_contains_identity_and_yn(self):
        _, stdout, prompts = self._run(("Alice", "a@example.com"), [""])
        combined = stdout + "".join(prompts)
        self.assertIn("Alice", combined)
        self.assertIn("a@example.com", combined)
        self.assertIn("[Y/n]", combined)
        self.assertIn("Detected git identity", combined)


class SupportedLanguagesTests(unittest.TestCase):
    """Step 3d: the wizard must know a fixed set of mise-supported languages."""

    def test_has_the_four_supported_languages(self):
        self.assertEqual(
            set(languages.SUPPORTED_LANGUAGES),
            {"python", "node", "rust", "go"},
        )

    def test_python_default_manager_is_pip_with_alternatives(self):
        py = languages.SUPPORTED_LANGUAGES["python"]
        self.assertEqual(py["default_manager"], "pip")
        self.assertIn("uv", py["managers"])
        self.assertIn("poetry", py["managers"])

    def test_every_language_has_a_version_check_command(self):
        # Step 3h prep: the Dockerfile's verify block needs a per-language
        # command since not every tool accepts --version.
        for name, cfg in languages.SUPPORTED_LANGUAGES.items():
            self.assertIn(
                "version_check",
                cfg,
                f"{name!r} must declare a version_check command",
            )
            self.assertTrue(
                cfg["version_check"].strip(),
                f"{name!r} version_check must be non-empty",
            )

    def test_python_version_check(self):
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["python"]["version_check"],
            "python --version",
        )

    def test_node_version_check(self):
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["node"]["version_check"],
            "node --version",
        )

    def test_rust_version_check_uses_rustc(self):
        # rust is the language; `rustc` is the compiler binary.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["rust"]["version_check"],
            "rustc --version",
        )

    def test_go_version_check_has_no_dashes(self):
        # `go version` (subcommand), not `go --version`.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["go"]["version_check"],
            "go version",
        )

    def test_node_default_manager_is_npm_with_alternatives(self):
        node = languages.SUPPORTED_LANGUAGES["node"]
        self.assertEqual(node["default_manager"], "npm")
        self.assertIn("pnpm", node["managers"])
        self.assertIn("yarn", node["managers"])


def _run_wizard(func, inputs):
    """Call a wizard function with patched input() and captured stdout."""
    with (
        patch("builtins.input", side_effect=iter(inputs)),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        return func()


class AskLanguagesTests(unittest.TestCase):
    """Step 3d: languages prompt — selection, version + manager per language."""

    def test_single_language_default_manager_omits_field(self):
        # Empty input for manager == accept default == omit the field.
        result = _run_wizard(start.ask_languages, ["python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_single_language_non_default_manager_stored(self):
        result = _run_wizard(start.ask_languages, ["python", "3.12", "uv"])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "uv"}})

    def test_explicit_default_manager_name_still_omits_field(self):
        # User types "pip" — it is the default; field is not stored.
        result = _run_wizard(start.ask_languages, ["python", "3.12", "pip"])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_multiple_languages_comma_separated(self):
        result = _run_wizard(
            start.ask_languages,
            ["python, node", "3.12", "uv", "22", ""],
        )
        self.assertEqual(
            result,
            {
                "python": {"version": "3.12", "manager": "uv"},
                "node": {"version": "22"},
            },
        )

    def test_unknown_language_reprompts(self):
        result = _run_wizard(start.ask_languages, ["cobol", "python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_empty_selection_reprompts(self):
        result = _run_wizard(start.ask_languages, ["", "python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_version_latest_is_rejected(self):
        result = _run_wizard(start.ask_languages, ["python", "latest", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_empty_version_is_rejected(self):
        result = _run_wizard(start.ask_languages, ["python", "", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12"}})

    def test_unknown_manager_reprompts(self):
        result = _run_wizard(start.ask_languages, ["python", "3.12", "pixi", "uv"])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "uv"}})

    def test_rust_single_manager_skips_manager_prompt(self):
        # Rust's only manager is cargo — no prompt, exactly two inputs total.
        result = _run_wizard(start.ask_languages, ["rust", "1.75"])
        self.assertEqual(result, {"rust": {"version": "1.75"}})


class AskOsPackagesTests(unittest.TestCase):
    """Step 3d: OS packages are optional; accept comma or whitespace separated."""

    def test_empty_returns_empty_list(self):
        result = _run_wizard(start.ask_os_packages, [""])
        self.assertEqual(result, [])

    def test_comma_separated(self):
        result = _run_wizard(start.ask_os_packages, ["build-essential, libpq-dev"])
        self.assertEqual(result, ["build-essential", "libpq-dev"])

    def test_space_separated(self):
        result = _run_wizard(start.ask_os_packages, ["build-essential libpq-dev ffmpeg"])
        self.assertEqual(result, ["build-essential", "libpq-dev", "ffmpeg"])


class AskStartupCommandsTests(unittest.TestCase):
    """Step 3d: startup commands — one per line, empty line finishes."""

    def test_empty_returns_empty_list(self):
        result = _run_wizard(start.ask_startup_commands, [""])
        self.assertEqual(result, [])

    def test_multiple_commands_end_on_empty_line(self):
        result = _run_wizard(
            start.ask_startup_commands,
            ["uv sync", "npm install", ""],
        )
        self.assertEqual(result, ["uv sync", "npm install"])


class AskCodingEnvironmentTests(unittest.TestCase):
    """Step 3d: orchestrator produces a dict shaped for coding-environment.toml."""

    def test_happy_path_multi_language_with_os_and_startup(self):
        inputs = [
            "python,node",  # languages
            "3.12",
            "uv",  # python: version + manager
            "22",
            "",  # node: version + default manager
            "build-essential libpq-dev",  # os packages
            "uv sync",
            "npm install",
            "",  # startup commands
        ]
        result = _run_wizard(start.ask_coding_environment, inputs)
        self.assertEqual(
            result,
            {
                "os": {"packages": ["build-essential", "libpq-dev"]},
                "languages": {
                    "python": {"version": "3.12", "manager": "uv"},
                    "node": {"version": "22"},
                },
                "startup": {"commands": ["uv sync", "npm install"]},
            },
        )

    def test_minimal_python_only_omits_empty_sections(self):
        inputs = ["python", "3.12", "", "", ""]
        result = _run_wizard(start.ask_coding_environment, inputs)
        self.assertEqual(result, {"languages": {"python": {"version": "3.12"}}})

    def test_sections_ordered_os_languages_startup(self):
        # All three sections non-empty; insertion order must be canonical.
        inputs = [
            "python",
            "3.12",
            "",  # python, default manager
            "libpq-dev",  # os
            "uv sync",
            "",  # startup
        ]
        result = _run_wizard(start.ask_coding_environment, inputs)
        self.assertEqual(list(result), ["os", "languages", "startup"])


class WriteCodingEnvironmentTomlTests(unittest.TestCase):
    """Step 3e: coding-environment.toml writer — agent-visible, zero branding."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_default_filename_when_no_collision(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        path = start.write_coding_environment_toml(self.project_dir, data)
        self.assertEqual(path, self.project_dir / "coding-environment.toml")
        self.assertTrue(path.is_file())

    def test_generated_file_is_valid_toml_and_round_trips(self):
        data = {
            "os": {"packages": ["build-essential", "libpq-dev"]},
            "languages": {
                "python": {"version": "3.12", "manager": "uv"},
                "node": {"version": "22"},
            },
            "startup": {"commands": ["uv sync", "npm install"]},
        }
        path = start.write_coding_environment_toml(self.project_dir, data)
        with open(path, "rb") as f:
            parsed = tomllib.load(f)
        self.assertEqual(parsed["os"]["packages"], ["build-essential", "libpq-dev"])
        self.assertEqual(parsed["languages"]["python"]["version"], "3.12")
        self.assertEqual(parsed["languages"]["python"]["manager"], "uv")
        self.assertEqual(parsed["languages"]["node"]["version"], "22")
        self.assertNotIn("manager", parsed["languages"]["node"])
        self.assertEqual(parsed["startup"]["commands"], ["uv sync", "npm install"])

    def test_zero_alcatrazer_branding_in_output(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        path = start.write_coding_environment_toml(self.project_dir, data)
        self.assertNotIn("alcatraz", path.read_text().lower())

    def test_omits_absent_sections(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        self.assertNotIn("[os]", content)
        self.assertNotIn("[startup]", content)

    def test_collision_uses_hex_suffix(self):
        existing = self.project_dir / "coding-environment.toml"
        existing.write_text("# user's existing file — do not clobber\n")
        data = {"languages": {"python": {"version": "3.12"}}}
        with patch.object(start.secrets, "token_hex", return_value="a3f7"):
            path = start.write_coding_environment_toml(self.project_dir, data)
        self.assertEqual(path, self.project_dir / "coding-environment-a3f7.toml")
        self.assertEqual(existing.read_text(), "# user's existing file — do not clobber\n")


class WriteAlcatrazerConfigTests(unittest.TestCase):
    """Step 3e: .alcatrazer/config.toml writer — daemon defaults stay in template."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _parse(self, path: Path) -> dict:
        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_writes_to_alcatrazer_slash_config_toml(self):
        path = start.write_alcatrazer_config(self.project_dir, "Alice", "alice@example.com")
        self.assertEqual(path, self.project_dir / ".alcatrazer" / "config.toml")
        self.assertTrue(path.is_file())

    def test_creates_alcatrazer_dir_if_missing(self):
        alcatrazer_dir = self.project_dir / ".alcatrazer"
        self.assertFalse(alcatrazer_dir.exists())
        start.write_alcatrazer_config(self.project_dir, "A", "a@e")
        self.assertTrue(alcatrazer_dir.is_dir())

    def test_promotion_identity_is_written(self):
        path = start.write_alcatrazer_config(
            self.project_dir, "Alice Smith", "alice.smith@example.com"
        )
        data = self._parse(path)
        self.assertEqual(data["promotion"]["name"], "Alice Smith")
        self.assertEqual(data["promotion"]["email"], "alice.smith@example.com")

    def test_daemon_defaults_preserved_from_template(self):
        path = start.write_alcatrazer_config(self.project_dir, "A", "a@e")
        daemon = self._parse(path)["promotion-daemon"]
        self.assertEqual(daemon["interval"], 5)
        self.assertEqual(daemon["branches"], "all")
        self.assertEqual(daemon["mode"], "mirror")
        self.assertEqual(daemon["verbosity"], "normal")
        self.assertEqual(daemon["max_log_size"], 512)

    def test_coding_environment_file_defaults_to_standard_name(self):
        path = start.write_alcatrazer_config(self.project_dir, "A", "a@e")
        self.assertEqual(self._parse(path)["coding_environment_file"], "coding-environment.toml")

    def test_coding_environment_file_custom_name_written(self):
        path = start.write_alcatrazer_config(
            self.project_dir,
            "A",
            "a@e",
            coding_env_file="coding-environment-a3f7.toml",
        )
        self.assertEqual(
            self._parse(path)["coding_environment_file"],
            "coding-environment-a3f7.toml",
        )

    def test_name_with_quotes_is_escaped(self):
        path = start.write_alcatrazer_config(self.project_dir, 'A "Quoted" Person', "a@e")
        self.assertEqual(self._parse(path)["promotion"]["name"], 'A "Quoted" Person')


class WriteEnvExampleTests(unittest.TestCase):
    """Step 3e: .env.example writer — agent-visible, zero branding, skip if present."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_env_example_at_repo_root(self):
        path = start.write_env_example(self.project_dir)
        self.assertEqual(path, self.project_dir / ".env.example")
        self.assertTrue(path.is_file())

    def test_zero_alcatrazer_branding(self):
        path = start.write_env_example(self.project_dir)
        self.assertNotIn("alcatraz", path.read_text().lower())

    def test_skips_and_returns_none_when_file_exists(self):
        existing = self.project_dir / ".env.example"
        existing.write_text("USER_VAR=1\n")
        result = start.write_env_example(self.project_dir)
        self.assertIsNone(result)
        self.assertEqual(existing.read_text(), "USER_VAR=1\n")


class WriteGitExcludeTests(unittest.TestCase):
    """Step 3f: append alcatrazer + workspace patterns to .git/info/exclude."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.project_dir / ".git" / "info").mkdir(parents=True)

    def _content(self) -> str:
        return (self.project_dir / ".git" / "info" / "exclude").read_text()

    def test_returns_path_to_exclude_file(self):
        path = start.write_git_exclude(self.project_dir, ".devspace-abcd")
        self.assertEqual(path, self.project_dir / ".git" / "info" / "exclude")
        self.assertTrue(path.is_file())

    def test_writes_both_patterns(self):
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        lines = self._content().splitlines()
        self.assertIn(".alcatrazer/", lines)
        self.assertIn(".devspace-abcd/", lines)

    def test_includes_header_comment_verbatim_from_doc(self):
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        self.assertIn(
            "# alcatrazer patterns (written by alcatrazer start)",
            self._content(),
        )

    def test_preserves_existing_user_content(self):
        existing = self.project_dir / ".git" / "info" / "exclude"
        existing.write_text("# user's exclude\n*.log\nbuild/\n")
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        content = self._content()
        self.assertIn("# user's exclude", content)
        self.assertIn("*.log", content)
        self.assertIn("build/", content)
        self.assertIn(".alcatrazer/", content)
        self.assertIn(".devspace-abcd/", content)

    def test_idempotent_when_patterns_already_present(self):
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        first = self._content()
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        second = self._content()
        self.assertEqual(first, second)
        self.assertEqual(second.count(".alcatrazer/"), 1)
        self.assertEqual(second.count(".devspace-abcd/"), 1)

    def test_creates_info_dir_when_missing(self):
        # .git exists but .git/info does not — must create it.
        import shutil

        shutil.rmtree(self.project_dir / ".git" / "info")
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        self.assertTrue((self.project_dir / ".git" / "info" / "exclude").is_file())

    def test_workspace_name_used_verbatim_no_double_dot(self):
        # Contract: caller passes the full directory name (usually with a
        # leading dot from identity.generate_workspace_dir_name()).
        start.write_git_exclude(self.project_dir, ".devspace-abcd")
        self.assertNotIn("..devspace-abcd", self._content())


class Step3fCompositionTests(unittest.TestCase):
    """End-to-end sanity: identity helpers + write_git_exclude compose cleanly."""

    def test_generate_then_store_then_exclude(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project_dir = Path(tmp.name)
        alcatraz_dir = project_dir / ".alcatrazer"
        alcatraz_dir.mkdir()
        (project_dir / ".git" / "info").mkdir(parents=True)

        # 1. Generate a random workspace name (existing identity helper).
        name = identity.generate_workspace_dir_name()
        self.assertTrue(name.startswith("."))

        # 2. Persist the name (existing identity helper).
        identity.store_workspace_dir(str(alcatraz_dir), name)
        self.assertEqual((alcatraz_dir / "workspace-dir").read_text().strip(), name)

        # 3. Write the exclude patterns (new Step 3f helper).
        start.write_git_exclude(project_dir, name)
        content = (project_dir / ".git" / "info" / "exclude").read_text()
        self.assertIn(".alcatrazer/", content)
        self.assertIn(f"{name}/", content)


class ExtractPackageSourceTests(unittest.TestCase):
    """Step 3g: copy the real alcatrazer package tree, bit-exact, minus
    compiled artifacts.

    Strategy: snapshot the real src/alcatrazer/ into a temp dir (same ignore
    rules the code under test uses), hash every file, run extract, hash the
    destination tree, require equality. This catches missing files, wrong
    content, and broken ignore rules in one assertion.
    """

    REAL_SRC = Path(start.__file__).parent
    IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")

    @classmethod
    def _snapshot_real_source(cls, dest: Path) -> None:
        shutil.copytree(cls.REAL_SRC, dest, ignore=shutil.ignore_patterns(*cls.IGNORE_PATTERNS))

    @classmethod
    def _tree_signature(cls, root: Path) -> dict[str, str]:
        """Return {relative_path: sha256_hex} for every file under root."""
        sig: dict[str, str] = {}
        for path in root.rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(root))
                sig[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return sig

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    @property
    def dest(self) -> Path:
        return self.project_dir / ".alcatrazer" / "src" / "alcatrazer"

    def test_returns_destination_path(self):
        result = start.extract_package_source(self.project_dir)
        self.assertEqual(result, self.dest)
        self.assertTrue(self.dest.is_dir())

    def test_extracted_tree_matches_real_source_bit_exact(self):
        """Every file that should be copied IS copied, with identical bytes."""
        with tempfile.TemporaryDirectory() as ref_tmp:
            reference = Path(ref_tmp) / "alcatrazer"
            self._snapshot_real_source(reference)
            expected = self._tree_signature(reference)
        start.extract_package_source(self.project_dir)
        self.assertEqual(self._tree_signature(self.dest), expected)

    def test_skips_pycache_directory(self):
        """Even when the source contains __pycache__, the dest must not."""
        with tempfile.TemporaryDirectory() as src_tmp:
            src = Path(src_tmp) / "alcatrazer"
            self._snapshot_real_source(src)
            # Inject __pycache__ artifacts at multiple depths.
            (src / "__pycache__").mkdir()
            (src / "__pycache__" / "start.cpython-312.pyc").write_bytes(b"bc")
            nested = src / "tests" / "__pycache__"
            nested.mkdir(exist_ok=True)
            (nested / "t.pyc").write_bytes(b"bc")
            start.extract_package_source(self.project_dir, source_dir=src)
        self.assertEqual(list(self.dest.rglob("__pycache__")), [])

    def test_skips_pyc_and_pyo_files(self):
        with tempfile.TemporaryDirectory() as src_tmp:
            src = Path(src_tmp) / "alcatrazer"
            self._snapshot_real_source(src)
            (src / "cli.pyc").write_bytes(b"bc")
            (src / "cli.pyo").write_bytes(b"bc")
            start.extract_package_source(self.project_dir, source_dir=src)
        self.assertEqual(list(self.dest.rglob("*.pyc")), [])
        self.assertEqual(list(self.dest.rglob("*.pyo")), [])

    def test_replaces_existing_destination_with_clean_slate(self):
        """Simulates an upgrade: an "old version" already sits at the dest with
        a different file tree and different content. After extraction, the tree
        and per-file checksums must match the real source — proving both that
        stale files were removed and that new files were installed correctly."""
        # Lay down a fake "old version" at dest.
        self.dest.parent.mkdir(parents=True)
        self.dest.mkdir()
        (self.dest / "old_file_removed_upstream.py").write_text("ancient\n")
        (self.dest / "cli.py").write_text("different content from upstream\n")
        (self.dest / "templates").mkdir()
        (self.dest / "templates" / "stale_template.toml").write_text("x = 0\n")
        old_sig = self._tree_signature(self.dest)

        # Build the reference (what dest should look like after upgrade).
        with tempfile.TemporaryDirectory() as ref_tmp:
            reference = Path(ref_tmp) / "alcatrazer"
            self._snapshot_real_source(reference)
            expected = self._tree_signature(reference)

        # Precondition: the old version really is different — otherwise the
        # test would pass trivially.
        self.assertNotEqual(old_sig, expected)

        start.extract_package_source(self.project_dir)

        self.assertEqual(self._tree_signature(self.dest), expected)


class CreateWorkspaceTests(unittest.TestCase):
    """Step 3j: create the inner workspace — dir, git repo, agent identity,
    flat snapshot, local git config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # Isolate git config so the host user's global config doesn't leak in.
        self.fake_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.fake_home.cleanup)
        env_patch = patch.dict(
            os.environ,
            {
                "HOME": self.fake_home.name,
                "XDG_CONFIG_HOME": self.fake_home.name,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

        # Build a minimal outer git repo with two files on `main`.
        self._outer_git("init", "-b", "main")
        self._outer_git("config", "user.name", "Outer Dev")
        self._outer_git("config", "user.email", "outer@example.com")
        (self.project_dir / "README.md").write_text("# Outer repo\n")
        (self.project_dir / "main.py").write_text("print('hi')\n")
        self._outer_git("add", "-A")
        self._outer_git("commit", "-m", "first")

    def _outer_git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.project_dir), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def _inner_git(self, workspace_dir: Path, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(workspace_dir), *args],
            capture_output=True,
            text=True,
        )

    def test_creates_workspace_dir_at_project_root(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        self.assertEqual(ws, self.project_dir / ".devspace-abcd")
        self.assertTrue(ws.is_dir())

    def test_initializes_inner_git_repo(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        self.assertTrue((ws / ".git").is_dir())

    def test_snapshot_contains_outer_tracked_files(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        self.assertEqual((ws / "README.md").read_text(), "# Outer repo\n")
        self.assertEqual((ws / "main.py").read_text(), "print('hi')\n")

    def test_snapshot_is_a_single_commit(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        count = self._inner_git(ws, "rev-list", "--count", "HEAD").stdout.strip()
        self.assertEqual(count, "1")

    def test_snapshot_history_diverges_from_outer(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        outer_sha = self._outer_git("rev-parse", "HEAD").stdout.strip()
        inner_sha = self._inner_git(ws, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(outer_sha, inner_sha)

    def test_agent_identity_persisted_to_alcatrazer_dir(self):
        start.create_workspace(self.project_dir, ".devspace-abcd")
        identity_file = self.project_dir / ".alcatrazer" / "agent-identity"
        self.assertTrue(identity_file.is_file())
        lines = identity_file.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(lines[0].split()), 2)  # "First Last"
        self.assertIn("@", lines[1])

    def test_inner_git_uses_agent_identity(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        identity_file = self.project_dir / ".alcatrazer" / "agent-identity"
        stored_name, stored_email = identity_file.read_text().strip().split("\n")
        inner_name = self._inner_git(ws, "config", "--local", "user.name").stdout.strip()
        inner_email = self._inner_git(ws, "config", "--local", "user.email").stdout.strip()
        self.assertEqual(inner_name, stored_name)
        self.assertEqual(inner_email, stored_email)

    def test_inner_git_disables_commit_signing(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        result = self._inner_git(ws, "config", "--local", "commit.gpgsign")
        self.assertEqual(result.stdout.strip(), "false")

    def test_inner_git_has_no_remote(self):
        ws = start.create_workspace(self.project_dir, ".devspace-abcd")
        result = self._inner_git(ws, "remote")
        self.assertEqual(result.stdout.strip(), "")


class WritePythonSymlinkTests(unittest.TestCase):
    """Step 3i: .alcatrazer/python is a symlink to sys.executable so the
    post-install daemon can find the host's resolved Python."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_symlink_to_alcatrazer_python(self):
        path = start.write_python_symlink(self.project_dir)
        self.assertEqual(path, self.project_dir / ".alcatrazer" / "python")
        self.assertTrue(path.is_symlink())

    def test_symlink_targets_sys_executable(self):
        path = start.write_python_symlink(self.project_dir)
        self.assertEqual(str(path.readlink()), sys.executable)

    def test_creates_alcatrazer_dir_if_missing(self):
        self.assertFalse((self.project_dir / ".alcatrazer").exists())
        start.write_python_symlink(self.project_dir)
        self.assertTrue((self.project_dir / ".alcatrazer").is_dir())

    def test_overwrites_existing_symlink(self):
        alcatraz = self.project_dir / ".alcatrazer"
        alcatraz.mkdir()
        stale = alcatraz / "python"
        stale.symlink_to("/nonexistent/old/python")
        path = start.write_python_symlink(self.project_dir)
        self.assertEqual(str(path.readlink()), sys.executable)


class RunStartupCommandsTests(unittest.TestCase):
    """Step 3k: orchestrate [startup] commands via prison.exec, fail-fast."""

    def _run(self, prison, commands):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.run_startup_commands(prison, commands)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_empty_command_list_returns_zero_without_invoking_prison(self):
        prison = Mock(spec=Alcatraz)
        rc, _, _ = self._run(prison, [])
        self.assertEqual(rc, 0)
        prison.exec.assert_not_called()

    def test_all_succeed_returns_zero(self):
        prison = Mock(spec=Alcatraz)
        prison.exec.return_value = 0
        rc, _, _ = self._run(prison, ["uv sync", "npm install"])
        self.assertEqual(rc, 0)
        self.assertEqual(prison.exec.call_count, 2)

    def test_each_command_wrapped_as_bash_c(self):
        prison = Mock(spec=Alcatraz)
        prison.exec.return_value = 0
        self._run(prison, ["uv sync"])
        prison.exec.assert_called_once_with(["bash", "-c", "uv sync"])

    def test_fail_fast_on_first_non_zero(self):
        prison = Mock(spec=Alcatraz)
        prison.exec.side_effect = [0, 7, 0]  # second fails
        rc, _, _ = self._run(prison, ["a", "b", "c"])
        self.assertEqual(rc, 7)
        self.assertEqual(prison.exec.call_count, 2)  # third never runs

    def test_error_output_identifies_command_index_and_text(self):
        prison = Mock(spec=Alcatraz)
        prison.exec.side_effect = [0, 1]
        _, _, err = self._run(prison, ["uv sync", "npm install"])
        self.assertIn("#2", err)
        self.assertIn("npm install", err)


class SaveCodingEnvironmentSnapshotTests(unittest.TestCase):
    """Step 3k: copy current coding-environment file to
    .alcatrazer/coding-environment.toml.last — the .last record Step 4
    diffs against for change detection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()

    def _write_config(self, filename: str) -> None:
        (self.alcatraz_dir / "config.toml").write_text(
            f'coding_environment_file = "{filename}"\n[promotion]\nname = "x"\nemail = "y"\n'
        )

    def test_copies_default_filename(self):
        self._write_config("coding-environment.toml")
        src = self.project_dir / "coding-environment.toml"
        src.write_text("[os]\npackages = []\n")
        target = start.save_coding_environment_snapshot(self.project_dir)
        self.assertEqual(target, self.alcatraz_dir / "coding-environment.toml.last")
        self.assertEqual(target.read_text(), src.read_text())

    def test_copies_hex_suffixed_filename(self):
        self._write_config("coding-environment-a3f7.toml")
        src = self.project_dir / "coding-environment-a3f7.toml"
        src.write_text("[os]\npackages = ['libpq-dev']\n")
        target = start.save_coding_environment_snapshot(self.project_dir)
        # Target name is always canonical, regardless of source filename.
        self.assertEqual(target, self.alcatraz_dir / "coding-environment.toml.last")
        self.assertEqual(target.read_text(), src.read_text())

    def test_target_is_alcatrazer_coding_environment_toml_last(self):
        self._write_config("coding-environment.toml")
        (self.project_dir / "coding-environment.toml").write_text("x = 1\n")
        target = start.save_coding_environment_snapshot(self.project_dir)
        self.assertTrue(target.is_file())
        self.assertEqual(target.name, "coding-environment.toml.last")


class CodingEnvironmentChangedTests(unittest.TestCase):
    """Step 4: compare current coding-environment.toml against the .last snapshot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()

    def _write_config(self, filename: str) -> None:
        (self.alcatraz_dir / "config.toml").write_text(
            f'coding_environment_file = "{filename}"\n[promotion]\nname = "x"\nemail = "y"\n'
        )

    def test_true_when_last_snapshot_missing(self):
        self._write_config("coding-environment.toml")
        (self.project_dir / "coding-environment.toml").write_text("x = 1\n")
        self.assertTrue(start.coding_environment_changed(self.project_dir))

    def test_true_when_contents_differ(self):
        self._write_config("coding-environment.toml")
        current = self.project_dir / "coding-environment.toml"
        current.write_text("x = 1\n")
        (self.alcatraz_dir / "coding-environment.toml.last").write_text("x = 2\n")
        self.assertTrue(start.coding_environment_changed(self.project_dir))

    def test_false_when_contents_identical(self):
        self._write_config("coding-environment.toml")
        current = self.project_dir / "coding-environment.toml"
        current.write_text("x = 1\n")
        (self.alcatraz_dir / "coding-environment.toml.last").write_text("x = 1\n")
        self.assertFalse(start.coding_environment_changed(self.project_dir))

    def test_uses_hex_suffixed_filename_from_config(self):
        self._write_config("coding-environment-a3f7.toml")
        (self.project_dir / "coding-environment-a3f7.toml").write_text("x = 1\n")
        (self.alcatraz_dir / "coding-environment.toml.last").write_text("x = 1\n")
        self.assertFalse(start.coding_environment_changed(self.project_dir))


class SubsequentRunTests(unittest.TestCase):
    """Step 4: _subsequent_run detection logic — branches on needs_rebuild,
    is_running, and coding_environment_changed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
            '[promotion]\nname = "x"\nemail = "y"\n'
        )
        (self.project_dir / "coding-environment.toml").write_text(
            '[languages.python]\nversion = "3.12"\n[startup]\ncommands = ["uv sync"]\n'
        )

    def _prison(self, running=False, rebuild=False, exec_rc=0):
        p = Mock(spec=Alcatraz)
        p.is_running.return_value = running
        p.needs_rebuild.return_value = rebuild
        p.exec.return_value = exec_rc
        return p

    def _seed_last(self, match: bool) -> None:
        """Write coding-environment.toml.last matching (or drifting from) current."""
        content = (self.project_dir / "coding-environment.toml").read_text()
        if not match:
            content += "\n# drift\n"
        (self.alcatraz_dir / "coding-environment.toml.last").write_text(content)

    def _run(self, prison) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start._subsequent_run(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_fast_path_when_running_and_nothing_changed(self):
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=False)
        rc, out, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.start.assert_not_called()
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        prison.exec.assert_not_called()
        self.assertIn("up to date", out.lower())

    def test_full_rebuild_when_dockerfile_would_differ_and_running(self):
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=True)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.generate_prison.assert_called_once()
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        prison.build.assert_called_once()
        prison.start.assert_called_once()
        prison.exec.assert_called()

    def test_rebuild_when_stopped_skips_stop(self):
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=True)
        self._run(prison)
        prison.stop.assert_not_called()
        prison.remove.assert_called_once()
        prison.build.assert_called_once()
        prison.start.assert_called_once()

    def test_restart_when_only_toml_changed(self):
        self._seed_last(match=False)
        prison = self._prison(running=True, rebuild=False)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        # Not a rebuild: no generate_prison, no build
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        # But must cycle the container to re-run startup
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        prison.start.assert_called_once()
        prison.exec.assert_called()

    def test_start_when_stopped_and_unchanged(self):
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=False)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.stop.assert_not_called()
        prison.start.assert_called_once()

    def test_snapshot_updated_on_success(self):
        self._seed_last(match=False)  # initially drifted
        prison = self._prison(running=True, rebuild=False)
        self._run(prison)
        self.assertEqual(
            (self.alcatraz_dir / "coding-environment.toml.last").read_text(),
            (self.project_dir / "coding-environment.toml").read_text(),
        )

    def test_startup_failure_returns_nonzero_and_skips_snapshot_save(self):
        self._seed_last(match=False)
        original_last = (self.alcatraz_dir / "coding-environment.toml.last").read_text()
        prison = self._prison(running=True, rebuild=False, exec_rc=7)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 7)
        # .last must not be refreshed when startup failed
        self.assertEqual(
            (self.alcatraz_dir / "coding-environment.toml.last").read_text(),
            original_last,
        )


class CmdInitIntegrationTests(unittest.TestCase):
    """End-to-end orchestration of cmd_init (Steps 3b-3h) — wizards + config
    writers + recipe generation run; build / workspace creation / container
    start do NOT (they're `alcatrazer start`'s job)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        (self.project_dir / ".git").mkdir()
        self.addCleanup(self.tmp.cleanup)

        self.prison = Mock(spec=Alcatraz)

        self.mocks: dict[str, Mock] = {}
        to_patch: list[tuple[object, str, object]] = [
            (start, "ask_promotion_identity", ("Alice", "alice@example.com")),
            (
                start,
                "ask_coding_environment",
                {
                    "languages": {"python": {"version": "3.12"}},
                    "startup": {"commands": ["uv sync"]},
                },
            ),
            (
                start,
                "write_coding_environment_toml",
                self.project_dir / "coding-environment.toml",
            ),
            (start, "write_alcatrazer_config", None),
            (start, "write_env_example", None),
            (start, "write_git_exclude", None),
            (start, "extract_package_source", None),
            (start, "write_python_symlink", None),
            (identity, "generate_workspace_dir_name", ".devspace-abcd"),
            (identity, "store_workspace_dir", None),
        ]
        for mod, name, rv in to_patch:
            p = patch.object(mod, name, return_value=rv)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def _run(self) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return start.cmd_init(self.project_dir, prison=self.prison)

    def test_happy_path_returns_zero(self):
        self.assertEqual(self._run(), 0)

    def test_proceeds_when_git_is_file_worktree(self):
        """.git as a file (git worktrees / submodules) counts as a valid repo."""
        shutil.rmtree(self.project_dir / ".git")
        (self.project_dir / ".git").write_text("gitdir: /some/path/.git\n")
        self.assertEqual(self._run(), 0)

    def test_errors_when_alcatrazer_dir_already_exists(self):
        (self.project_dir / ".alcatrazer").mkdir()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_init(self.project_dir, prison=self.prison)
        self.assertNotEqual(rc, 0)
        self.assertIn("already", stderr.getvalue().lower())
        # No wizards should have run
        self.mocks["ask_promotion_identity"].assert_not_called()
        self.mocks["ask_coding_environment"].assert_not_called()

    def test_every_init_helper_is_called(self):
        self._run()
        for name, mock in self.mocks.items():
            self.assertGreaterEqual(mock.call_count, 1, f"{name} should have been called")
        self.prison.generate_prison.assert_called_once()

    def test_build_and_start_are_not_called(self):
        """Init stops before docker build — alcatrazer start does that."""
        self._run()
        self.prison.build.assert_not_called()
        self.prison.start.assert_not_called()

    def test_promotion_identity_flows_into_alcatrazer_config_writer(self):
        self.mocks["ask_promotion_identity"].return_value = (
            "Bob Builder",
            "bob@example.com",
        )
        self._run()
        call = self.mocks["write_alcatrazer_config"].call_args
        self.assertEqual(call.args[1], "Bob Builder")
        self.assertEqual(call.args[2], "bob@example.com")

    def test_coding_environment_flows_into_prison_generate_prison(self):
        coding_env = {
            "os": {"packages": ["libpq-dev"]},
            "languages": {"rust": {"version": "1.75"}},
        }
        self.mocks["ask_coding_environment"].return_value = coding_env
        self._run()
        self.prison.generate_prison.assert_called_once_with(coding_env)

    def test_workspace_name_flows_into_exclude(self):
        self.mocks["generate_workspace_dir_name"].return_value = ".devspace-zzzz"
        self._run()
        exclude_call = self.mocks["write_git_exclude"].call_args
        self.assertEqual(exclude_call.args[1], ".devspace-zzzz")


class FirstRunAfterInitTests(unittest.TestCase):
    """Orchestration of _first_run_after_init (Step 3.5): build → workspace
    snapshot → container start → startup commands → save .last snapshot.
    Wizards and config writers are NOT re-run — init already produced them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
            '[promotion]\nname = "x"\nemail = "y"\n'
        )
        (self.project_dir / "coding-environment.toml").write_text(
            '[languages.python]\nversion = "3.12"\n[startup]\ncommands = ["uv sync"]\n'
        )
        self.addCleanup(self.tmp.cleanup)

        self.prison = Mock(spec=Alcatraz)
        self.prison.exec.return_value = 0

        self.mocks: dict[str, Mock] = {}
        to_patch: list[tuple[object, str, object]] = [
            (start, "create_workspace", None),
            (start, "run_startup_commands", 0),
            (start, "save_coding_environment_snapshot", None),
            (identity, "load_workspace_dir", ".devspace-abcd"),
        ]
        for mod, name, rv in to_patch:
            p = patch.object(mod, name, return_value=rv)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def _run(self) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return start._first_run_after_init(self.project_dir, prison=self.prison)

    def test_happy_path_returns_zero(self):
        self.assertEqual(self._run(), 0)

    def test_build_runs_before_workspace_and_start(self):
        parent = Mock()
        parent.attach_mock(self.prison.build, "build")
        parent.attach_mock(self.mocks["create_workspace"], "create_workspace")
        parent.attach_mock(self.prison.start, "start")
        self._run()
        names = [c[0] for c in parent.mock_calls]
        self.assertLess(names.index("build"), names.index("create_workspace"))
        self.assertLess(names.index("create_workspace"), names.index("start"))

    def test_does_not_re_run_wizards_or_writers(self):
        """cmd_init already produced config + Dockerfile; don't touch them."""
        with (
            patch.object(start, "ask_promotion_identity") as ask_id,
            patch.object(start, "ask_coding_environment") as ask_env,
            patch.object(start, "write_coding_environment_toml") as w_ce,
            patch.object(start, "write_alcatrazer_config") as w_cfg,
            patch.object(start, "write_env_example") as w_env,
            patch.object(start, "extract_package_source") as w_src,
        ):
            self._run()
        ask_id.assert_not_called()
        ask_env.assert_not_called()
        w_ce.assert_not_called()
        w_cfg.assert_not_called()
        w_env.assert_not_called()
        w_src.assert_not_called()
        self.prison.generate_prison.assert_not_called()

    def test_workspace_name_loaded_from_identity(self):
        self.mocks["load_workspace_dir"].return_value = ".devspace-zzzz"
        self._run()
        workspace_call = self.mocks["create_workspace"].call_args
        self.assertEqual(workspace_call.args[1], ".devspace-zzzz")

    def test_startup_failure_returns_nonzero_and_skips_snapshot(self):
        self.mocks["run_startup_commands"].return_value = 7
        rc = self._run()
        self.assertEqual(rc, 7)
        self.mocks["save_coding_environment_snapshot"].assert_not_called()

    def test_build_failure_reports_and_returns_nonzero(self):
        self.prison.build.side_effect = PrisonBuildError(
            "docker build failed",
            stdout="build stdout",
            stderr="build stderr",
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = start._first_run_after_init(self.project_dir, prison=self.prison)
        self.assertNotEqual(rc, 0)
        err = stderr.getvalue()
        self.assertIn("build", err.lower())
        self.assertIn("build stderr", err)
        self.prison.start.assert_not_called()
        self.mocks["run_startup_commands"].assert_not_called()
        self.mocks["save_coding_environment_snapshot"].assert_not_called()


class CmdStopTests(unittest.TestCase):
    """Step 5: `alcatrazer stop` — idempotent container stop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, prison=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_stop(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_returns_error_when_no_alcatrazer_setup(self):
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("alcatrazer", err.lower())

    def test_noop_when_container_not_running(self):
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = False
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_not_called()
        self.assertIn("not running", out.lower())

    def test_stops_running_container(self):
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_called_once()
        self.assertIn("stopped", out.lower())


class CliStopTests(unittest.TestCase):
    """Step 5: `alcatrazer stop` CLI wiring."""

    def test_cli_stop_command_invokes_cmd_stop(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "stop"]),
            patch.object(start, "cmd_stop", return_value=0) as mock_stop,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_stop.assert_called_once()
        self.assertEqual(cm.exception.code, 0)

    def test_cli_stop_propagates_nonzero_exit(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "stop"]),
            patch.object(start, "cmd_stop", return_value=1),
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        self.assertEqual(cm.exception.code, 1)


class CliInitTests(unittest.TestCase):
    """`alcatrazer init` subcommand — dispatches to `start.cmd_init`."""

    def test_cli_init_command_invokes_cmd_init(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "init"]),
            patch.object(start, "cmd_init", return_value=0) as mock_init,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_init.assert_called_once()
        self.assertEqual(cm.exception.code, 0)

    def test_cli_init_propagates_nonzero_exit(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "init"]),
            patch.object(start, "cmd_init", return_value=2),
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        self.assertEqual(cm.exception.code, 2)


class CmdSelftestTests(unittest.TestCase):
    """`alcatrazer.start.cmd_selftest(project_dir)` builds the factory's
    TestCase against the running Alcatraz and runs it, returning 0 on
    success or non-zero on failure."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _build_fake_testcase(self) -> type[unittest.TestCase]:
        """Return a trivial TestCase subclass we can hand to the factory mock."""

        class Fake(unittest.TestCase):
            def test_nothing(self_):
                pass

        return Fake

    def test_invokes_factory_with_project_dir(self):
        fake = self._build_fake_testcase()
        with (
            patch.object(selftest, "make_alcatraz_selftest_testcase", return_value=fake) as factory,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            start.cmd_selftest(self.project_dir)
        factory.assert_called_once_with(self.project_dir)

    def test_returns_zero_on_success(self):
        fake = self._build_fake_testcase()
        with (
            patch.object(selftest, "make_alcatraz_selftest_testcase", return_value=fake),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = start.cmd_selftest(self.project_dir)
        self.assertEqual(rc, 0)

    def test_returns_nonzero_on_failure(self):
        class Failing(unittest.TestCase):
            def test_always_fails(self_):
                self_.fail("intentional")

        with (
            patch.object(selftest, "make_alcatraz_selftest_testcase", return_value=Failing),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = start.cmd_selftest(self.project_dir)
        self.assertNotEqual(rc, 0)


class CliRunSelftestFlagTests(unittest.TestCase):
    """`alcatrazer start --run-selftest` runs cmd_start, then cmd_selftest —
    but only if cmd_start succeeded."""

    def test_runs_selftest_after_successful_start(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start", "--run-selftest"]),
            patch.object(start, "cmd_start", return_value=0) as mock_start,
            patch.object(start, "cmd_selftest", return_value=0) as mock_selftest,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_start.assert_called_once()
        mock_selftest.assert_called_once()
        self.assertEqual(cm.exception.code, 0)

    def test_skips_selftest_when_start_failed(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start", "--run-selftest"]),
            patch.object(start, "cmd_start", return_value=1),
            patch.object(start, "cmd_selftest") as mock_selftest,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_selftest.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_nonzero_selftest_propagates_to_exit(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start", "--run-selftest"]),
            patch.object(start, "cmd_start", return_value=0),
            patch.object(start, "cmd_selftest", return_value=3),
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        self.assertEqual(cm.exception.code, 3)

    def test_no_selftest_when_flag_absent(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "start"]),
            patch.object(start, "cmd_start", return_value=0),
            patch.object(start, "cmd_selftest") as mock_selftest,
            self.assertRaises(SystemExit),
        ):
            cli.main()
        mock_selftest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
