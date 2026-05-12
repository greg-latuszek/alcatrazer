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
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from alcatrazer import __version__, cli, identity, languages, selftest, start, state
from alcatrazer import status as status_mod
from alcatrazer.alcatraz import Alcatraz, PrisonBuildError
from alcatrazer.daemon_lifecycle import ShutdownResult

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
    """cmd_start routes to first-run when EITHER the image is stale
    (Phase 1.2.6: image_matches=False, includes "image missing" and
    "image built from older config") OR the Alcatraz workspace is
    missing. Image and workspace have independent lifetimes so
    routing on just one signal misses legitimate recovery scenarios."""

    WORKSPACE_NAME = ".devspace-routing"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "workspace-dir").write_text(self.WORKSPACE_NAME + "\n")
        # Phase 1.2.6: cmd_start now loads coding-environment.toml to
        # compute the recipe hash. Provide minimal valid contents.
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
        )
        (self.project_dir / "coding-environment.toml").write_text(
            '[languages.python]\nversion = "3.12"\nmanager = "pip"\n'
        )
        self.addCleanup(self.tmp.cleanup)

    def _make_workspace_ready(self) -> None:
        """Create the bind-mount target + an inner `.git` so the
        workspace-readiness check passes."""
        workspace = self.project_dir / self.WORKSPACE_NAME
        (workspace / ".git").mkdir(parents=True)

    def _prison(self, image_matches: bool) -> Mock:
        # Phase 1.2.6: routing now consults image_matches (was image_exists).
        # recipe_hash is also called by cmd_start to know what to expect.
        p = Mock(spec=Alcatraz)
        p.recipe_hash.return_value = "expected_hash_123"
        p.image_matches.return_value = image_matches
        return p

    def test_stale_image_routes_to_first_run(self):
        """Primary first-run trigger: image needs (re)building. After
        Phase 1.2.6, "needs building" includes "image's config_hash
        label doesn't match current recipe" — covers the user-reported
        bug where `rm -rf .alcatrazer/ + init` left an old image in
        place that today's `image_exists=True` check trusted blindly."""
        self._make_workspace_ready()
        prison = self._prison(image_matches=False)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        first.assert_called_once_with(self.project_dir, prison=prison)
        subsequent.assert_not_called()
        self.assertEqual(rc, 0)

    def test_missing_workspace_routes_to_first_run_even_when_image_matches(self):
        """Self-heal: user deleted `.devspace-xxx/` manually. Image is
        current but workspace doesn't exist — go to first-run so
        workspace gets recreated."""
        # Workspace dir NOT created → workspace_ready = False.
        prison = self._prison(image_matches=True)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        first.assert_called_once_with(self.project_dir, prison=prison)
        subsequent.assert_not_called()
        self.assertEqual(rc, 0)

    def test_missing_workspace_pointer_routes_to_first_run(self):
        """Pointer file itself missing — even weirder state, but same
        recovery path applies."""
        (self.alcatraz_dir / "workspace-dir").unlink()
        prison = self._prison(image_matches=True)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        first.assert_called_once_with(self.project_dir, prison=prison)
        subsequent.assert_not_called()
        self.assertEqual(rc, 0)

    def test_current_image_and_workspace_present_routes_to_subsequent_run(self):
        """Normal steady-state path — image_matches=True AND workspace
        ready, so drift detection in subsequent_run decides what (if
        anything) needs doing."""
        self._make_workspace_ready()
        prison = self._prison(image_matches=True)
        with (
            patch.object(start, "_first_run_after_init", return_value=0) as first,
            patch.object(start, "_subsequent_run", return_value=0) as subsequent,
        ):
            rc = start.cmd_start(self.project_dir, prison=prison)
        subsequent.assert_called_once_with(self.project_dir, prison=prison)
        first.assert_not_called()
        self.assertEqual(rc, 0)

    def test_routing_propagates_handler_return_code(self):
        self._make_workspace_ready()
        prison = self._prison(image_matches=False)
        with patch.object(start, "_first_run_after_init", return_value=7):
            rc = start.cmd_start(self.project_dir, prison=prison)
        self.assertEqual(rc, 7)

    def test_recipe_hash_is_passed_to_image_matches(self):
        """Sanity check: the recipe hash returned by `recipe_hash` is
        what gets passed to `image_matches`. This wires together what
        cmd_start's two port calls communicate about."""
        self._make_workspace_ready()
        prison = self._prison(image_matches=True)
        prison.recipe_hash.return_value = "specifichash00ab"
        with (
            patch.object(start, "_first_run_after_init", return_value=0),
            patch.object(start, "_subsequent_run", return_value=0),
        ):
            start.cmd_start(self.project_dir, prison=prison)
        prison.image_matches.assert_called_once_with("specifichash00ab")


