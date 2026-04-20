"""Tests for the `alcatrazer start` CLI skeleton (Step 3a).

Only the routing: `.alcatrazer/` presence decides whether we enter the
first-time setup flow or the subsequent-run flow. Both branches are
placeholders at this step — we only assert which one is called.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import cli, start


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


if __name__ == "__main__":
    unittest.main()
