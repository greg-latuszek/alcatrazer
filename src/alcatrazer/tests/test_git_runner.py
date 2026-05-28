"""Unit tests for the host-side git funnel.

The funnel's only job is to prepend `-c safe.directory='*'` to every git
invocation, bypassing Git's dubious-ownership refusal so reads against
the foreign-UID-owned inner Alcatraz workspace can't silently return
empty/zero. These tests lock that single behaviour and the kwarg
forwarding to subprocess.run."""

import subprocess
import unittest
from unittest.mock import patch

from alcatrazer.git_runner import run_git_command


class TestRunGitCommandAlwaysPrependsSafeDirectoryWildcard(unittest.TestCase):
    @patch("alcatrazer.git_runner.subprocess.run")
    def test_prepends_safe_directory_wildcard_for_dash_C_invocation(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["-C", "/some/repo", "rev-parse", "HEAD"])
        self.assertEqual(
            mock_run.call_args.args[0],
            ["git", "-c", "safe.directory=*", "-C", "/some/repo", "rev-parse", "HEAD"],
        )

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_prepends_safe_directory_wildcard_for_init_with_positional_path(self, mock_run):
        """The funnel imposes no shape on args — `git init <path>` (which
        would mkdir the path) keeps working without rewriting the call."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["init", "-b", "main", "/tmp/new-repo"])
        self.assertEqual(
            mock_run.call_args.args[0],
            ["git", "-c", "safe.directory=*", "init", "-b", "main", "/tmp/new-repo"],
        )

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_prepends_safe_directory_wildcard_for_clone(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["clone", "https://example.com/r.git", "/tmp/dest"])
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                "git",
                "-c",
                "safe.directory=*",
                "clone",
                "https://example.com/r.git",
                "/tmp/dest",
            ],
        )


class TestRunGitCommandKwargForwarding(unittest.TestCase):
    """check / text / input / env must flow through to subprocess.run
    untouched — binary commands (archive), patch-piping (am), and the
    am-with-rewritten-committer use case all depend on them."""

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_forwards_check_text_input_and_env_to_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(
            ["-C", "/some/repo", "am"],
            check=True,
            text=False,
            input=b"<patch bytes>",
            env={"GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "a@x"},
        )
        kwargs = mock_run.call_args.kwargs
        self.assertTrue(kwargs["check"])
        self.assertFalse(kwargs["text"])
        self.assertEqual(kwargs["input"], b"<patch bytes>")
        self.assertEqual(
            kwargs["env"], {"GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "a@x"}
        )
        self.assertTrue(kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
