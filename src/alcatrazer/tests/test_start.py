"""Tests for the `alcatrazer start` CLI skeleton.

Step 3a — routing: `.alcatrazer/` presence decides first-time vs subsequent.
Step 3b — first-time flow aborts unless run at a git repo root.
"""

import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