class CmdStartDetachedHeadTests(unittest.TestCase):
    """Step 1.7 (change_promotion_machinery.md L797-798):
    `alcatrazer start` must refuse to run when the outer repository
    is on a detached HEAD. Promotion is bound to a starting branch
    (Phase 1 introduces pinned_branch as state.json's anchor for
    every later cycle); without a branched HEAD there is no anchor
    to record.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # Minimal .alcatrazer/ so cmd_start passes its first guard
        # ("run alcatrazer init first"). The detached-HEAD check is
        # expected to fire before any further setup is required.
        (self.project_dir / ".alcatrazer").mkdir()

    def _detach_outer(self) -> None:
        """Initialize outer on `main` with one commit, then detach HEAD."""
        cwd = str(self.project_dir)
        subprocess.run(
            ["git", "init", "-b", "main", cwd],
            capture_output=True,
            check=True,
        )
        for k, v in (("user.name", "T"), ("user.email", "t@test")):
            subprocess.run(
                ["git", "-C", cwd, "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["git", "-C", cwd, "commit", "--allow-empty", "-m", "first"],
            capture_output=True,
            check=True,
        )
        sha = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", cwd, "checkout", "--detach", sha],
            capture_output=True,
            check=True,
        )

    def test_refuses_with_explanatory_message_on_detached_head(self):
        self._detach_outer()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_start(self.project_dir)
        self.assertNotEqual(rc, 0)
        # Error message must mention the constraint: outer must be on a branch.
        err = stderr.getvalue().lower()
        self.assertIn("branch", err)


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

    def test_supported_language_set(self):
        self.assertEqual(
            set(languages.SUPPORTED_LANGUAGES),
            {"python", "node", "rust", "go", "dotnet", "java"},
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

    # --- Phase 1.2: dotnet (C# / F# / VB.NET) -----------------------------

    def test_dotnet_default_manager_is_dotnet_only(self):
        # .NET ships one canonical CLI — `dotnet add package` (NuGet under
        # the hood). Same single-element shape as `go` and `rust`.
        dotnet = languages.SUPPORTED_LANGUAGES["dotnet"]
        self.assertEqual(dotnet["default_manager"], "dotnet")
        self.assertEqual(dotnet["managers"], ("dotnet",))

    def test_dotnet_version_check_uses_dotnet_dash_dash_version(self):
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["dotnet"]["version_check"],
            "dotnet --version",
        )

    def test_dotnet_declares_libicu74_as_required_os_package(self):
        # .NET runtime crashes immediately without an ICU library
        # ("Couldn't find a valid ICU package") on minimal Ubuntu 24.04.
        # Encoding it next to the language entry means picking
        # [languages.dotnet] auto-installs libicu74 at image build —
        # no runtime sudo, no discovery-by-crash.
        self.assertIn(
            "libicu74",
            languages.SUPPORTED_LANGUAGES["dotnet"].get("required_os_packages", ()),
        )

    def test_other_languages_have_no_required_os_packages(self):
        # python/node/rust/go/java run on what Ubuntu 24.04's minimal base
        # provides; any future addition must justify itself with a
        # crash-on-startup-without-it argument like .NET's ICU.
        for name in ("python", "node", "rust", "go", "java"):
            self.assertEqual(
                languages.SUPPORTED_LANGUAGES[name].get("required_os_packages", ()),
                (),
                f"{name!r} should not declare required_os_packages",
            )

    # --- Phase 1.2.2: java (JVM — Maven / Gradle) ------------------------

    def test_java_default_manager_is_maven_with_gradle_alternative(self):
        java = languages.SUPPORTED_LANGUAGES["java"]
        self.assertEqual(java["default_manager"], "maven")
        self.assertIn("maven", java["managers"])
        self.assertIn("gradle", java["managers"])

    def test_java_version_check_redirects_stderr(self):
        # `2>&1` is load-bearing: java prints `-version` output to stderr,
        # and the verify block's chained `&&` only captures stdout in the
        # docker-build log. Without the redirect, the version line is lost.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["java"]["version_check"],
            "java -version 2>&1",
        )

    def test_java_has_no_required_os_packages(self):
        # JDK binary distributions (Temurin, etc.) link only against
        # Ubuntu 24.04's libc / libstdc++ — no extra apt-time deps for
        # `java -version` / basic compilation. Verified empirically inside
        # a fresh Alcatraz before merging.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["java"].get("required_os_packages", ()),
            (),
        )

    def test_java_declares_distribution_version_tip(self):
        # Java is the first language with multiple shipped distributions
        # (Temurin / Corretto / Zulu / Liberica / GraalVM) reachable via
        # the same mise key. The version_tip nudges users mid-wizard so
        # the option doesn't stay invisible.
        tip = languages.SUPPORTED_LANGUAGES["java"].get("version_tip", "")
        self.assertTrue(tip, "java must declare a version_tip")
        tip_lower = tip.lower()
        self.assertIn("temurin", tip_lower)
        self.assertTrue(
            any(d in tip_lower for d in ("corretto", "zulu", "graalvm")),
            "version_tip must mention at least one alternative distribution",
        )
        self.assertIn("readme", tip_lower)

    # --- Phase 1.2.3: every language declares a version_tip --------------

    def test_every_language_declares_a_version_tip(self):
        # Phase 1.2.3 expanded version_tip from java-only to every entry,
        # so users always see format examples and any default behavior
        # before the version prompt. The tip is no longer optional in
        # practice — leaving it off a future entry is a regression.
        for name, cfg in languages.SUPPORTED_LANGUAGES.items():
            self.assertIn(
                "version_tip",
                cfg,
                f"{name!r} must declare a version_tip",
            )
            self.assertTrue(
                cfg["version_tip"].strip(),
                f"{name!r} version_tip must be non-empty",
            )

    def test_every_version_tip_starts_with_version_examples(self):
        # Leading "Version examples:" is the chosen wording — keeps tips
        # consistent across languages and signals intent ("here are
        # concrete strings you can paste") rather than an open-ended
        # explanation.
        for name, cfg in languages.SUPPORTED_LANGUAGES.items():
            tip = cfg.get("version_tip", "")
            self.assertTrue(
                tip.startswith("Version examples:"),
                f"{name!r} version_tip must start with 'Version examples:' (got: {tip[:40]!r}…)",
            )

    def test_python_version_tip_includes_modern_releases(self):
        tip = languages.SUPPORTED_LANGUAGES["python"]["version_tip"]
        for v in ("3.11", "3.12", "3.13"):
            self.assertIn(v, tip)

    def test_node_version_tip_mentions_lts(self):
        tip = languages.SUPPORTED_LANGUAGES["node"]["version_tip"].lower()
        self.assertIn("lts", tip)
        # Node's even-numbered LTS lines (18/20/22) are the recommended
        # production versions; tip should surface them.
        for v in ("18", "20", "22"):
            self.assertIn(v, tip)

    def test_rust_and_go_version_tips_pin_concrete_releases(self):
        rust_tip = languages.SUPPORTED_LANGUAGES["rust"]["version_tip"].lower()
        go_tip = languages.SUPPORTED_LANGUAGES["go"]["version_tip"].lower()
        self.assertIn("concrete", rust_tip)
        self.assertIn("concrete", go_tip)

    def test_dotnet_version_tip_mentions_lts_versions(self):
        tip = languages.SUPPORTED_LANGUAGES["dotnet"]["version_tip"].lower()
        self.assertIn("lts", tip)
        # Both currently-supported LTS versions surface in the tip.
        self.assertIn("8.0.404", tip)
        self.assertIn("10.0.100", tip)

    # --- Phase 1.2.4: bundled_managers + manager_tip ---------------------

    def test_every_language_declares_bundled_managers(self):
        # Phase 1.2.4 separates "default manager" (UI hint) from "what
        # mise installs". `bundled_managers` says "ships with the
        # runtime, no separate install needed". Every entry must
        # declare this — the empty tuple is a valid value (e.g. java).
        for name, cfg in languages.SUPPORTED_LANGUAGES.items():
            self.assertIn(
                "bundled_managers",
                cfg,
                f"{name!r} must declare bundled_managers",
            )
            self.assertIsInstance(
                cfg["bundled_managers"],
                tuple,
                f"{name!r} bundled_managers must be a tuple",
            )

    def test_python_pip_is_bundled_other_managers_are_not(self):
        # pip ships with CPython; mise installs CPython, so pip arrives
        # for free. uv / poetry / pipenv are separate and must install.
        bm = languages.SUPPORTED_LANGUAGES["python"]["bundled_managers"]
        self.assertIn("pip", bm)
        for non_bundled in ("uv", "poetry", "pipenv"):
            self.assertNotIn(non_bundled, bm)

    def test_node_npm_is_bundled_pnpm_yarn_are_not(self):
        bm = languages.SUPPORTED_LANGUAGES["node"]["bundled_managers"]
        self.assertIn("npm", bm)
        for non_bundled in ("pnpm", "yarn"):
            self.assertNotIn(non_bundled, bm)

    def test_rust_cargo_is_bundled(self):
        # cargo ships with the rust toolchain; single canonical manager.
        bm = languages.SUPPORTED_LANGUAGES["rust"]["bundled_managers"]
        self.assertEqual(bm, ("cargo",))

    def test_go_and_dotnet_managers_are_runtime_themselves(self):
        # `go` IS the runtime; `dotnet` IS the runtime. Bundled by
        # tautology — there's nothing else to install.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["go"]["bundled_managers"],
            ("go",),
        )
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["dotnet"]["bundled_managers"],
            ("dotnet",),
        )

    def test_java_has_empty_bundled_managers(self):
        # Java is the first language where NOTHING is bundled. mise
        # must install whichever manager is picked (default or override).
        # This is the heart of the bug Phase 1.2.4 fixes — accepting
        # the default `[maven]` previously left Maven uninstalled.
        self.assertEqual(
            languages.SUPPORTED_LANGUAGES["java"]["bundled_managers"],
            (),
        )

    def test_every_language_declares_a_manager_tip(self):
        # Symmetric with version_tip from Phase 1.2.3. Every entry
        # declares one — even single-manager languages get the tip in
        # the generated TOML as self-documenting comment, although the
        # wizard skips the prompt for single-manager cases.
        for name, cfg in languages.SUPPORTED_LANGUAGES.items():
            self.assertIn(
                "manager_tip",
                cfg,
                f"{name!r} must declare a manager_tip",
            )
            self.assertTrue(
                cfg["manager_tip"].strip(),
                f"{name!r} manager_tip must be non-empty",
            )

    def test_python_manager_tip_mentions_pip_bundled(self):
        # Tip should explain the pip-is-bundled detail so users know
        # their default isn't a separate install.
        tip = languages.SUPPORTED_LANGUAGES["python"]["manager_tip"].lower()
        self.assertIn("pip", tip)
        self.assertIn("bundled", tip)

    def test_java_manager_tip_mentions_both_options(self):
        # Java's tip surfaces both maven (default) and gradle.
        tip = languages.SUPPORTED_LANGUAGES["java"]["manager_tip"].lower()
        self.assertIn("maven", tip)
        self.assertIn("gradle", tip)

    def test_aqua_attestation_misaligned_includes_uv(self):
        """Phase 1.2.7: uv is the first manager whose mise install fails
        because aqua-registry expects a workflow-signed build-provenance
        attestation while upstream publishes a release-type attestation
        signed by GitHub's release infrastructure. AQUA_ATTESTATION_MISALIGNED
        is the data anchor for the workaround in `_render_mise_uses`;
        entries are removed when upstream's aqua-registry config catches
        up. Locking the current state under test means a future "looks
        fine, ship it" removal must update this test too — explicit
        signal, not silent drift."""
        self.assertIn("uv", languages.AQUA_ATTESTATION_MISALIGNED)


def _run_wizard(func, inputs):
    """Call a wizard function with patched input() and captured stdout."""
    with (
        patch("builtins.input", side_effect=iter(inputs)),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        return func()


class AskLanguagesTests(unittest.TestCase):
    """Step 3d: languages prompt — selection, version + manager per language."""

    def test_single_language_default_manager_stored_as_default(self):
        # Phase 1.2.4: accepting the default no longer omits the field.
        # The resolved manager (default `pip` for python) is always
        # written into the dict so the generated TOML can show it
        # explicitly and `_render_mise_uses` can decide whether to
        # install it (bundled vs not).
        result = _run_wizard(start.ask_languages, ["python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_single_language_non_default_manager_stored(self):
        result = _run_wizard(start.ask_languages, ["python", "3.12", "uv"])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "uv"}})

    def test_explicit_default_manager_name_stored_same_as_implicit(self):
        # User types "pip" (the default) → result must equal what the
        # implicit-default path produces. No special-casing.
        result = _run_wizard(start.ask_languages, ["python", "3.12", "pip"])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_multiple_languages_comma_separated(self):
        result = _run_wizard(
            start.ask_languages,
            ["python, node", "3.12", "uv", "22", ""],
        )
        self.assertEqual(
            result,
            {
                "python": {"version": "3.12", "manager": "uv"},
                "node": {"version": "22", "manager": "npm"},
            },
        )

    def test_unknown_language_reprompts(self):
        result = _run_wizard(start.ask_languages, ["cobol", "python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_empty_selection_reprompts(self):
        result = _run_wizard(start.ask_languages, ["", "python", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_version_latest_is_rejected(self):
        result = _run_wizard(start.ask_languages, ["python", "latest", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_empty_version_is_rejected(self):
        result = _run_wizard(start.ask_languages, ["python", "", "3.12", ""])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "pip"}})

    def test_unknown_manager_reprompts(self):
        result = _run_wizard(start.ask_languages, ["python", "3.12", "pixi", "uv"])
        self.assertEqual(result, {"python": {"version": "3.12", "manager": "uv"}})

    def test_rust_single_manager_skips_prompt_and_stores_cargo(self):
        # Single-manager case skips the wizard prompt but still records
        # the resolved manager in the dict.
        result = _run_wizard(start.ask_languages, ["rust", "1.75"])
        self.assertEqual(result, {"rust": {"version": "1.75", "manager": "cargo"}})

    def test_dotnet_single_manager_skips_prompt_and_stores_dotnet(self):
        result = _run_wizard(start.ask_languages, ["dotnet", "10.0.100"])
        self.assertEqual(result, {"dotnet": {"version": "10.0.100", "manager": "dotnet"}})

    def test_java_manager_prompt_default_accepted_stores_maven(self):
        # Java has multiple managers; default-accepted (empty input)
        # now records the default explicitly. This is the bug Phase
        # 1.2.4 fixes — accepting `[maven]` previously left manager
        # unset, which silently meant Maven didn't install.
        result = _run_wizard(start.ask_languages, ["java", "21", ""])
        self.assertEqual(result, {"java": {"version": "21", "manager": "maven"}})

    def test_java_manager_prompt_accepts_gradle(self):
        result = _run_wizard(start.ask_languages, ["java", "21", "gradle"])
        self.assertEqual(result, {"java": {"version": "21", "manager": "gradle"}})


class AskVersionTipTests(unittest.TestCase):
    """Phase 1.2.2: `_ask_version` prints `cfg["version_tip"]` (if declared)
    before the version prompt, so language-specific nudges (Java's
    distribution prefixes, future Ruby/Python tips) surface in the wizard.
    Languages without a `version_tip` keep the bare prompt they have today.
    """

    def _capture_ask_version(self, language: str, answer: str) -> tuple[str, str]:
        """Run `_ask_version(language)` with `answer` as the typed input.
        Returns (return_value, captured_stdout)."""
        stdout = io.StringIO()
        with (
            patch("builtins.input", return_value=answer),
            contextlib.redirect_stdout(stdout),
        ):
            result = start._ask_version(language)
        return result, stdout.getvalue()

    def test_prints_tip_when_language_declares_it(self):
        # Java declares a version_tip mentioning Temurin, alternatives,
        # and a "see README" pointer; all three must appear in the
        # printed tip (otherwise the wizard nudge is incomplete).
        result, out = self._capture_ask_version("java", "21")
        self.assertEqual(result, "21")
        out_lower = out.lower()
        self.assertIn("tip:", out_lower)
        self.assertIn("temurin", out_lower)
        self.assertTrue(
            any(d in out_lower for d in ("corretto", "zulu", "graalvm")),
            "wizard tip must surface at least one alternative distribution",
        )
        self.assertIn("readme", out_lower)

    def test_prints_tip_for_python_too(self):
        # Phase 1.2.3 expanded version_tip from java-only to every entry.
        # Python now also gets its own format hint before the prompt.
        result, out = self._capture_ask_version("python", "3.12")
        self.assertEqual(result, "3.12")
        self.assertIn("Tip:", out)
        self.assertIn("Version examples:", out)

    def test_blank_line_precedes_tip_not_follows_it(self):
        # Layout fix: today's blank-line-after-tip visually orphans the
        # tip from the upcoming `Version for X:` prompt and groups it
        # with the previous answer instead. The blank must come BEFORE
        # the tip so the tip groups with what it explains.
        _, out = self._capture_ask_version("java", "21")
        lines = out.split("\n")
        # Find the Tip line; the line just before it must be empty.
        tip_idx = next(i for i, line in enumerate(lines) if "Tip:" in line)
        self.assertGreater(tip_idx, 0, "Tip must not be the very first line")
        self.assertEqual(
            lines[tip_idx - 1].strip(),
            "",
            "blank line must precede the Tip block",
        )

    def test_no_blank_line_separates_tip_from_prompt(self):
        # Counterpart to the previous test: no blank line between the
        # tip's last line and the version prompt. The captured stdout
        # ends right after the tip (input() under mock doesn't echo its
        # prompt), so the discriminator is whether output ends with a
        # SINGLE newline (good — tip's last line then prompt is next)
        # or DOUBLE (bad — tip then orphan blank line then prompt).
        _, out = self._capture_ask_version("java", "21")
        self.assertFalse(
            out.endswith("\n\n"),
            "output must not end with a blank line; the prompt comes next",
        )


class AskManagerTipTests(unittest.TestCase):
    """Phase 1.2.4: `_ask_manager` prints `cfg["manager_tip"]` before the
    multi-manager prompt — same pattern as `_ask_version` + `version_tip`
    from Phase 1.2.3 (blank BEFORE the tip, no blank between tip and
    prompt). Single-manager languages skip the prompt entirely; the tip
    still surfaces in the generated TOML as a comment."""

    def _capture_ask_manager(self, language: str, answer: str | None) -> tuple:
        """Run `_ask_manager(language)`. If answer is None, no input
        is consumed (single-manager case). Returns (return_value,
        captured_stdout)."""
        stdout = io.StringIO()
        side_effect = iter([]) if answer is None else iter([answer])
        with (
            patch("builtins.input", side_effect=side_effect),
            contextlib.redirect_stdout(stdout),
        ):
            result = start._ask_manager(language)
        return result, stdout.getvalue()

    def test_returns_resolved_default_when_user_accepts_default(self):
        # Phase 1.2.4: _ask_manager always returns a string, never None.
        # The contract change is the heart of "manager always written".
        result, _ = self._capture_ask_manager("python", "")
        self.assertEqual(result, "pip")

    def test_returns_user_pick_when_user_chooses_alternative(self):
        result, _ = self._capture_ask_manager("python", "uv")
        self.assertEqual(result, "uv")

    def test_returns_lone_manager_for_single_manager_language(self):
        # rust has only `cargo` — no prompt, just return the lone option.
        result, _ = self._capture_ask_manager("rust", None)
        self.assertEqual(result, "cargo")

    def test_returns_dotnet_for_single_manager_dotnet(self):
        result, _ = self._capture_ask_manager("dotnet", None)
        self.assertEqual(result, "dotnet")

    def test_prints_tip_for_multi_manager_language(self):
        # Java's manager_tip should surface in the wizard before the
        # `Package manager for java?` prompt.
        _, out = self._capture_ask_manager("java", "")
        self.assertIn("Tip:", out)
        out_lower = out.lower()
        self.assertIn("maven", out_lower)
        self.assertIn("gradle", out_lower)

    def test_prints_no_tip_for_single_manager_language(self):
        # rust has only cargo; the wizard skips the prompt entirely.
        # No tip is printed mid-wizard either (nothing to choose).
        _, out = self._capture_ask_manager("rust", None)
        self.assertNotIn("Tip:", out)

    def test_blank_line_precedes_tip_not_follows_it(self):
        # Same layout convention as `_ask_version`'s tip (Phase 1.2.3).
        _, out = self._capture_ask_manager("java", "")
        self.assertTrue(
            out.startswith("\n"),
            "output must start with a blank line preceding the Tip block",
        )


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
                    # Phase 1.2.4: default-accepted manager is now stored
                    # in the dict (resolved value).
                    "node": {"version": "22", "manager": "npm"},
                },
                "startup": {"commands": ["uv sync", "npm install"]},
            },
        )

    def test_minimal_python_only_omits_empty_sections(self):
        inputs = ["python", "3.12", "", "", ""]
        result = _run_wizard(start.ask_coding_environment, inputs)
        # Phase 1.2.4: python with default manager accepted now stores
        # the resolved `pip` value.
        self.assertEqual(
            result,
            {"languages": {"python": {"version": "3.12", "manager": "pip"}}},
        )

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


class WizardSelfExplanationTests(unittest.TestCase):
    """Phase 1.2.1: the wizard prints a one-time intro diagram, section
    banners, and a few lines of context per `ask_*` so domain terms
    (`promote`, `Alcatraz`, `baked`) are introduced BEFORE they appear
    inside prompts. No structural changes — same prompts, same accepted
    answers, same return values."""

    def _capture(self, callable_, *args, **kwargs):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            callable_(*args, **kwargs)
        return stdout.getvalue()

    def _capture_with_inputs(self, func, inputs):
        stdout = io.StringIO()
        with (
            patch("builtins.input", side_effect=iter(inputs)),
            contextlib.redirect_stdout(stdout),
        ):
            func()
        return stdout.getvalue()

    # --- Intro panel (`_print_init_intro`) -------------------------------

    def test_intro_panel_introduces_promote_term(self):
        # The very first prompt later asks "Use for promoted commits?".
        # Users who don't already know what "promoted" means in this
        # context need to have seen the term defined first.
        out = self._capture(start._print_init_intro)
        self.assertIn("promote", out.lower())

    def test_intro_panel_names_alcatraz_and_your_repo(self):
        out = self._capture(start._print_init_intro)
        self.assertIn("Alcatraz", out)
        self.assertIn("your repo", out)

    def test_intro_panel_distinguishes_real_and_throwaway_identity(self):
        # YOUR (uppercase) for emphasis on the real side; the agent
        # side mentions a fake / throwaway identity.
        out = self._capture(start._print_init_intro)
        self.assertIn("YOUR", out)
        self.assertTrue(
            "fake" in out.lower() or "throwaway" in out.lower(),
            "intro must describe the agent identity as fake/throwaway",
        )

    def test_intro_panel_mentions_credentials_contrast(self):
        out = self._capture(start._print_init_intro)
        self.assertIn("credentials", out.lower())

    def test_intro_panel_points_at_editable_toml(self):
        # The intro tells users they can edit coding-environment.toml
        # before `alcatrazer start` if they want to change anything.
        out = self._capture(start._print_init_intro)
        self.assertIn("coding-environment.toml", out)

    def test_intro_panel_mentions_agents_cannot_push(self):
        # Trust property — agents commit but only the user pushes to
        # remotes. Stating it once in the intro avoids surprise later.
        out = self._capture(start._print_init_intro).lower()
        self.assertIn("cannot push", out)

    # --- Per-section banners + context ---------------------------------

    def test_promotion_identity_has_banner_and_promote_context(self):
        with patch.object(start, "read_git_identity", return_value=("A", "a@e")):
            out = self._capture_with_inputs(
                lambda: start.ask_promotion_identity(Path("/")),
                [""],
            )
        self.assertIn("=== Promotion identity ===", out)
        # Banner sentence references the term defined in the intro panel.
        self.assertIn("promote", out.lower())

    def test_languages_has_banner_and_baked_context(self):
        # Three inputs cover: languages list, version, manager (empty).
        out = self._capture_with_inputs(start.ask_languages, ["python", "3.12", ""])
        self.assertIn("=== Languages ===", out)
        self.assertIn("baked", out.lower())
        self.assertIn("coding-environment.toml", out)

    def test_os_packages_has_banner_and_baked_context(self):
        out = self._capture_with_inputs(start.ask_os_packages, [""])
        self.assertIn("=== System packages", out)
        self.assertIn("baked", out.lower())

    def test_startup_commands_has_banner_and_every_boot_context(self):
        out = self._capture_with_inputs(start.ask_startup_commands, [""])
        self.assertIn("=== Startup commands", out)
        self.assertIn("every time", out.lower())
        # Explicit contrast with the baked sections above.
        self.assertIn("NOT baked", out)


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
        # Phase 1.2.4: even when caller omits `manager`, the writer fills
        # it with the language default — so node lands as `npm` here.
        self.assertEqual(parsed["languages"]["node"]["manager"], "npm")
        self.assertEqual(parsed["startup"]["commands"], ["uv sync", "npm install"])

    def test_zero_alcatrazer_branding_in_output(self):
        data = {"languages": {"python": {"version": "3.12"}}}
        path = start.write_coding_environment_toml(self.project_dir, data)
        self.assertNotIn("alcatraz", path.read_text().lower())

    def test_absent_sections_have_commented_examples_not_active_headers(self):
        """When the user doesn't pick [os] or [startup] at init time, the
        generated file carries commented-out example blocks (so they can
        edit and run `alcatrazer start` later) but NOT an active header."""
        data = {"languages": {"python": {"version": "3.12"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()

        # Active headers (line-anchored, no leading '#') must be absent.
        self.assertNotRegex(content, r"(?m)^\[os\]")
        self.assertNotRegex(content, r"(?m)^\[startup\]")
        # Commented examples must be present.
        self.assertRegex(content, r"(?m)^# \[os\]")
        self.assertRegex(content, r"(?m)^# \[startup\]")
        # And parses cleanly as TOML (commented blocks don't break it).
        tomllib.loads(content)
        self.assertTrue(re.compile(r"^# \[os\]", re.MULTILINE).search(content))

    def test_shows_commented_example_for_a_language_user_didnt_pick(self):
        """Syntax reference so the user can add another language later."""
        data = {"languages": {"python": {"version": "3.12"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        # Python is active (chosen), so the commented example must be
        # one of the OTHER supported languages — never a duplicated python
        # block that'd shadow the real one.
        self.assertRegex(content, r"(?m)^# \[languages\.(node|rust|go)\]")
        self.assertNotRegex(content, r"(?m)^# \[languages\.python\]")

    def test_header_explains_rebuild_vs_restart_semantics(self):
        """Users editing this file directly need to know which sections
        trigger a rebuild (os, languages) vs just a restart (startup)."""
        data = {"languages": {"python": {"version": "3.12"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        self.assertIn("rebuild", content.lower())
        self.assertIn("restart", content.lower())

    def test_backend_agnostic_vocabulary(self):
        """Agent-visible file — no 'container' / 'image' / 'docker' leaks."""
        data = {"languages": {"python": {"version": "3.12"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text().lower()
        self.assertNotIn("container", content)
        self.assertNotIn("docker", content)
        # "image" doesn't appear in any of our comments.
        self.assertNotIn(" image ", content)
        self.assertIn("workspace", content)

    def test_collision_uses_hex_suffix(self):
        existing = self.project_dir / "coding-environment.toml"
        existing.write_text("# user's existing file — do not clobber\n")
        data = {"languages": {"python": {"version": "3.12"}}}
        with patch.object(start.secrets, "token_hex", return_value="a3f7"):
            path = start.write_coding_environment_toml(self.project_dir, data)
        self.assertEqual(path, self.project_dir / "coding-environment-a3f7.toml")
        self.assertEqual(existing.read_text(), "# user's existing file — do not clobber\n")

    def test_writer_emits_schema_version_field(self):
        """Phase 1.1: every freshly-generated file declares its schema."""
        data = {"languages": {"python": {"version": "3.12"}}}
        path = start.write_coding_environment_toml(self.project_dir, data)
        with open(path, "rb") as f:
            parsed = tomllib.load(f)
        self.assertEqual(parsed["schema_version"], start.CODING_ENV_SCHEMA_VERSION)

    def test_schema_version_precedes_section_headers(self):
        """Top-level fields must appear before any [section] in TOML — and
        beyond that requirement, we want it visually first so a human
        editor sees the version stamp before the data."""
        data = {
            "os": {"packages": ["build-essential"]},
            "languages": {"python": {"version": "3.12"}},
            "startup": {"commands": ["uv sync"]},
        }
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        schema_idx = content.find("schema_version")
        self.assertGreater(schema_idx, -1, "schema_version line must be present")
        prefix = content[:schema_idx]
        # Only comments and blank lines may precede schema_version.
        for line in prefix.splitlines():
            stripped = line.strip()
            self.assertTrue(
                stripped == "" or stripped.startswith("#"),
                f"Non-comment content before schema_version: {line!r}",
            )

    # --- Phase 1.2.3: version_tip rendered as TOML comment -----------------

    def test_each_language_section_carries_its_version_tip_as_comment(self):
        # The same version_tip the wizard prints surfaces in the generated
        # TOML as a comment block above its `version =` line — DRY: one
        # source string in SUPPORTED_LANGUAGES, two consumers (wizard +
        # generated config). Users editing the file later see the same
        # guidance, no need to re-run init.
        data = {
            "languages": {
                "python": {"version": "3.12"},
                "java": {"version": "21"},
            },
        }
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()

        for lang in ("python", "java"):
            section_idx = content.index(f"[languages.{lang}]")
            # Find this section's version line; everything between header
            # and version line is the tip-as-comments block.
            version_idx = content.index("version =", section_idx)
            block = content[section_idx:version_idx]
            tip = languages.SUPPORTED_LANGUAGES[lang]["version_tip"]
            # The tip's leading "Version examples:" must show up as a
            # comment in the section.
            self.assertIn("# Version examples:", block, f"missing tip in {lang} section")
            # And the language-specific kernel of the tip must show too
            # (sanity check that we're rendering the right tip per language).
            kernel = "3.11" if lang == "python" else "Eclipse Temurin"
            self.assertIn(kernel, block)
            # No bare (uncommented) leakage — every non-blank line in the
            # block between header and version must start with `#`.
            for line in block.splitlines()[1:]:  # skip the [languages.X] header
                stripped = line.strip()
                if stripped:
                    self.assertTrue(
                        stripped.startswith("#"),
                        f"non-comment line in tip block for {lang}: {line!r}",
                    )
            # And the kernel string proves the right tip is rendered for
            # this language, not a stale one (avoid `tip` "unused" lint).
            self.assertTrue(tip)

    def test_long_version_tips_wrap_at_comment_friendly_width(self):
        # Java's tip is the longest one (mentions four distributions plus
        # README pointer). It must wrap into multiple `# `-prefixed lines
        # in the generated TOML rather than land as one mile-long comment.
        data = {"languages": {"java": {"version": "21"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        section = content[
            content.index("[languages.java]") : content.index(
                "version =", content.index("[languages.java]")
            )
        ]
        comment_lines = [ln for ln in section.splitlines() if ln.strip().startswith("#")]
        self.assertGreater(
            len(comment_lines),
            1,
            "java's tip must wrap to multiple comment lines, not stay one long line",
        )
        # Each wrapped line should keep within a sensible width (~80 cols).
        for line in comment_lines:
            self.assertLessEqual(
                len(line),
                80,
                f"tip comment line exceeds 80 cols: {line!r}",
            )

    # --- Phase 1.2.4: `manager =` always emitted + manager_tip as comment -

    def test_each_language_section_always_emits_manager_line(self):
        # Phase 1.2.4: `manager =` is no longer optional in the TOML.
        # The resolved value (user pick OR language default) lands on
        # disk so users can see and edit the choice without re-init.
        # Tested for both default-accepted (python+pip) and explicit-
        # override (java+gradle) cases.
        data = {
            "languages": {
                "python": {"version": "3.12", "manager": "pip"},
                "java": {"version": "21", "manager": "gradle"},
            },
        }
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        # Both sections carry the resolved manager line.
        self.assertIn('manager = "pip"', content)
        self.assertIn('manager = "gradle"', content)

    def test_each_language_section_carries_its_manager_tip_as_comment(self):
        # Symmetric with version_tip: same DRY plumbing, same shape —
        # `manager_tip` lands as `# `-prefixed comments above the
        # `manager =` line.
        data = {
            "languages": {
                "python": {"version": "3.12", "manager": "pip"},
                "java": {"version": "21", "manager": "maven"},
            },
        }
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        for lang in ("python", "java"):
            section_start = content.index(f"[languages.{lang}]")
            manager_idx = content.index("manager =", section_start)
            block = content[section_start:manager_idx]
            tip = languages.SUPPORTED_LANGUAGES[lang]["manager_tip"]
            # Some kernel of the tip's content must surface in the
            # comment block (avoids matching against the wrong tip).
            kernel = "pip" if lang == "python" else "maven"
            self.assertIn("# ", block, f"missing manager_tip comment for {lang}")
            self.assertIn(kernel, block)
            # The tip itself shouldn't be unused in this assertion path.
            self.assertTrue(tip)

    def test_long_manager_tips_wrap_at_comment_friendly_width(self):
        # Python's manager_tip is the longest (mentions pip default
        # and three alternatives). Must wrap to multiple lines, each
        # within ~80 cols.
        data = {"languages": {"python": {"version": "3.12", "manager": "pip"}}}
        content = start.write_coding_environment_toml(self.project_dir, data).read_text()
        section_start = content.index("[languages.python]")
        version_idx = content.index("version =", section_start)
        manager_idx = content.index("manager =", section_start)
        # Slice between the version line and the manager line — that's
        # where the manager_tip comment block lives.
        manager_block = content[version_idx:manager_idx]
        manager_comment_lines = [
            ln for ln in manager_block.splitlines() if ln.strip().startswith("#")
        ]
        self.assertGreaterEqual(
            len(manager_comment_lines),
            2,
            "python's manager_tip must wrap to multiple comment lines",
        )
        for line in manager_comment_lines:
            self.assertLessEqual(
                len(line),
                80,
                f"manager_tip comment line exceeds 80 cols: {line!r}",
            )


class CodingEnvironmentSchemaVersionTests(unittest.TestCase):
    """Phase 1.1: schema versioning for coding-environment.toml.

    Two surfaces under test:
    - `_validate_coding_env_schema_version(data)` — pure dict-level validator.
    - `_load_coding_environment(project_dir)` — file-level loader that calls
      the validator after parsing. Backwards compat is the load contract:
      configs without an explicit `schema_version` field were written before
      Phase 1.1 and must keep working.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
        )
        self.coding_env_path = self.project_dir / "coding-environment.toml"
        self.addCleanup(self.tmp.cleanup)

    # --- Constant ------------------------------------------------------------

    def test_constant_is_one_for_this_release(self):
        self.assertEqual(start.CODING_ENV_SCHEMA_VERSION, 1)

    # --- Validator: accept ---------------------------------------------------

    def test_validator_accepts_missing_field(self):
        """Files predating Phase 1.1 carry no schema_version. Treat them
        as the current version — refusing them would break every existing
        user's config."""
        start._validate_coding_env_schema_version({})  # must not raise

    def test_validator_accepts_current_version_explicit(self):
        start._validate_coding_env_schema_version({"schema_version": 1})

    # --- Validator: reject ---------------------------------------------------

    def test_validator_rejects_higher_version_with_upgrade_hint(self):
        with self.assertRaises(start.UnsupportedSchemaVersionError) as ctx:
            start._validate_coding_env_schema_version({"schema_version": 99})
        msg = str(ctx.exception)
        self.assertIn("99", msg)
        self.assertIn("upgrade alcatrazer", msg.lower())

    def test_validator_rejects_zero(self):
        with self.assertRaises(start.UnsupportedSchemaVersionError):
            start._validate_coding_env_schema_version({"schema_version": 0})

    def test_validator_rejects_negative(self):
        with self.assertRaises(start.UnsupportedSchemaVersionError):
            start._validate_coding_env_schema_version({"schema_version": -1})

    def test_validator_rejects_string(self):
        with self.assertRaises(start.UnsupportedSchemaVersionError) as ctx:
            start._validate_coding_env_schema_version({"schema_version": "1"})
        self.assertIn("integer", str(ctx.exception).lower())

    def test_validator_rejects_float(self):
        # TOML `schema_version = 1.0` parses as float — reject it so users
        # don't accidentally drift toward semver semantics we don't support.
        with self.assertRaises(start.UnsupportedSchemaVersionError):
            start._validate_coding_env_schema_version({"schema_version": 1.0})

    def test_validator_rejects_bool(self):
        # bool is a subclass of int in Python; the validator must
        # short-circuit on bool before the int path.
        with self.assertRaises(start.UnsupportedSchemaVersionError):
            start._validate_coding_env_schema_version({"schema_version": True})

    # --- Loader integration (validator wired into _load_coding_environment) -

    def test_loader_treats_missing_field_as_v1(self):
        """Backwards-compat for configs written before Phase 1.1."""
        self.coding_env_path.write_text('[languages.python]\nversion = "3.12"\n')
        data = start._load_coding_environment(self.project_dir)
        self.assertEqual(data["languages"]["python"]["version"], "3.12")

    def test_loader_accepts_explicit_v1(self):
        self.coding_env_path.write_text(
            'schema_version = 1\n[languages.python]\nversion = "3.12"\n'
        )
        data = start._load_coding_environment(self.project_dir)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["languages"]["python"]["version"], "3.12")

    def test_loader_rejects_unsupported_version(self):
        self.coding_env_path.write_text(
            'schema_version = 99\n[languages.python]\nversion = "3.12"\n'
        )
        with self.assertRaises(start.UnsupportedSchemaVersionError):
            start._load_coding_environment(self.project_dir)

    def test_writer_loader_round_trip(self):
        """The wizard-generated file is consumable by the loader unchanged."""
        data = {
            "os": {"packages": ["build-essential"]},
            "languages": {"python": {"version": "3.12", "manager": "uv"}},
            "startup": {"commands": ["uv sync"]},
        }
        start.write_coding_environment_toml(self.project_dir, data)
        loaded = start._load_coding_environment(self.project_dir)
        self.assertEqual(loaded["schema_version"], start.CODING_ENV_SCHEMA_VERSION)
        self.assertEqual(loaded["os"]["packages"], ["build-essential"])
        self.assertEqual(loaded["languages"]["python"]["manager"], "uv")
        self.assertEqual(loaded["startup"]["commands"], ["uv sync"])


