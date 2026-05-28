"""Unit tests for the host-side git funnel.

The funnel's job is to autodetect whether a `-C <repo>` argument targets
the inner Alcatraz workspace (which needs `-c safe.directory=<repo>` to
bypass Git's dubious-ownership refusal for the foreign-UID-owned dir) or
an ordinary host repo (plain `git`). These tests lock both branches and
the surrounding plumbing (cache, missing `-C`, kwarg forwarding)."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer.git_runner import run_git_command


class _RepoFixtureBase(unittest.TestCase):
    """Sets up a tempdir project with `.alcatrazer/workspace-dir` and the
    matching workspace dir, so subclasses can ask the funnel to operate on
    either the workspace path or an unrelated host-owned path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name).resolve()
        self.workspace_name = ".workspace-abc"
        (self.project_dir / ".alcatrazer").mkdir()
        (self.project_dir / ".alcatrazer" / "workspace-dir").write_text(self.workspace_name + "\n")
        self.workspace_path = self.project_dir / self.workspace_name
        self.workspace_path.mkdir()
        self.addCleanup(self._tmp.cleanup)


class TestRunGitCommandOnHostRepo(_RepoFixtureBase):
    """Repo is the user's outer git repo (or any host-owned path that is
    NOT the inner workspace) — funnel should emit plain `git -C <repo> …`
    with no safe.directory override."""

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_emits_plain_git_argv_when_target_is_not_the_workspace(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["-C", str(self.project_dir), "rev-parse", "HEAD"])
        sent_argv = mock_run.call_args.args[0]
        self.assertEqual(
            sent_argv,
            ["git", "-C", str(self.project_dir), "rev-parse", "HEAD"],
        )

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_emits_plain_git_argv_for_arbitrary_host_path_outside_any_project(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with tempfile.TemporaryDirectory() as elsewhere:
            run_git_command(["-C", elsewhere, "status"])
            sent_argv = mock_run.call_args.args[0]
            self.assertEqual(sent_argv, ["git", "-C", elsewhere, "status"])


class TestRunGitCommandOnInnerWorkspace(_RepoFixtureBase):
    """Repo IS the inner Alcatraz workspace — funnel must prepend
    `-c safe.directory=<workspace>` before the args so git won't refuse on
    "dubious ownership" when the workspace is owned by the container's
    agent UID."""

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_prepends_safe_directory_override_when_target_is_the_workspace(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["-C", str(self.workspace_path), "rev-list", "--count", "HEAD"])
        sent_argv = mock_run.call_args.args[0]
        self.assertEqual(
            sent_argv,
            [
                "git",
                "-c",
                f"safe.directory={self.workspace_path}",
                "-C",
                str(self.workspace_path),
                "rev-list",
                "--count",
                "HEAD",
            ],
        )


class TestRunGitCommandDispatchAutodetect(_RepoFixtureBase):
    """The autodetect re-reads `.alcatrazer/workspace-dir` on every call
    (no cache) — so workspace state changes underneath us (e.g. after
    `alcatrazer clear` removes the workspace, or a future recreate /
    multi-workspace flow) are reflected immediately. A cached result
    could silently miss the workspace case and skip the safe.directory
    override, putting us back in the silent-Pending-0 bug class."""

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_workspace_detection_is_re_read_on_every_call_not_cached(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        # First call: workspace-dir lists this path's name → detected as
        # workspace, safe.directory prepended.
        run_git_command(["-C", str(self.workspace_path), "rev-parse", "HEAD"])
        first_argv = mock_run.call_args.args[0]
        self.assertIn(f"safe.directory={self.workspace_path}", first_argv)
        # Mutate workspace-dir to a different name — the path is no longer
        # the configured workspace.
        (self.project_dir / ".alcatrazer" / "workspace-dir").write_text("other-name\n")
        # Second call must reflect the change (re-read, not cached) →
        # plain git, no safe.directory entry.
        run_git_command(["-C", str(self.workspace_path), "status"])
        second_argv = mock_run.call_args.args[0]
        self.assertNotIn("-c", second_argv)
        self.assertNotIn(f"safe.directory={self.workspace_path}", second_argv)

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_workspace_removal_under_us_is_seen_immediately(self, mock_run):
        """The `alcatrazer clear` scenario: workspace-dir file removed
        between calls. Next call must classify the path as not-workspace."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(["-C", str(self.workspace_path), "rev-parse", "HEAD"])
        (self.project_dir / ".alcatrazer" / "workspace-dir").unlink()
        run_git_command(["-C", str(self.workspace_path), "status"])
        second_argv = mock_run.call_args.args[0]
        self.assertNotIn("-c", second_argv)


class TestRunGitCommandArgsValidation(unittest.TestCase):
    """`-C <repo>` is the funnel's anchor for autodetect — args missing it
    leave the funnel unable to decide UID-safety, so it refuses upfront."""

    def test_raises_when_args_contain_no_dash_C(self):
        with self.assertRaisesRegex(ValueError, r"-C <repo>"):
            run_git_command(["status", "--porcelain"])

    def test_raises_when_dash_C_is_the_final_arg_with_no_path_following(self):
        with self.assertRaisesRegex(ValueError, r"-C <repo>"):
            run_git_command(["-C"])


class TestRunGitCommandKwargForwarding(_RepoFixtureBase):
    """check / text / input / env must flow through to subprocess.run
    untouched — binary commands (archive), patch-piping (am), and the
    am-with-rewritten-committer use case all depend on them."""

    @patch("alcatrazer.git_runner.subprocess.run")
    def test_forwards_check_text_input_and_env_to_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_git_command(
            ["-C", str(self.project_dir), "am"],
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
