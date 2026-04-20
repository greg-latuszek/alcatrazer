"""Tests for the `alcatrazer start` CLI skeleton.

Step 3a — routing: `.alcatrazer/` presence decides first-time vs subsequent.
Step 3b — first-time flow aborts unless run at a git repo root.
Step 3c — promotion-identity read + interactive prompt helpers.
Step 3d — coding-environment wizard (languages, OS packages, startup).
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import cli, start

GIT_REPO_ROOT_ERROR = "alcatrazer must be run from a git repository root."


class StartRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_no_alcatrazer_dir_routes_to_first_time(self):
        with (
            patch.object(start, "_first_time_setup", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir)
        first.assert_called_once_with(self.project_dir)
        subsequent.assert_not_called()
        self.assertEqual(rc, 0)

    def test_alcatrazer_dir_present_routes_to_subsequent(self):
        (self.project_dir / ".alcatrazer").mkdir()
        with (
            patch.object(start, "_first_time_setup", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir)
        subsequent.assert_called_once_with(self.project_dir)
        first.assert_not_called()
        self.assertEqual(rc, 0)

    def test_routing_propagates_handler_return_code(self):
        with patch.object(start, "_first_time_setup", return_value=7):
            rc = start.cmd_start(self.project_dir)
        self.assertEqual(rc, 7)


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


class FirstTimeGitRepoCheckTests(unittest.TestCase):
    """Step 3b: first-time setup must refuse to run outside a git repo root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run_first_time(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start._first_time_setup(self.project_dir)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_aborts_when_not_a_git_repo(self):
        rc, _, err = self._run_first_time()
        self.assertNotEqual(rc, 0)
        self.assertIn(GIT_REPO_ROOT_ERROR, err)

    def test_proceeds_when_git_is_directory(self):
        (self.project_dir / ".git").mkdir()
        rc, _, err = self._run_first_time()
        self.assertEqual(rc, 0)
        self.assertNotIn(GIT_REPO_ROOT_ERROR, err)

    def test_proceeds_when_git_is_file_worktree(self):
        # In git worktrees and submodules, `.git` is a file pointing elsewhere.
        (self.project_dir / ".git").write_text("gitdir: /some/path/.git\n")
        rc, _, err = self._run_first_time()
        self.assertEqual(rc, 0)
        self.assertNotIn(GIT_REPO_ROOT_ERROR, err)


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
            set(start.SUPPORTED_LANGUAGES),
            {"python", "node", "rust", "go"},
        )

    def test_python_default_manager_is_pip_with_alternatives(self):
        py = start.SUPPORTED_LANGUAGES["python"]
        self.assertEqual(py["default_manager"], "pip")
        self.assertIn("uv", py["managers"])
        self.assertIn("poetry", py["managers"])

    def test_node_default_manager_is_npm_with_alternatives(self):
        node = start.SUPPORTED_LANGUAGES["node"]
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


if __name__ == "__main__":
    unittest.main()