class CmdStartHandlesUnsupportedSchemaVersionTests(unittest.TestCase):
    """Phase 1.1: when coding-environment.toml declares a schema_version this
    alcatrazer cannot parse, cmd_start surfaces the error to the user as a
    clean stderr line — not a Python traceback. The actionable message
    (`upgrade alcatrazer ...`) is what the user sees, with no internal frames
    or class names leaking.

    Routing: workspace-dir pointer present but workspace dir missing →
    workspace_ready=False → cmd_start routes to _first_run_after_init,
    which calls _load_coding_environment as its first real step. The
    exception fires there, before prison.build is invoked.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.project_dir / ".git").mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
        )
        (self.alcatraz_dir / "workspace-dir").write_text(".devspace-aaaa\n")
        # Deliberately do NOT create the workspace dir — workspace_ready
        # comes back False, routing to _first_run_after_init.
        self.coding_env_path = self.project_dir / "coding-environment.toml"
        self.coding_env_path.write_text(
            'schema_version = 99\n[languages.python]\nversion = "3.12"\n'
        )
        self.addCleanup(self.tmp.cleanup)

        self.prison = Mock(spec=Alcatraz)
        self.prison.image_exists.return_value = True

    def _run(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_start(self.project_dir, prison=self.prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_returns_exit_code_one(self):
        rc, _, _ = self._run()
        self.assertEqual(rc, 1)

    def test_stderr_names_the_offending_version(self):
        _, _, stderr = self._run()
        self.assertIn("99", stderr)

    def test_stderr_tells_user_to_upgrade(self):
        _, _, stderr = self._run()
        self.assertIn("upgrade alcatrazer", stderr.lower())

    def test_stderr_has_no_python_traceback(self):
        """Traceback / exception class name leaking would make the error look
        like a crash rather than a config issue."""
        _, _, stderr = self._run()
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("UnsupportedSchemaVersionError", stderr)

    def test_does_not_invoke_prison_build_or_start(self):
        """The schema check is a fast-fail gate; nothing downstream runs."""
        self._run()
        self.prison.build.assert_not_called()
        self.prison.start.assert_not_called()


class CmdStartHandlesMalformedTomlTests(unittest.TestCase):
    """Manual-test bug F/ERR 1: a typo in coding-environment.toml (e.g. a
    table header commented out without commenting out its assignments)
    surfaces as a raw Python traceback through tomllib. Symmetric to the
    schema-version handler — config-file mistakes should look like
    config-file mistakes, not tool crashes.

    Same routing as the schema test: workspace_ready=False so cmd_start
    runs _load_coding_environment, which in turn calls tomllib.load and
    raises TOMLDecodeError before any other work happens.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        (self.project_dir / ".git").mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
        )
        (self.alcatraz_dir / "workspace-dir").write_text(".devspace-aaaa\n")
        self.coding_env_path = self.project_dir / "coding-environment.toml"
        # Real-world shape of the bug: `[languages.node]` table header is
        # commented out, but its `version =` and `manager =` assignments
        # land back into the previous `[languages.python]` table where
        # those keys were already set — tomllib refuses with "Cannot
        # overwrite a value".
        self.coding_env_path.write_text(
            "[languages.python]\n"
            'version = "3.12"\n'
            'manager = "pip"\n'
            "\n"
            "# [languages.node]\n"
            'version = "22"\n'
            'manager = "yarn"\n'
        )
        self.addCleanup(self.tmp.cleanup)

        self.prison = Mock(spec=Alcatraz)

    def _run(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_start(self.project_dir, prison=self.prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_returns_exit_code_one(self):
        rc, _, _ = self._run()
        self.assertEqual(rc, 1)

    def test_stderr_names_the_config_file(self):
        """The user needs to know which file to edit; bare 'Cannot
        overwrite a value' wouldn't tell them."""
        _, _, stderr = self._run()
        self.assertIn("coding-environment.toml", stderr)

    def test_stderr_includes_tomllib_diagnostic(self):
        """tomllib already pinpoints the offending line + column —
        surface it verbatim so the user can jump straight to the typo."""
        _, _, stderr = self._run()
        self.assertIn("Cannot overwrite a value", stderr)

    def test_stderr_has_no_python_traceback(self):
        _, _, stderr = self._run()
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("TOMLDecodeError", stderr)

    def test_does_not_invoke_prison_build_or_start(self):
        self._run()
        self.prison.build.assert_not_called()
        self.prison.start.assert_not_called()


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
    """Step 3e: .env.example writer — emits a bracketed block with an
    ANTHROPIC_API_KEY placeholder so the user has concrete guidance on
    what to fill in before running `alcatrazer start`.

    Anti-leak rule: .env.example IS committed to the outer repo's git
    AND snapshotted into /workspace, so its content reaches the agent.
    The block MUST contain zero "alcatraz" / "alcatrazer" branding —
    markers instead use the generated workspace name (a neutral hex-
    suffixed tag), and the prose refers to "the workspace" rather than
    "the container" (also backend-agnostic)."""

    WORKSPACE_NAME = ".devspace-7f3a"
    # Markers drop the leading dot for readability; the workspace name's
    # hex suffix still makes them collision-resistant inside the file.
    BEGIN_MARKER = "# --- devspace-7f3a begin ---"
    END_MARKER = "# --- devspace-7f3a end ---"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _write(self):
        return start.write_env_example(self.project_dir, self.WORKSPACE_NAME)

    def _read(self) -> str:
        return (self.project_dir / ".env.example").read_text()

    def test_writes_env_example_at_repo_root(self):
        path = self._write()
        self.assertEqual(path, self.project_dir / ".env.example")
        self.assertTrue(path.is_file())

    def test_includes_anthropic_api_key_placeholder(self):
        self._write()
        self.assertIn("ANTHROPIC_API_KEY", self._read())

    def test_fresh_write_contains_marker_block(self):
        self._write()
        text = self._read()
        self.assertIn(self.BEGIN_MARKER, text)
        self.assertIn(self.END_MARKER, text)
        self.assertLess(text.index(self.BEGIN_MARKER), text.index(self.END_MARKER))

    def test_zero_alcatraz_branding_in_written_file(self):
        """Prevents leak: .env.example is committed + snapshotted, so the
        agent reads whatever we write here. Nothing may say 'alcatraz'."""
        self._write()
        self.assertNotIn("alcatraz", self._read().lower())

    def test_marker_uses_workspace_name_not_alcatrazer(self):
        """Marker must be the workspace name so updates are idempotent
        without leaking product branding into the committed file."""
        self._write()
        text = self._read()
        # Positive: workspace-name marker is there.
        self.assertIn("devspace-7f3a", text)
        # Negative: no alcatraz tokens in any form.
        lower = text.lower()
        self.assertNotIn("alcatrazer", lower)
        self.assertNotIn("alcatraz", lower)

    def test_prose_is_backend_agnostic(self):
        """No 'container' references — 'workspace' is the neutral term
        that survives a future podman / sysbox / VM backend."""
        self._write()
        self.assertNotIn("container", self._read().lower())
        self.assertIn("workspace", self._read().lower())

    def test_appends_block_when_file_exists_without_markers(self):
        """Target repo may already have its own .env.example; we append
        our block without touching existing content."""
        existing = self.project_dir / ".env.example"
        existing_content = "USER_VAR=1\nANOTHER=two\n"
        existing.write_text(existing_content)

        self._write()
        text = self._read()
        self.assertIn("USER_VAR=1", text)
        self.assertIn("ANOTHER=two", text)
        self.assertIn(self.BEGIN_MARKER, text)
        self.assertIn("ANTHROPIC_API_KEY", text)
        # User content appears before our appended block.
        self.assertLess(text.index("USER_VAR=1"), text.index(self.BEGIN_MARKER))

    def test_idempotent_when_markers_already_present(self):
        """Re-running on a file that already has our block rewrites it
        in place — no duplication, existing non-block content preserved."""
        self._write()  # first pass
        user_addition = "\n# user added below\nMY_VAR=x\n"
        path = self.project_dir / ".env.example"
        path.write_text(path.read_text() + user_addition)

        self._write()  # second pass
        text = self._read()
        # Exactly one block.
        self.assertEqual(text.count(self.BEGIN_MARKER), 1)
        self.assertEqual(text.count(self.END_MARKER), 1)
        # User addition preserved.
        self.assertIn("MY_VAR=x", text)
        # Placeholder still present.
        self.assertIn("ANTHROPIC_API_KEY", text)

    def test_returns_path_even_when_file_existed(self):
        (self.project_dir / ".env.example").write_text("EXISTING=1\n")
        result = self._write()
        self.assertEqual(result, self.project_dir / ".env.example")


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
            "# alcatrazer patterns (written by alcatrazer init)",
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


class NormalizeEnvContentTests(unittest.TestCase):
    """Step 4c: `_normalize_env_content` strips what the container will
    never see so a comment-only .env edit doesn't force an Alcatraz
    recreate. Rules:
      - Drop blank lines.
      - Drop full-line comments (leading whitespace then `#`).
      - Preserve order (duplicate keys with different values may matter
        to some parsers; conservative default).
      - Do NOT strip inline `# ...` tails on KEY=VALUE lines — docker's
        --env-file treats them as part of the value."""

    def test_drops_blank_lines(self):
        normalized = start._normalize_env_content("FOO=1\n\n\nBAR=2\n")
        self.assertEqual(normalized, b"FOO=1\nBAR=2")

    def test_drops_full_line_comments(self):
        normalized = start._normalize_env_content("# hello\nFOO=1\n# world\nBAR=2\n")
        self.assertEqual(normalized, b"FOO=1\nBAR=2")

    def test_drops_full_line_comments_with_leading_whitespace(self):
        """`    # indented comment` is still a comment line."""
        normalized = start._normalize_env_content("    # indented\nFOO=1\n")
        self.assertEqual(normalized, b"FOO=1")

    def test_preserves_inline_hash_as_part_of_value(self):
        """docker's --env-file reads `FOO=bar  # x` as value `bar  # x`,
        not as `bar` with a trailing comment. Stripping `# x` would
        change semantics, so we keep it."""
        normalized = start._normalize_env_content("FOO=bar  # not a comment\n")
        self.assertEqual(normalized, b"FOO=bar  # not a comment")

    def test_preserves_line_order(self):
        """Duplicate keys with different values can behave differently
        across parsers — be conservative, don't reorder."""
        normalized = start._normalize_env_content("B=2\nA=1\n")
        self.assertEqual(normalized, b"B=2\nA=1")

    def test_empty_or_comments_only_yields_empty_bytes(self):
        self.assertEqual(start._normalize_env_content(""), b"")
        self.assertEqual(start._normalize_env_content("# only\n\n# comments\n"), b"")


class EnvFileChangedTests(unittest.TestCase):
    """Step 4c: `.env` change detection for the Step 4 lifecycle.

    Four cases from install_method.md Step 4c:
      - absent .env + absent .hash.last → unchanged (greenfield repo)
      - absent .env + present .hash.last → changed (user removed .env;
        recreate drops the baked env vars)
      - present .env + hash matches .hash.last → unchanged
      - otherwise → changed (hash mismatch, or .env appeared where it
        previously didn't exist)

    Hash is SHA256 of the UTF-8-encoded NORMALIZED content (see
    NormalizeEnvContentTests), so comment-only edits don't count."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        self.env_path = self.project_dir / ".env"
        self.last_path = self.alcatraz_dir / "env.hash.last"

    def _seed_hash(self, content: str) -> None:
        """Write .hash.last as if save_env_snapshot had run with `content`."""
        normalized = start._normalize_env_content(content)
        self.last_path.write_text(hashlib.sha256(normalized).hexdigest() + "\n")

    def test_false_when_both_env_and_hash_absent(self):
        self.assertFalse(start.env_file_changed(self.project_dir))

    def test_true_when_env_absent_but_hash_present(self):
        """User had .env, snapshot was taken, then user deleted .env —
        recreate so the now-absent vars stop being baked into the Alcatraz."""
        self._seed_hash("FOO=1\n")
        self.assertTrue(start.env_file_changed(self.project_dir))

    def test_true_when_env_present_but_no_hash_yet(self):
        """Bootstrap: user added .env between two subsequent runs before
        any snapshot captured its state. Force recreate."""
        self.env_path.write_text("FOO=1\n")
        self.assertTrue(start.env_file_changed(self.project_dir))

    def test_false_when_hash_matches(self):
        self.env_path.write_text("FOO=1\n")
        self._seed_hash("FOO=1\n")
        self.assertFalse(start.env_file_changed(self.project_dir))

    def test_true_when_hash_differs(self):
        self.env_path.write_text("FOO=1\n")
        self._seed_hash("FOO=2\n")
        self.assertTrue(start.env_file_changed(self.project_dir))

    def test_false_when_only_comment_added(self):
        """The whole point of normalization: comment-only edits are a
        no-op from the container's perspective, so they must not
        trigger a recreate."""
        self._seed_hash("FOO=1\n")
        self.env_path.write_text("# explanation\nFOO=1\n")
        self.assertFalse(start.env_file_changed(self.project_dir))

    def test_false_when_only_blank_lines_added(self):
        self._seed_hash("FOO=1\nBAR=2\n")
        self.env_path.write_text("FOO=1\n\n\nBAR=2\n")
        self.assertFalse(start.env_file_changed(self.project_dir))

    def test_true_when_inline_hash_value_changes(self):
        """Inline `# ...` is part of the value — changing it IS a real edit."""
        self._seed_hash("FOO=bar  # x\n")
        self.env_path.write_text("FOO=bar  # y\n")
        self.assertTrue(start.env_file_changed(self.project_dir))


class SaveEnvSnapshotTests(unittest.TestCase):
    """Step 4c: `save_env_snapshot` — symmetric with
    save_coding_environment_snapshot. Writes the hash when .env exists,
    removes .hash.last when .env doesn't (so the "both absent"
    unchanged case survives across runs)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        self.env_path = self.project_dir / ".env"
        self.last_path = self.alcatraz_dir / "env.hash.last"

    def test_writes_hash_when_env_exists(self):
        self.env_path.write_text("FOO=1\n")
        start.save_env_snapshot(self.project_dir)
        expected = hashlib.sha256(start._normalize_env_content("FOO=1\n")).hexdigest()
        self.assertEqual(self.last_path.read_text().strip(), expected)

    def test_removes_hash_when_env_absent(self):
        """If .env was removed since the last snapshot, the stored hash
        becomes misleading — drop it so env_file_changed's "both absent"
        branch holds on the NEXT run (not just this one)."""
        self.last_path.write_text("deadbeef\n")
        start.save_env_snapshot(self.project_dir)
        self.assertFalse(self.last_path.exists())

    def test_no_op_when_env_absent_and_hash_absent(self):
        start.save_env_snapshot(self.project_dir)
        self.assertFalse(self.last_path.exists())

    def test_round_trips_through_env_file_changed(self):
        """After save_env_snapshot, env_file_changed returns False until
        the .env actually changes. Catches off-by-one bugs in newline /
        encoding handling."""
        self.env_path.write_text("A=1\nB=2\n")
        start.save_env_snapshot(self.project_dir)
        self.assertFalse(start.env_file_changed(self.project_dir))
        # Cosmetic edit (comment) — still unchanged.
        self.env_path.write_text("# meta\nA=1\nB=2\n")
        self.assertFalse(start.env_file_changed(self.project_dir))
        # Real edit.
        self.env_path.write_text("A=1\nB=3\n")
        self.assertTrue(start.env_file_changed(self.project_dir))


class SubsequentRunTests(unittest.TestCase):
    """Step 4: _subsequent_run branches on five signals per the lifecycle
    table in install_method.md:

      - rebuild       — needs_rebuild (would-be recipe vs on-disk)
      - toml_changed  — coding-environment.toml vs .last
      - env_changed   — .env hash vs env.hash.last
      - running       — is_running
      - exists        — exists (running OR stopped)

    Five cases:
      1. Fast path: running && no changes                    → no-op
      2. Full recreate: rebuild || env_changed               → stop/rm/build?/start
      3. Resume stopped: exists && !running && !rebuild      → resume (caches kept)
      4. Startup-only on running: running && toml_changed    → exec (no stop/rm)
      5. Fresh start: !exists                                → start (user rm'd)"""

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

    def _prison(self, running=False, rebuild=False, exec_rc=0, exists=True):
        p = Mock(spec=Alcatraz)
        p.is_running.return_value = running
        p.needs_rebuild.return_value = rebuild
        p.exists.return_value = exists
        p.exec.return_value = exec_rc
        return p

    def _seed_last(self, match: bool) -> None:
        """Write coding-environment.toml.last matching (or drifting from) current."""
        content = (self.project_dir / "coding-environment.toml").read_text()
        if not match:
            content += "\n# drift\n"
        (self.alcatraz_dir / "coding-environment.toml.last").write_text(content)

    def _run(self, prison, env_changed=False) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            patch.object(start, "env_file_changed", return_value=env_changed),
            patch.object(start, "launch_daemon_and_print") as self._launch_mock,
        ):
            rc = start._subsequent_run(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    # --- Case 1: fast path ------------------------------------------------

    def test_fast_path_when_running_and_nothing_changed(self):
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=False, exists=True)
        rc, out, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.start.assert_not_called()
        prison.resume.assert_not_called()
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        prison.exec.assert_not_called()
        self.assertIn("up to date", out.lower())

    # --- Case 2: full recreate (rebuild or env_changed) -------------------

    def test_full_rebuild_when_dockerfile_would_differ_and_running(self):
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=True, exists=True)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.generate_prison.assert_called_once()
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        prison.build.assert_called_once()
        prison.start.assert_called_once()
        prison.resume.assert_not_called()
        prison.exec.assert_called()

    def test_rebuild_when_stopped_skips_stop(self):
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=True, exists=True)
        self._run(prison)
        prison.stop.assert_not_called()
        prison.remove.assert_called_once()
        prison.build.assert_called_once()
        prison.start.assert_called_once()
        prison.resume.assert_not_called()

    def test_env_change_forces_full_recreate_without_rebuild(self):
        """`.env` changes must recreate the container (env is baked at
        `docker run` time), but must NOT rebuild the image — the recipe
        didn't change."""
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=False, exists=True)
        rc, out, _ = self._run(prison, env_changed=True)
        self.assertEqual(rc, 0)
        # Recreate: stop + remove + start.
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        prison.start.assert_called_once()
        # NOT a rebuild.
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.exec.assert_called()
        self.assertIn(".env", out.lower())

    # --- Case 3: resume stopped (writable layer preserved) ----------------

    def test_resumes_when_stopped_and_no_changes(self):
        """The key cache-preserving branch: stopped container with no
        drift → `docker start`, not `docker run`. Writable overlay
        (and mise/pip/npm caches) stay intact."""
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=False, exists=True)
        rc, out, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.resume.assert_called_once()
        # Critical: start (fresh docker run) MUST NOT fire — it would
        # discard the writable layer we're trying to preserve.
        prison.start.assert_not_called()
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        prison.exec.assert_called()  # startup commands re-run inside the resumed instance
        self.assertIn("resuming", out.lower())

    # --- Case 4: startup-only change on a running container ---------------

    def test_startup_only_change_on_running_keeps_container(self):
        """`[startup]` toml tweak on a healthy running Alcatraz — no need
        to stop, recreate, or rebuild. Just re-run the new commands
        live via exec."""
        self._seed_last(match=False)  # drift → toml_changed
        prison = self._prison(running=True, rebuild=False, exists=True)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        # NO disruption: no stop, no remove, no recreate.
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        prison.start.assert_not_called()
        prison.resume.assert_not_called()
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        # But DO re-exec the new startup commands.
        prison.exec.assert_called()

    # --- Case 5: fresh start (user externally removed the container) ------

    def test_fresh_start_when_container_absent(self):
        """User ran `docker rm workspace` behind our back. Start from
        scratch — no stop/remove (nothing to stop), no rebuild (recipe
        unchanged)."""
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=False, exists=False)
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()
        prison.resume.assert_not_called()
        prison.start.assert_called_once()
        prison.exec.assert_called()

    # --- Snapshot refreshes (both coding-env and env.hash.last) -----------

    def test_coding_env_snapshot_updated_on_success(self):
        self._seed_last(match=False)  # initially drifted
        prison = self._prison(running=True, rebuild=False, exists=True)
        self._run(prison)
        self.assertEqual(
            (self.alcatraz_dir / "coding-environment.toml.last").read_text(),
            (self.project_dir / "coding-environment.toml").read_text(),
        )

    def test_env_snapshot_saved_on_success(self):
        """Successful run must refresh env.hash.last so the next
        _subsequent_run doesn't misdetect the same .env as "changed"."""
        self._seed_last(match=True)
        (self.project_dir / ".env").write_text("FOO=1\n")
        prison = self._prison(running=True, rebuild=False, exists=True)
        self._run(prison, env_changed=True)  # force recreate so we exercise save path
        last_path = self.alcatraz_dir / "env.hash.last"
        self.assertTrue(last_path.exists())
        expected = hashlib.sha256(start._normalize_env_content("FOO=1\n")).hexdigest()
        self.assertEqual(last_path.read_text().strip(), expected)

    def test_startup_failure_skips_both_snapshot_saves(self):
        self._seed_last(match=False)
        original_coding_last = (self.alcatraz_dir / "coding-environment.toml.last").read_text()
        (self.project_dir / ".env").write_text("FOO=1\n")
        prison = self._prison(running=True, rebuild=False, exec_rc=7, exists=True)
        rc, _, _ = self._run(prison, env_changed=True)
        self.assertEqual(rc, 7)
        # Neither snapshot should be refreshed when startup fails — a
        # retry must see the same drift signals.
        self.assertEqual(
            (self.alcatraz_dir / "coding-environment.toml.last").read_text(),
            original_coding_last,
        )
        self.assertFalse((self.alcatraz_dir / "env.hash.last").exists())

    # --- Sync daemon wiring (Step 5.7e) ----------------------------------

    def test_sync_daemon_launched_on_fast_path(self):
        """Fast path self-heals the daemon: even when nothing about the
        Alcatraz changed, if the daemon died since last start we want
        it back. Helper's own stale-PID detection handles the no-op
        case when the daemon is already alive."""
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=False, exists=True)
        self._run(prison)
        self._launch_mock.assert_called_once_with(self.project_dir)

    def test_sync_daemon_launched_after_successful_recreate(self):
        """Full-recreate path ends at `docker run` for a fresh Alcatraz —
        daemon needs to come up alongside it."""
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=True, exists=True)
        self._run(prison)
        self._launch_mock.assert_called_once_with(self.project_dir)

    def test_sync_daemon_launched_after_resume(self):
        """Resume-stopped branch brings the container back without
        recreate — daemon still needs relaunching (it doesn't survive
        stop/clear cycles, only the container's writable layer does)."""
        self._seed_last(match=True)
        prison = self._prison(running=False, rebuild=False, exists=True)
        self._run(prison)
        self._launch_mock.assert_called_once_with(self.project_dir)

    def test_sync_daemon_not_launched_on_startup_failure(self):
        """Don't spawn a daemon against an Alcatraz whose startup
        commands failed — it would just log promote errors."""
        self._seed_last(match=True)
        prison = self._prison(running=True, rebuild=True, exec_rc=7, exists=True)
        self._run(prison)
        self._launch_mock.assert_not_called()


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

    def _run_capturing(self, host_has_creds: bool) -> tuple[int, str]:
        stdout = io.StringIO()
        with (
            patch.object(start, "_host_has_claude_creds", return_value=host_has_creds),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = start.cmd_init(self.project_dir, prison=self.prison)
        return rc, stdout.getvalue()

    def test_happy_path_returns_zero(self):
        self.assertEqual(self._run(), 0)

    def test_guidance_when_host_has_no_claude_creds(self):
        """Without host creds, user must populate ANTHROPIC_API_KEY in .env."""
        rc, out = self._run_capturing(host_has_creds=False)
        self.assertEqual(rc, 0)
        self.assertIn("ANTHROPIC_API_KEY", out)
        self.assertIn(".env", out)
        self.assertIn("alcatrazer start", out)

    def test_tells_user_which_files_to_commit_to_git(self):
        """User ergonomics: after writing configs, print the list + flag
        which ones go to version control (coding-environment.toml and
        .env.example are team-shared; .alcatrazer/config.toml is local)."""
        _, out = self._run_capturing(host_has_creds=True)
        self.assertIn("coding-environment.toml", out)
        self.assertIn(".env.example", out)
        self.assertIn("commit to git", out)

    def test_guidance_when_host_has_claude_creds(self):
        """With host creds, no credential prompt — just tell the user to start."""
        rc, out = self._run_capturing(host_has_creds=True)
        self.assertEqual(rc, 0)
        self.assertIn("alcatrazer start", out)
        # No prompting to populate .env when the mount will cover auth.
        self.assertNotIn("ANTHROPIC_API_KEY", out)

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

    # --- Phase 1.2.1: wizard self-explanation wiring + closing scrub ----

    def test_intro_panel_printed_before_writing_configuration(self):
        """The intro must appear before any other cmd_init output so its
        terms (`promote`, `Alcatraz`) are defined before `Use for promoted
        commits?` is asked. The integration test mocks the ask_* functions
        so they don't print; whatever phrases land on stdout BEFORE
        'Writing configuration...' come from the intro and must match."""
        _, out = self._run_capturing(host_has_creds=True)
        intro_idx = out.find("your repo")
        writing_idx = out.find("Writing configuration")
        self.assertGreater(intro_idx, -1, "intro panel not printed by cmd_init")
        self.assertGreater(writing_idx, intro_idx, "intro must precede 'Writing configuration'")

    def test_closing_with_creds_uses_alcatraz_vocabulary(self):
        """Replaces the old 'mounted into the workspace' / 'launch the
        workspace' phrasing with Alcatraz-native wording."""
        _, out = self._run_capturing(host_has_creds=True)
        closing_start = out.find("Generating Alcatraz recipe")
        self.assertGreater(closing_start, -1)
        closing = out[closing_start:]
        self.assertIn("they will be used by Alcatraz", closing)
        self.assertIn("build Alcatraz and run it with own git", closing)

    def test_closing_with_creds_drops_container_terms(self):
        _, out = self._run_capturing(host_has_creds=True)
        closing_start = out.find("Generating Alcatraz recipe")
        closing = out[closing_start:].lower()
        self.assertNotIn("workspace", closing)
        self.assertNotIn("the image", closing)

    def test_closing_without_creds_uses_alcatraz_vocabulary(self):
        """The API-key branch's trailing 'run alcatrazer start' line gets
        the same Alcatraz-vocabulary rewrite as the with-creds branch."""
        _, out = self._run_capturing(host_has_creds=False)
        closing_start = out.find("Generating Alcatraz recipe")
        closing = out[closing_start:]
        self.assertIn("build Alcatraz and run it with own git", closing)
        self.assertNotIn("the image", closing.lower())
        self.assertNotIn("the workspace", closing.lower())

    # --- Phase 1.2.5: closing message points at `alcatrazer visit` -------

    def test_closing_with_creds_includes_visit_hint(self):
        # After Phase 1.2.5, both closing branches mention `alcatrazer
        # visit` so users know the next step after start completes.
        _, out = self._run_capturing(host_has_creds=True)
        closing_start = out.find("Generating Alcatraz recipe")
        closing = out[closing_start:]
        self.assertIn("`alcatrazer visit`", closing)
        self.assertIn("step inside", closing)

    def test_closing_without_creds_includes_visit_hint(self):
        _, out = self._run_capturing(host_has_creds=False)
        closing_start = out.find("Generating Alcatraz recipe")
        closing = out[closing_start:]
        self.assertIn("`alcatrazer visit`", closing)
        self.assertIn("step inside", closing)

    def test_workspace_name_flows_into_exclude(self):
        self.mocks["generate_workspace_dir_name"].return_value = ".devspace-zzzz"
        self._run()
        exclude_call = self.mocks["write_git_exclude"].call_args
        self.assertEqual(exclude_call.args[1], ".devspace-zzzz")

    def test_creates_alcatrazer_directory_up_front(self):
        """Regression guard: cmd_init owns `.alcatrazer/` and must mkdir
        it explicitly before any step that writes into it. Previously the
        directory was created implicitly by write_alcatrazer_config's
        mkdir side-effect; the 3f-before-3e reorder (workspace-name
        markers) moved store_workspace_dir ahead of that, which smoke
        caught as FileNotFoundError on `.alcatrazer/workspace-dir`.

        This test runs store_workspace_dir for real (no mock) so the
        directory-missing failure would surface in unit tests, not only
        in CI smoke."""
        real_store_called = []

        def real_store(alcatraz_dir_str: str, name: str) -> None:
            real_store_called.append((alcatraz_dir_str, name))
            Path(alcatraz_dir_str).joinpath("workspace-dir").write_text(f"{name}\n")

        with patch.object(identity, "store_workspace_dir", side_effect=real_store):
            rc = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue((self.project_dir / ".alcatrazer").is_dir())
        self.assertEqual(len(real_store_called), 1)


class FirstRunAfterInitTests(unittest.TestCase):
    """Orchestration of _first_run_after_init (Step 3.5): build → workspace
    snapshot → Alcatraz start → startup commands → save .last snapshot.
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
        # Phase 1.2.6: first_run uses image_matches(recipe_hash) instead
        # of bare image_exists(). Default: image is stale (or absent) →
        # first_run builds it. Tests exercising the "image already
        # current" branch override `image_matches.return_value = True`.
        self.prison.recipe_hash.return_value = "current_hash_abcd"
        self.prison.image_matches.return_value = False

        self.mocks: dict[str, Mock] = {}
        to_patch: list[tuple[object, str, object]] = [
            (start, "create_workspace", None),
            (start, "run_startup_commands", 0),
            (start, "save_coding_environment_snapshot", None),
            (start, "save_env_snapshot", None),
            (start, "launch_daemon_and_print", None),
            (identity, "load_workspace_dir", ".devspace-abcd"),
        ]
        for mod, name, rv in to_patch:
            p = patch.object(mod, name, return_value=rv)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def _run(self) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return start._first_run_after_init(self.project_dir, prison=self.prison)

    def _run_capturing(self) -> tuple[int, str]:
        """Capture stdout for tests that assert on post-success output."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            rc = start._first_run_after_init(self.project_dir, prison=self.prison)
        return rc, stdout.getvalue()

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
        """cmd_init already collected the user's answers and wrote the
        config files; don't reprompt or rewrite them. The Dockerfile is
        a separate concern — it's regenerated from the (possibly edited)
        coding_env on the rebuild path; see
        test_dockerfile_regenerated_before_build_when_image_stale."""
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
        self.mocks["save_env_snapshot"].assert_not_called()

    def test_env_snapshot_saved_on_happy_path(self):
        """First run primes env.hash.last so the next _subsequent_run
        doesn't misdetect the same .env as changed."""
        self._run()
        self.mocks["save_env_snapshot"].assert_called_once_with(self.project_dir)

    def test_sync_daemon_launched_on_happy_path(self):
        """First-run happy path wires up the sync daemon — Step 5.7e."""
        self._run()
        self.mocks["launch_daemon_and_print"].assert_called_once_with(self.project_dir)

    def test_sync_daemon_not_launched_on_startup_failure(self):
        """If startup commands failed we don't have a healthy Alcatraz;
        launching the sync daemon would just race with the failure."""
        self.mocks["run_startup_commands"].return_value = 7
        self._run()
        self.mocks["launch_daemon_and_print"].assert_not_called()

    def test_build_skipped_when_image_is_current(self):
        """_first_run_after_init is called from two routing paths now
        (stale/missing image OR no workspace). When only the workspace
        is missing AND the image's config_hash matches the current
        recipe, rebuilding the image is wasted work — skip it."""
        self.prison.image_matches.return_value = True
        self._run()
        self.prison.build.assert_not_called()
        # Workspace still gets created + container still starts.
        self.mocks["create_workspace"].assert_called_once()
        self.prison.start.assert_called_once()

    def test_workspace_creation_skipped_when_workspace_already_populated(self):
        """Manual-test bug B/ERR 1: editing coding-environment.toml after a
        successful start routes back through _first_run_after_init (image
        hash changed). The workspace dir from the prior run is still on
        disk with its `.git/` populated — re-running create_workspace
        crashes because `git init` exits 128 on a directory whose `.git/`
        already contains files (often owned by the phantom agent UID from
        the previous container run).

        Image-rebuild and workspace-creation are independent concerns: when
        only the image is stale, leave the existing workspace alone."""
        # Workspace from the prior run — `.git/` already initialized.
        (self.project_dir / ".devspace-abcd" / ".git").mkdir(parents=True)
        # Image is stale (toml edit), workspace is fine.
        self.prison.image_matches.return_value = False
        self._run()
        self.prison.build.assert_called_once()
        self.mocks["create_workspace"].assert_not_called()
        self.prison.start.assert_called_once()

    def test_workspace_created_when_workspace_dir_absent(self):
        """Symmetric to the previous test: when the workspace dir is
        missing entirely (the `rm -rf .devspace-*` recovery scenario),
        we DO need to recreate it. Default setUp leaves the dir absent
        so this is just an explicit assertion of today's behavior."""
        self.assertFalse((self.project_dir / ".devspace-abcd").exists())
        self._run()
        self.mocks["create_workspace"].assert_called_once()

    def test_workspace_created_when_dir_exists_without_git(self):
        """Half-broken state: the workspace dir exists but `.git/` was
        wiped (or never finished initializing). Treat this as 'not
        populated' — recreate. The check has to be the inner `.git/`,
        not the workspace dir itself, because outer-side mkdir is
        idempotent and would otherwise mask half-broken workspaces."""
        (self.project_dir / ".devspace-abcd").mkdir()
        self._run()
        self.mocks["create_workspace"].assert_called_once()

    def test_build_runs_when_image_is_stale(self):
        """Phase 1.2.6: stale image → rebuild. Covers both the
        "no image" original case and the new "image present but
        built from older config" case the user reported (rm -rf
        .alcatrazer/ + init left a stale image visible)."""
        self.prison.image_matches.return_value = False
        self._run()
        self.prison.build.assert_called_once()

    def test_dockerfile_regenerated_before_build_when_image_stale(self):
        """Manual-test bug F/ERR (toml-edit-no-rebuild): editing
        coding-environment.toml between starts routes back here because
        the image's baked config_hash no longer matches the recipe hash
        the new toml would produce. But the on-disk .alcatrazer/Dockerfile
        is the OLD one — it's what built the (now-stale) image. Rebuilding
        from that file produces the same stale image with the same stale
        label, and the next start enters this branch again: an infinite
        no-op rebuild loop. `save_coding_environment_snapshot` then masks
        the failure by refreshing `.last` to match the current toml.

        Re-rendering the Dockerfile from the freshly-loaded coding_env
        before `build()` closes the loop — the build now produces an
        image whose label matches the current recipe."""
        self.prison.image_matches.return_value = False
        coding_env = start._load_coding_environment(self.project_dir)
        self._run()
        self.prison.generate_prison.assert_called_once_with(coding_env)
        prison_call_names = [c[0] for c in self.prison.mock_calls]
        self.assertLess(
            prison_call_names.index("generate_prison"),
            prison_call_names.index("build"),
        )

    def test_stale_container_removed_before_start(self):
        """Manual-test bug F/ERR 2: `rm -rf .alcatrazer/ .devspace-*/`
        followed by `alcatrazer init && start` collides with a leftover
        container from the prior session because the container name is
        derived from the canonical project path (deterministic across
        re-inits). 1.2.6 self-healed stale IMAGES via image_matches; the
        symmetric fix here self-heals stale CONTAINERS by removing them
        before docker run gets a chance to fail with `Conflict. The
        container name "..." is already in use`."""
        self.prison.exists.return_value = True
        self._run()
        self.prison.remove.assert_called_once()
        self.prison.start.assert_called_once()
        # Order matters: remove must precede start, otherwise docker run
        # still hits the conflict.
        prison_call_names = [c[0] for c in self.prison.mock_calls]
        self.assertLess(
            prison_call_names.index("remove"),
            prison_call_names.index("start"),
        )

    def test_no_remove_when_no_stale_container(self):
        """Default greenfield: no prior container exists, so we shouldn't
        invoke remove (it's a no-op for absent containers, but skipping
        it keeps the docker call count minimal and the trace easier to
        read in failures)."""
        self.prison.exists.return_value = False
        self._run()
        self.prison.remove.assert_not_called()
        self.prison.start.assert_called_once()

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
        self.mocks["save_env_snapshot"].assert_not_called()

    # --- Phase 1.2.5: post-success message points at `alcatrazer visit` --

    def test_post_success_message_directs_user_to_alcatrazer_visit(self):
        # After _first_run_after_init's happy path, the closing line
        # tells the user how to enter the running Alcatraz. The
        # phrasing "Ready. To enter the Alcatraz: alcatrazer visit"
        # ties to the readme's "step inside" diagram vocabulary.
        rc, out = self._run_capturing()
        self.assertEqual(rc, 0)
        self.assertIn("Ready. To enter the Alcatraz: alcatrazer visit", out)


class LoadExistingAlcatrazerConfigTests(unittest.TestCase):
    """Phase 1.2.3: detection helper for an alcatrazer-generated
    coding-environment.toml — used by cmd_init to ask the user whether
    to reuse the existing file rather than orphan it with a hex-suffix
    duplicate.

    The detection is deliberately conservative (require BOTH header
    markers): a false positive lets an already-good user file be
    "reused" with no real harm; a false negative reverts to today's
    hex-suffix behavior — annoying but never destroys content.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.target = self.project_dir / "coding-environment.toml"

    def _alcatrazer_v1_content(self) -> str:
        # Reuse the writer to get a guaranteed-real alcatrazer-generated
        # file, so the test doesn't drift from `_render_coding_environment`
        # output and break invisibly on a future header tweak.
        return start._render_coding_environment({"languages": {"python": {"version": "3.12"}}})

    def test_returns_none_when_file_missing(self):
        # No coding-environment.toml — no decision to make.
        self.assertIsNone(start._load_existing_alcatrazer_config(self.project_dir))

    def test_returns_parsed_dict_for_alcatrazer_generated_v1_file(self):
        self.target.write_text(self._alcatrazer_v1_content())
        result = start._load_existing_alcatrazer_config(self.project_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["languages"]["python"]["version"], "3.12")

    def test_returns_none_for_user_authored_file_without_markers(self):
        # User wrote their own TOML — looks valid but has no alcatrazer
        # header. Helper must return None so cmd_init falls back to
        # today's hex-suffix behavior (never clobber user content).
        self.target.write_text('schema_version = 1\n[languages.python]\nversion = "3.12"\n')
        self.assertIsNone(start._load_existing_alcatrazer_config(self.project_dir))

    def test_returns_none_when_only_one_header_marker_present(self):
        # Conservative threshold — both markers required. A single
        # matching line could happen by chance; both lines together is
        # a strong enough signal of alcatrazer authorship.
        self.target.write_text(
            "# Coding environment definition for this repository.\n"
            "# … but not the other marker line …\n"
            "schema_version = 1\n"
            '[languages.python]\nversion = "3.12"\n'
        )
        self.assertIsNone(start._load_existing_alcatrazer_config(self.project_dir))

    def test_returns_none_for_unknown_schema_version(self):
        # File claims schema_version = 99 — we don't have markers for
        # that schema, so we can't safely identify it as ours. Treat as
        # "don't recognize, fall back to current behavior" rather than
        # try to apply v1 markers and possibly misread.
        content = self._alcatrazer_v1_content().replace(
            "schema_version = 1",
            "schema_version = 99",
        )
        self.target.write_text(content)
        self.assertIsNone(start._load_existing_alcatrazer_config(self.project_dir))

    def test_treats_missing_schema_version_as_v1(self):
        # Files predating Phase 1.1 carry no schema_version field; the
        # validator treats them as v1, and detection should follow that
        # convention — old alcatrazer-generated files are still "ours".
        content = self._alcatrazer_v1_content().replace(
            "schema_version = 1\n",
            "",
        )
        self.target.write_text(content)
        self.assertIsNotNone(start._load_existing_alcatrazer_config(self.project_dir))

    def test_returns_none_for_invalid_toml(self):
        # Garbage in the file — don't crash, just bail to None so the
        # caller falls back to the bare wizard.
        self.target.write_text("this is { not valid toml = =\n")
        self.assertIsNone(start._load_existing_alcatrazer_config(self.project_dir))


class GeneratedMarkersTableTests(unittest.TestCase):
    """Phase 1.2.3 detection-table guard: every supported schema_version
    must have an entry in `_GENERATED_MARKERS_BY_SCHEMA`. When v2 lands
    with [provision], adding `2: (...)` is the contract — without this
    test, a future schema bump could silently lose detection."""

    def test_current_schema_has_marker_entry(self):
        self.assertIn(
            start.CODING_ENV_SCHEMA_VERSION,
            start._GENERATED_MARKERS_BY_SCHEMA,
            f"_GENERATED_MARKERS_BY_SCHEMA must have an entry for the "
            f"current schema version ({start.CODING_ENV_SCHEMA_VERSION}); "
            f"otherwise reuse-prompt detection silently breaks.",
        )

    def test_v1_markers_match_actual_header_lines(self):
        # The markers must literally appear in `_render_coding_environment`
        # output — otherwise detection of our own files fails.
        sample = start._render_coding_environment({"languages": {"python": {"version": "3.12"}}})
        for marker in start._GENERATED_MARKERS_BY_SCHEMA[1]:
            self.assertIn(marker, sample, f"v1 marker not in writer output: {marker!r}")


class CmdInitReusePromptTests(unittest.TestCase):
    """Phase 1.2.3: when an alcatrazer-generated coding-environment.toml
    already exists, cmd_init asks the user whether to reuse it (default
    Y, skip the languages/os/startup wizard) or run the wizard fresh
    (then ask whether to overwrite or fall back to hex-suffix).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        (self.project_dir / ".git").mkdir()
        self.addCleanup(self.tmp.cleanup)

        self.prison = Mock(spec=Alcatraz)

        # Pre-create an alcatrazer-style coding-environment.toml so the
        # reuse-prompt path triggers.
        self.existing = self.project_dir / "coding-environment.toml"
        self.existing.write_text(
            start._render_coding_environment({"languages": {"python": {"version": "3.10"}}})
        )

        self.mocks: dict[str, Mock] = {}
        to_patch: list[tuple[object, str, object]] = [
            (start, "ask_promotion_identity", ("Alice", "alice@example.com")),
            (start, "ask_coding_environment", {"languages": {"go": {"version": "1.22"}}}),
            (start, "write_alcatrazer_config", None),
            (start, "write_env_example", None),
            (start, "write_git_exclude", None),
            (start, "extract_package_source", None),
            (start, "write_python_symlink", None),
            (identity, "generate_workspace_dir_name", ".devspace-abcd"),
            (identity, "store_workspace_dir", None),
            (start, "_host_has_claude_creds", True),
        ]
        for mod, name, rv in to_patch:
            p = patch.object(mod, name, return_value=rv)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def _run_with_inputs(self, inputs: list[str]) -> tuple[int, str]:
        """Run cmd_init with patched input() returning the given answers.
        Returns (exit code, captured stdout)."""
        stdout = io.StringIO()
        with (
            patch("builtins.input", side_effect=iter(inputs)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = start.cmd_init(self.project_dir, prison=self.prison)
        return rc, stdout.getvalue()

    # --- Reuse path ------------------------------------------------------

    def test_accept_reuse_skips_languages_os_startup_wizard(self):
        # User answers "y" to "Reuse existing coding-environment.toml?"
        # → ask_coding_environment is NOT called (the wizard's expensive
        # part is skipped); the existing file's parsed content flows
        # downstream instead.
        rc, _ = self._run_with_inputs(["y"])
        self.assertEqual(rc, 0)
        self.mocks["ask_coding_environment"].assert_not_called()
        # The existing file must still be there, untouched.
        self.assertTrue(self.existing.is_file())

    def test_accept_reuse_with_enter_default(self):
        # Empty input == accept default (Y).
        rc, _ = self._run_with_inputs([""])
        self.assertEqual(rc, 0)
        self.mocks["ask_coding_environment"].assert_not_called()

    def test_reuse_passes_parsed_existing_data_to_prison_generator(self):
        # The recipe must be generated from the EXISTING config (python
        # 3.10), not from the mock `ask_coding_environment` return value
        # (go 1.22). Confirms we actually reuse the file rather than
        # accept-and-discard.
        self._run_with_inputs(["y"])
        call = self.prison.generate_prison.call_args
        coding_env = call.args[0]
        self.assertIn("python", coding_env["languages"])
        self.assertEqual(coding_env["languages"]["python"]["version"], "3.10")
        # And the mock-supplied "go" was NOT used.
        self.assertNotIn("go", coding_env["languages"])

    # --- Decline reuse → overwrite path ----------------------------------

    def test_decline_reuse_runs_wizard_and_overwrite_replaces_file(self):
        # User says "n" to reuse → wizard runs → "y" to overwrite →
        # canonical filename used; original content (python 3.10) is
        # replaced by the wizard's mock output (go 1.22).
        original_size = self.existing.stat().st_size
        rc, _ = self._run_with_inputs(["n", "y"])
        self.assertEqual(rc, 0)
        self.mocks["ask_coding_environment"].assert_called_once()
        # File still at canonical name, no hex suffix.
        self.assertTrue(self.existing.is_file())
        # And content has changed (python 3.10 → go 1.22 from the mock).
        self.assertIn("[languages.go]", self.existing.read_text())
        self.assertNotEqual(self.existing.stat().st_size, original_size)
        # No hex-suffix file was created.
        suffix_files = list(self.project_dir.glob("coding-environment-*.toml"))
        self.assertEqual(suffix_files, [])

    # --- Decline reuse → decline overwrite → hex suffix ------------------

    def test_decline_overwrite_falls_back_to_hex_suffix(self):
        # User says "n" to reuse → wizard runs → "n" to overwrite →
        # original file preserved; new content lands in
        # coding-environment-XXXX.toml.
        rc, _ = self._run_with_inputs(["n", "n"])
        self.assertEqual(rc, 0)
        self.mocks["ask_coding_environment"].assert_called_once()
        # Original preserved.
        self.assertIn("python", self.existing.read_text())
        # Hex-suffixed file exists with the new content.
        suffix_files = list(self.project_dir.glob("coding-environment-*.toml"))
        self.assertEqual(len(suffix_files), 1)
        self.assertIn("[languages.go]", suffix_files[0].read_text())


class CmdInitDoesNotPromptReuseForUserAuthoredConfigTests(unittest.TestCase):
    """Phase 1.2.3 regression guard: when an existing
    coding-environment.toml is user-authored (no alcatrazer header
    markers), cmd_init must NOT show the reuse prompt and must fall
    back to today's hex-suffix behavior — never overwrite user content
    just because it happens to share the canonical filename."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        (self.project_dir / ".git").mkdir()
        self.addCleanup(self.tmp.cleanup)

        # Pre-create a user-authored TOML — no alcatrazer header.
        self.user_file = self.project_dir / "coding-environment.toml"
        self.user_file.write_text(
            '# user\'s own config\nschema_version = 1\n[languages.python]\nversion = "3.10"\n'
        )

        self.prison = Mock(spec=Alcatraz)

        self.mocks: dict[str, Mock] = {}
        to_patch: list[tuple[object, str, object]] = [
            (start, "ask_promotion_identity", ("Alice", "alice@example.com")),
            (start, "ask_coding_environment", {"languages": {"go": {"version": "1.22"}}}),
            (start, "write_alcatrazer_config", None),
            (start, "write_env_example", None),
            (start, "write_git_exclude", None),
            (start, "extract_package_source", None),
            (start, "write_python_symlink", None),
            (identity, "generate_workspace_dir_name", ".devspace-abcd"),
            (identity, "store_workspace_dir", None),
            (start, "_host_has_claude_creds", True),
        ]
        for mod, name, rv in to_patch:
            p = patch.object(mod, name, return_value=rv)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def test_no_reuse_prompt_for_user_authored_config(self):
        # No reuse-prompt input is consumed here — if cmd_init asked
        # for one, the empty iter would raise StopIteration and the
        # test would error rather than just "pass without prompt".
        stdout = io.StringIO()
        with (
            patch("builtins.input", side_effect=iter([])),  # NO inputs available
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = start.cmd_init(self.project_dir, prison=self.prison)
        self.assertEqual(rc, 0)
        self.mocks["ask_coding_environment"].assert_called_once()
        # User's original file stays untouched at canonical name.
        self.assertIn("user's own config", self.user_file.read_text())
        # New file lands at hex-suffixed name (today's behavior).
        suffix_files = list(self.project_dir.glob("coding-environment-*.toml"))
        self.assertEqual(len(suffix_files), 1)


class CmdVisitTests(unittest.TestCase):
    """Phase 1.2.5: `alcatrazer visit` — open an interactive shell as agent
    inside the running Alcatraz. Wraps the new `Alcatraz.shell()` port
    method; errors explicitly when no alcatrazer setup exists or the
    Alcatraz isn't running. No auto-start, no command pass-through."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, prison) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_visit(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_errors_when_no_alcatrazer_dir(self):
        # No `.alcatrazer/` → user hasn't run `init`. Friendly message,
        # exit 1, no prison interaction.
        prison = Mock(spec=Alcatraz)
        rc, _, err = self._run(prison)
        self.assertEqual(rc, 1)
        self.assertIn("alcatrazer init", err)
        prison.shell.assert_not_called()

    def test_errors_when_alcatraz_not_running(self):
        # `.alcatrazer/` present but no running container. Explicit error
        # ("run `alcatrazer start` first") rather than auto-starting —
        # auto-start would hide rebuilds and daemon launches under what
        # should be a fast "drop me in" command.
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = False
        rc, _, err = self._run(prison)
        self.assertEqual(rc, 1)
        self.assertIn("alcatrazer start", err)
        prison.shell.assert_not_called()

    def test_calls_prison_shell_when_running(self):
        # Happy path: running alcatraz, `cmd_visit` delegates to
        # `prison.shell()`. The real shell() never returns (execvp), but
        # the mock returns None — code path tolerates both.
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, _, _ = self._run(prison)
        self.assertEqual(rc, 0)
        prison.shell.assert_called_once()


class CmdStopTests(unittest.TestCase):
    """Step 5 + 5.7f: `alcatrazer stop` — freeze the Alcatraz, then
    tell the sync daemon to finalize. Ordering is non-negotiable:
    docker down FIRST (so agents can't commit any more), then daemon
    shutdown (so the final sync sees a frozen inner repo)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # Patch daemon-lifecycle helpers — cmd_stop always calls them;
        # tests control the outcome via the return value.
        self.ShutdownResult = ShutdownResult
        shutdown_patcher = patch.object(start, "shutdown_sync_daemon")
        self.mock_shutdown = shutdown_patcher.start()
        self.mock_shutdown.return_value = ShutdownResult(
            outcome="no_daemon", synced_count=0, conflict_branches=[]
        )
        self.addCleanup(shutdown_patcher.stop)

        print_patcher = patch.object(start, "print_shutdown_result")
        self.mock_print_shutdown = print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def _run(self, prison=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_stop(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_returns_error_when_no_alcatrazer_setup(self):
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("alcatrazer init", err)
        # Daemon shutdown must not be attempted — there's no setup yet.
        self.mock_shutdown.assert_not_called()

    def test_noop_when_container_not_running_but_still_shuts_down_daemon(self):
        """An idle `alcatrazer stop` still calls daemon shutdown — a
        lingering daemon process should be reaped and state.json's
        flag cleared."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = False
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_not_called()
        self.assertIn("not running", out.lower())
        self.mock_shutdown.assert_called_once_with(self.project_dir)

    def test_stops_running_container(self):
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_called_once()
        self.assertIn("stopped", out.lower())

    def test_docker_stop_happens_before_daemon_shutdown(self):
        """The non-negotiable ordering rule: docker stop FIRST so
        agents can't commit while the daemon is doing its final sync."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True

        parent = Mock()
        parent.attach_mock(prison.stop, "prison_stop")
        parent.attach_mock(self.mock_shutdown, "shutdown_daemon")

        self._run(prison=prison)

        names = [call[0] for call in parent.mock_calls]
        self.assertLess(names.index("prison_stop"), names.index("shutdown_daemon"))

    def test_conflict_outcome_returns_nonzero(self):
        """Conflict during final sync — exit non-zero so the user knows
        to check their repository. Container remains stopped; daemon
        gone; unsynced commits safe in the Alcatraz workspace."""
        self.mock_shutdown.return_value = self.ShutdownResult(
            outcome="conflict", synced_count=1, conflict_branches=["feat/x"]
        )
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, _, _ = self._run(prison=prison)
        self.assertEqual(rc, 1)

    def test_failed_outcome_returns_nonzero(self):
        self.mock_shutdown.return_value = self.ShutdownResult(
            outcome="failed", synced_count=0, conflict_branches=[]
        )
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, _, _ = self._run(prison=prison)
        self.assertEqual(rc, 1)

    def test_timeout_outcome_returns_nonzero(self):
        self.mock_shutdown.return_value = self.ShutdownResult(
            outcome="timeout", synced_count=0, conflict_branches=[]
        )
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.is_running.return_value = True
        rc, _, _ = self._run(prison=prison)
        self.assertEqual(rc, 1)


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


class CmdClearTests(unittest.TestCase):
    """Step 5.5 + 5.7g: `alcatrazer clear` — throw away the Alcatraz
    runtime, preserve the workspace. Same docker-first-then-daemon
    ordering as cmd_stop; `docker rm` at the end; inner-repo dir
    explicitly NOT removed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # Patch daemon-lifecycle helpers — cmd_clear always calls them;
        # tests control the outcome via the return value.
        self.ShutdownResult = ShutdownResult
        shutdown_patcher = patch.object(start, "shutdown_sync_daemon")
        self.mock_shutdown = shutdown_patcher.start()
        self.mock_shutdown.return_value = ShutdownResult(
            outcome="no_daemon", synced_count=0, conflict_branches=[]
        )
        self.addCleanup(shutdown_patcher.stop)

        print_patcher = patch.object(start, "print_shutdown_result")
        self.mock_print_shutdown = print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def _run(self, prison=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_clear(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_returns_error_when_no_alcatrazer_setup(self):
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("alcatrazer init", err)
        self.mock_shutdown.assert_not_called()

    def test_noop_when_alcatraz_absent_still_reaps_daemon(self):
        """No container to remove, but a lingering daemon might still
        exist (user did `docker rm` manually) — shut it down regardless."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = False
        prison.is_running.return_value = False
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        self.mock_shutdown.assert_called_once_with(self.project_dir)
        self.assertIn("nothing to clear", out.lower())

    def test_removes_running_alcatraz(self):
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True
        rc, out, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        self.assertIn("cleared", out.lower())
        self.assertIn("workspace preserved", out.lower())
        # Abstract-layer naming rule (feedback_alcatraz_naming.md):
        # CLI-visible output must not leak Docker-specific vocabulary.
        self.assertNotIn("container", out.lower())

    def test_removes_stopped_alcatraz_without_calling_stop(self):
        """A stopped Alcatraz still exists and still has writable state
        to discard — remove, but don't bother calling stop on it."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = False
        rc, _, _ = self._run(prison=prison)
        self.assertEqual(rc, 0)
        prison.stop.assert_not_called()
        prison.remove.assert_called_once()

    def test_does_not_touch_image_or_config(self):
        """Clear is strictly the container — image survives, no
        rebuild / recipe regeneration."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True
        self._run(prison=prison)
        prison.generate_prison.assert_not_called()
        prison.build.assert_not_called()

    def test_ordering_docker_stop_then_daemon_then_docker_rm(self):
        """The three-step dance: docker down first, daemon finalizes,
        then discard container."""
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True

        parent = Mock()
        parent.attach_mock(prison.stop, "prison_stop")
        parent.attach_mock(self.mock_shutdown, "shutdown_daemon")
        parent.attach_mock(prison.remove, "prison_remove")

        self._run(prison=prison)

        names = [call[0] for call in parent.mock_calls]
        self.assertLess(names.index("prison_stop"), names.index("shutdown_daemon"))
        self.assertLess(names.index("shutdown_daemon"), names.index("prison_remove"))

    def test_conflict_outcome_returns_nonzero_but_still_removes_container(self):
        """A conflict during final sync means unsynced commits remain in
        the Alcatraz workspace (which we preserve), so it's safe to
        proceed with docker rm. Exit non-zero so the user is informed."""
        self.mock_shutdown.return_value = self.ShutdownResult(
            outcome="conflict", synced_count=1, conflict_branches=["feat/x"]
        )
        (self.project_dir / ".alcatrazer").mkdir()
        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True
        rc, _, _ = self._run(prison=prison)
        self.assertEqual(rc, 1)
        prison.remove.assert_called_once()


class CliClearTests(unittest.TestCase):
    """Step 5.5: `alcatrazer clear` CLI wiring."""

    def test_cli_clear_command_invokes_cmd_clear(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "clear"]),
            patch.object(start, "cmd_clear", return_value=0) as mock_clear,
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main()
        mock_clear.assert_called_once()
        self.assertEqual(cm.exception.code, 0)

    def test_cli_clear_propagates_nonzero_exit(self):
        with (
            patch.object(sys, "argv", ["alcatrazer", "clear"]),
            patch.object(start, "cmd_clear", return_value=1),
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


class CmdStartPostSuccessMessageTests(unittest.TestCase):
    """Phase 5 Step 5.5 (change_promotion_machinery.md L937-939):
    On successful cmd_start, a user-facing message must:
    - confirm Alcatrazer started
    - name the branch the workspace is bound to
    - explain that switching branches puts syncing on hold

    Per docs/coding_conventions.md "User-facing strings speak the
    user's language" — uses git vocabulary, no tool jargon.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        self.addCleanup(self.tmp.cleanup)

        # Initialise project_dir as a real git repo on feat/X so
        # cmd_start's detached-HEAD precondition (Phase 1) passes.
        subprocess.run(
            ["git", "init", "-b", "feat/X", str(self.project_dir)],
            capture_output=True,
            check=True,
        )
        for k, v in (
            ("user.name", "Outer User"),
            ("user.email", "user@outer.example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(
                ["git", "-C", str(self.project_dir), "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(self.project_dir), "commit", "--allow-empty", "-m", "initial"],
            capture_output=True,
            check=True,
        )

        # Minimal configs cmd_start expects.
        (self.alcatraz_dir / "config.toml").write_text(
            'coding_environment_file = "coding-environment.toml"\n'
        )
        (self.project_dir / "coding-environment.toml").write_text(
            '[languages.python]\nversion = "3.12"\nmanager = "pip"\n'
        )
        # Workspace pointer (workspace dir itself doesn't need to
        # exist; we mock the success routes below).
        (self.alcatraz_dir / "workspace-dir").write_text(".devspace-test\n")

        # State: pinned_branch is what the post-success message
        # references. snapshot.py sets this on real first run; for
        # the test we pre-populate it.
        state.update_state(self.alcatraz_dir, pinned_branch="feat/X")

    def _prison_ok(self) -> Mock:
        p = Mock(spec=Alcatraz)
        p.recipe_hash.return_value = "h"
        p.image_matches.return_value = True
        return p

    def test_post_success_message_names_branch_and_explains_hold(self):
        """Run cmd_start with both routes mocked to return 0 (success).
        Expected stdout (or stderr):
        - some confirmation Alcatrazer started
        - the branch name 'feat/X'
        - guidance about switching branches putting things on hold
        - no project jargon (pinned / promotion / outer / inner)
        """
        prison = self._prison_ok()
        # Force first-run path so workspace setup isn't checked at all.
        with (
            patch.object(start, "_first_run_after_init", return_value=0),
            patch.object(start, "_subsequent_run", return_value=0),
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = start.cmd_start(self.project_dir, prison=prison)

        self.assertEqual(rc, 0)
        out = stdout.getvalue() + stderr.getvalue()
        # Branch name appears verbatim (with quotes per house style).
        self.assertIn("'feat/X'", out)
        # Confirmation language present (started or running).
        self.assertRegex(out, r"\b(started|running)\b")
        # The hold-on-switch guidance: must mention switching branches
        # AND the "hold"/"pause" concept.
        lower = out.lower()
        self.assertIn("switch", lower)
        self.assertTrue(
            "hold" in lower or "pause" in lower,
            f"expected 'hold' or 'pause' in post-success message, got: {out!r}",
        )
        # User-language: no project jargon.
        for jargon in ("pinned", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


class _CmdStatusTestBase(unittest.TestCase):
    """Shared fixtures for cmd_status tests (Phase 5 Steps 5.1-5.3).

    Each test bootstraps:
    - project_dir as outer git repo on a named branch
    - workspace_dir (sibling .devspace-test) as inner repo with
      'Initial commit' inner_root
    - .alcatrazer/ with workspace-dir pointer + state.json
    - .alcatrazer/promotion-daemon.pid pointing at current process
      (so cmd_status's daemon-alive check passes)
    """

    WORKSPACE_NAME = ".devspace-test"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.alcatraz_dir = self.project_dir / ".alcatrazer"
        self.alcatraz_dir.mkdir()
        self.workspace_dir = self.project_dir / self.WORKSPACE_NAME
        self.addCleanup(self.tmp.cleanup)

    def _outer_on(self, branch: str) -> None:
        subprocess.run(
            ["git", "init", "-b", branch, str(self.project_dir)],
            capture_output=True,
            check=True,
        )
        for k, v in (
            ("user.name", "Outer User"),
            ("user.email", "user@outer.example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(
                ["git", "-C", str(self.project_dir), "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(self.project_dir), "commit", "--allow-empty", "-m", "initial outer"],
            capture_output=True,
            check=True,
        )

    def _workspace_with_initial(self) -> str:
        """Initialise inner repo + one empty Initial commit. Return inner_root SHA."""
        self.workspace_dir.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(self.workspace_dir)],
            capture_output=True,
            check=True,
        )
        for k, v in (
            ("user.name", "Patricia Garcia"),
            ("user.email", "patricia@inner.example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(
                ["git", "-C", str(self.workspace_dir), "config", k, v],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace_dir),
                "commit",
                "--allow-empty",
                "-m",
                "Initial commit",
            ],
            capture_output=True,
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.workspace_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _add_agent_commits(self, n: int) -> None:
        for i in range(n):
            Path(self.workspace_dir, f"agent{i}.py").write_text(f"# agent {i}\n")
            subprocess.run(
                ["git", "-C", str(self.workspace_dir), "add", "."],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(self.workspace_dir), "commit", "-m", f"agent: {i}"],
                capture_output=True,
                check=True,
            )

    def _write_workspace_pointer(self) -> None:
        Path(self.alcatraz_dir, "workspace-dir").write_text(self.WORKSPACE_NAME + "\n")

    def _write_daemon_pid(self) -> None:
        """Write current process PID so cmd_status's `os.kill(pid, 0)`
        liveness check passes."""
        Path(self.alcatraz_dir, "promotion-daemon.pid").write_text(f"{os.getpid()}\n")

    def _run_status(self) -> tuple[int, str, str]:
        """Run cmd_status and return (rc, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = status_mod.cmd_status(self.project_dir)
        return rc, stdout.getvalue(), stderr.getvalue()


class CmdStatusPausedStateTests(_CmdStatusTestBase):
    """Phase 5 Step 5.3 (change_promotion_machinery.md L931-932):
    cmd_status paused-state output when state.paused is non-null
    (working-tree conflict). The block must:

    - say the state is "paused"
    - name the pinned branch
    - emit the working-tree-conflict explanation with the
      Commit-or-stash actionable next step
    - render "Last sync: never" when last_promotion_time absent
    """

    def test_paused_state_renders_conflict_message_and_never_last_sync(self):
        # Outer on the pinned branch (so the held state isn't OFF_PIN —
        # the conflict is at the apply layer, not the pin layer).
        self._outer_on("feat/X")
        inner_root = self._workspace_with_initial()
        self._add_agent_commits(1)
        self._write_workspace_pointer()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            last_promoted=inner_root,
            # last_promotion_time deliberately absent — paused before
            # ever applying anything successfully.
            paused={"reason": "git am failed (exit 128): patch does not apply"},
        )
        self._write_daemon_pid()

        rc, out, _ = self._run_status()

        self.assertEqual(rc, 0)
        # Paused marker.
        self.assertIn("paused", out.lower())
        # Started-from line names the branch.
        self.assertIn("Started from", out)
        self.assertIn("'feat/X'", out)
        # Working-tree conflict explanation + actionable next step.
        self.assertIn("working tree", out.lower())
        self.assertRegex(out, r"[Cc]ommit or stash")
        # Pending commits = 1 (the agent commit waiting to apply).
        self.assertRegex(out, r"Pending commits:\s*1\b")
        # Last sync: never (last_promotion_time absent).
        self.assertIn("never", out.lower())
        # User-language: forbidden jargon absent.
        lower = out.lower()
        for jargon in ("pinned", "promoted", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


class CmdStatusHeldStateTests(_CmdStatusTestBase):
    """Phase 5 Step 5.2 (change_promotion_machinery.md L927-929):
    cmd_status held-state output when outer is off-pin (most common
    held case). The block must:

    - say the state is "on hold"
    - name BOTH branches (current + started-on) in the explanation
    - count pending commits accurately
    - render last sync time
    - give an actionable next step (the `git checkout <branch>` hint)
    """

    def test_off_pin_held_state_names_both_branches_and_pending_count(self):
        # Outer initialised on 'main'; feat/X also exists (so the
        # state is OFF_PIN, not PIN_DELETED).
        self._outer_on("main")
        subprocess.run(
            ["git", "-C", str(self.project_dir), "branch", "feat/X"],
            capture_output=True,
            check=True,
        )
        inner_root = self._workspace_with_initial()
        # 3 agent commits piled in workspace — pending count == 3.
        self._add_agent_commits(3)
        self._write_workspace_pointer()
        old = (datetime.now(UTC) - timedelta(minutes=23)).isoformat()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            last_promoted=inner_root,  # nothing promoted yet; 3 pending
            last_promotion_time=old,
        )
        self._write_daemon_pid()

        rc, out, _ = self._run_status()

        self.assertEqual(rc, 0)
        # Held marker.
        self.assertIn("on hold", out.lower())
        # Both branch names named (current AND started-on).
        self.assertIn("'main'", out)
        self.assertIn("'feat/X'", out)
        # Started-from line.
        self.assertIn("Started from", out)
        # Pending commit count = 3.
        self.assertRegex(out, r"Pending commits:\s*3\b")
        # Last sync uses minute-based relative time (23 minutes ago).
        self.assertRegex(out, r"\d+\s*minute")
        # Actionable hint: the `git checkout <branch>` command appears
        # somewhere in the output. textwrap may split it across line
        # boundaries (e.g. "...(`git\n  checkout feat/X`)..."), so we
        # normalize whitespace before searching for the command.
        normalized = " ".join(out.split())
        self.assertIn("git checkout feat/X", normalized)
        # User-language: forbidden jargon absent.
        lower = out.lower()
        for jargon in ("pinned", "promoted", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


class CmdStatusActiveStateTests(_CmdStatusTestBase):
    """Phase 5 Step 5.1 (change_promotion_machinery.md L923-925):
    cmd_status active-state output.

    Expected (spec L286-291, revised per user-language rule):
      Sync daemon running (PID <pid>)
        Started from:     '<branch>'  active
        Pending commits:  0
        Last sync:        <relative time, e.g. "2 minutes ago">
    """

    def test_active_state_renders_pid_branch_pending_zero_and_recent_sync(self):
        self._outer_on("feat/X")
        inner_root = self._workspace_with_initial()
        self._write_workspace_pointer()
        recent = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            # No pending: last_promoted == workspace tip (= inner_root since
            # no agent commits yet).
            last_promoted=inner_root,
            last_promotion_time=recent,
        )
        self._write_daemon_pid()

        rc, out, _ = self._run_status()

        self.assertEqual(rc, 0)
        # Daemon line names the PID.
        self.assertIn(str(os.getpid()), out)
        # Active state marker.
        self.assertIn("active", out)
        # Started-from line names the branch.
        self.assertIn("Started from", out)
        self.assertIn("feat/X", out)
        # Pending commits = 0.
        self.assertRegex(out, r"Pending commits:\s*0\b")
        # Last sync line carries a relative time including "minute".
        self.assertIn("Last sync", out)
        self.assertRegex(out, r"\d+\s*minute")
        # User-language: forbidden jargon does not appear (case-insensitive).
        lower = out.lower()
        for jargon in ("pinned", "promoted", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


class CmdClearBlocksOffPinPendingTests(_CmdStatusTestBase):
    """Phase 5 Step 5.7 (change_promotion_machinery.md L957-958):
    `alcatrazer clear` MUST block — with a user-friendly error and
    a non-zero exit — when the outer repo is off the start branch
    (or detached / start branch deleted) AND the workspace has
    pending agent commits not yet synced.

    Why default-deny: losing N hours of agent work because the user
    did `git checkout main` to inspect something and then ran
    `alcatrazer clear` from muscle memory is a real failure mode
    (spec L339-342). The explicit override is `--discard-pending`,
    tested separately in Step 5.8.

    Required behavior:
      - rc != 0
      - prison.stop / prison.remove NOT called (no docker damage)
      - shutdown_sync_daemon NOT called (no daemon reap)
      - error message names the pending count, BOTH branches
        (started-on + current), and BOTH recovery paths
        (`git checkout <branch>` and `--discard-pending`)
      - vocabulary follows the user-language rule (docs/
        coding_conventions.md): branch / commit / repository,
        no `pin` / `promotion` / `outer` / `inner` jargon.
    """

    def setUp(self):
        super().setUp()
        # cmd_clear today calls these unconditionally. The new
        # pre-check logic in Step 5.10 must short-circuit BEFORE
        # either is invoked — so we mock them out and assert "not
        # called" below.
        self.ShutdownResult = ShutdownResult
        shutdown_patcher = patch.object(start, "shutdown_sync_daemon")
        self.mock_shutdown = shutdown_patcher.start()
        self.mock_shutdown.return_value = ShutdownResult(
            outcome="no_daemon", synced_count=0, conflict_branches=[]
        )
        self.addCleanup(shutdown_patcher.stop)
        print_patcher = patch.object(start, "print_shutdown_result")
        self.mock_print_shutdown = print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def _run_clear(self, prison):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_clear(self.project_dir, prison=prison)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_blocks_when_off_pin_with_pending_commits(self):
        # Outer on 'main' but pin is 'feat/X' (which also exists) —
        # this is the OFF_PIN case, the common "user wandered" scenario.
        self._outer_on("main")
        subprocess.run(
            ["git", "-C", str(self.project_dir), "branch", "feat/X"],
            capture_output=True,
            check=True,
        )
        inner_root = self._workspace_with_initial()
        # 3 agent commits in workspace past last_promoted = inner_root.
        self._add_agent_commits(3)
        self._write_workspace_pointer()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            last_promoted=inner_root,
        )

        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True

        rc, out, err = self._run_clear(prison=prison)

        # Non-zero exit; no destructive action taken.
        self.assertNotEqual(rc, 0)
        prison.stop.assert_not_called()
        prison.remove.assert_not_called()
        self.mock_shutdown.assert_not_called()

        # Block message — accept either stream so we don't bind the
        # GREEN impl to a specific stream.
        message = out + err
        # Names the pending count (3 commits at stake).
        self.assertRegex(message, r"\b3\b")
        # Names BOTH branches (started-on + current).
        self.assertIn("'feat/X'", message)
        self.assertIn("'main'", message)
        # Names BOTH recovery paths. `git checkout <branch>` may be
        # broken across lines by wrap; normalize whitespace first.
        normalized = " ".join(message.split())
        self.assertIn("git checkout feat/X", normalized)
        self.assertIn("--discard-pending", message)
        # User-language: forbidden jargon absent.
        lower = message.lower()
        for jargon in ("pinned", "promoted", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


class CmdClearDiscardPendingTests(_CmdStatusTestBase):
    """Phase 5 Step 5.8 (change_promotion_machinery.md L960-961):
    `alcatrazer clear --discard-pending` is the explicit override
    of the Step 5.7 block — when the user knowingly wants to throw
    away unsynced agent commits (e.g. an experiment that turned
    out worse than `main`), the flag MUST let cmd_clear proceed
    with the normal tear-down sequence.

    Same setup as Step 5.7 (off-pin + pending — the case that
    would otherwise block), but with the flag set:
      - rc == 0
      - prison.stop / prison.remove called as usual
      - shutdown_sync_daemon called (final reap of the daemon)
      - the block-message vocabulary ("cannot clear", recovery
        hints) does NOT appear — the user got what they asked for
        without lecture.
    """

    def setUp(self):
        super().setUp()
        self.ShutdownResult = ShutdownResult
        shutdown_patcher = patch.object(start, "shutdown_sync_daemon")
        self.mock_shutdown = shutdown_patcher.start()
        self.mock_shutdown.return_value = ShutdownResult(
            outcome="no_daemon", synced_count=0, conflict_branches=[]
        )
        self.addCleanup(shutdown_patcher.stop)
        print_patcher = patch.object(start, "print_shutdown_result")
        self.mock_print_shutdown = print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def test_discard_pending_flag_proceeds_through_teardown(self):
        self._outer_on("main")
        subprocess.run(
            ["git", "-C", str(self.project_dir), "branch", "feat/X"],
            capture_output=True,
            check=True,
        )
        inner_root = self._workspace_with_initial()
        self._add_agent_commits(3)
        self._write_workspace_pointer()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            last_promoted=inner_root,
        )

        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_clear(self.project_dir, prison=prison, discard_pending=True)
        out = stdout.getvalue()
        err = stderr.getvalue()

        # Override succeeded; full tear-down ran.
        self.assertEqual(rc, 0)
        prison.stop.assert_called_once()
        prison.remove.assert_called_once()
        self.mock_shutdown.assert_called_once_with(self.project_dir)

        # No block-message content (the user explicitly opted in;
        # don't lecture them on the way out).
        message = out + err
        self.assertNotIn("cannot clear", message.lower())


class CmdClearProceedsOnPinWithPendingTests(_CmdStatusTestBase):
    """Phase 5 Step 5.9 (change_promotion_machinery.md L963-965):
    `alcatrazer clear` on-pin with pending commits — the case the
    block in Step 5.7 is NOT supposed to catch. The user is on the
    branch their agent has been syncing to; their pending commits
    are safe by definition (they're already destined for the branch
    the user is on). cmd_clear MUST proceed normally:

      - rc == 0
      - prison.stop / shutdown / prison.remove all called in order
      - shutdown_sync_daemon does the final sync drain onto the
        pinned branch (here mocked to return synced_count=3 so we
        can assert the count flows through to the user)
      - NO block-message ("cannot clear", "--discard-pending")
      - cmd_clear emits a user-facing acknowledgment naming the
        pending count AND the pinned branch — so the user sees
        what's being synced before the workspace is torn down.

    The acknowledgment is the new content GREEN 5.10 must add. It
    makes this test cleanly RED today (today's cmd_clear stdout
    is silent about pending count / branch when proceeding).
    """

    def setUp(self):
        super().setUp()
        self.ShutdownResult = ShutdownResult
        shutdown_patcher = patch.object(start, "shutdown_sync_daemon")
        self.mock_shutdown = shutdown_patcher.start()
        # Pretend the daemon drained 3 commits during its final sync.
        self.mock_shutdown.return_value = ShutdownResult(
            outcome="synced", synced_count=3, conflict_branches=[]
        )
        self.addCleanup(shutdown_patcher.stop)
        # Real print_shutdown_result so the user-facing message
        # composition (which IS the contract) is exercised.

    def test_on_pin_with_pending_proceeds_and_acknowledges_drain(self):
        # Outer ON the pin (feat/X). 3 pending agent commits in the
        # workspace — the drain target.
        self._outer_on("feat/X")
        inner_root = self._workspace_with_initial()
        self._add_agent_commits(3)
        self._write_workspace_pointer()
        state.update_state(
            self.alcatraz_dir,
            pinned_branch="feat/X",
            inner_root=inner_root,
            last_promoted=inner_root,
        )

        prison = Mock(spec=Alcatraz)
        prison.exists.return_value = True
        prison.is_running.return_value = True

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = start.cmd_clear(self.project_dir, prison=prison)
        out = stdout.getvalue()
        err = stderr.getvalue()
        message = out + err

        # Proceeds — full tear-down ran.
        self.assertEqual(rc, 0)
        prison.stop.assert_called_once()
        self.mock_shutdown.assert_called_once_with(self.project_dir)
        prison.remove.assert_called_once()

        # No block content — this path is the safe one.
        lower = message.lower()
        self.assertNotIn("cannot clear", lower)
        self.assertNotIn("--discard-pending", message)

        # New GREEN 5.10 content: cmd_clear acknowledges what it's
        # about to do, naming the pending count + pinned branch so
        # the user sees the drain happen, not silence.
        self.assertRegex(message, r"\b3\b")
        self.assertIn("'feat/X'", message)

        # User-language: forbidden jargon absent.
        for jargon in ("pinned", "promoted", "promotion", "outer ", "inner "):
            self.assertNotIn(jargon, lower)


if __name__ == "__main__":
    unittest.main()
