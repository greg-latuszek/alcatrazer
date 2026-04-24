"""Tests for alcatrazer.state — the tiny cooperation file backing
`.alcatrazer/state.json`.

Scope today: one flag (`daemon_shutdown`) used by `alcatrazer stop` /
`clear` to signal shutdown intent to the promotion daemon. Scope
tomorrow: this is the seed of the future infocenter layer (see
docs/features/refactor_for_infocenter.md) — the tests lock in the
file-lifecycle contract (always-exists, merge-on-write, atomic rename)
so adding fields later is one-at-a-time without surface churn.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import state


class LoadStateTests(unittest.TestCase):
    """load_state is a best-effort reader — callers must not crash on
    missing / corrupt / unexpected-shape file content."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alcatraz_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_returns_empty_dict_when_file_missing(self):
        self.assertEqual(state.load_state(self.alcatraz_dir), {})

    def test_returns_empty_dict_when_json_corrupt(self):
        (self.alcatraz_dir / "state.json").write_text("{not valid json")
        self.assertEqual(state.load_state(self.alcatraz_dir), {})

    def test_returns_empty_dict_when_json_is_not_an_object(self):
        """Top-level JSON array is valid JSON but the wrong shape — we
        expect a dict so callers can `.get(field)` safely without type
        juggling."""
        (self.alcatraz_dir / "state.json").write_text("[1, 2, 3]")
        self.assertEqual(state.load_state(self.alcatraz_dir), {})

    def test_returns_parsed_dict_on_valid_json(self):
        (self.alcatraz_dir / "state.json").write_text(
            '{"schema_version": 1, "daemon_shutdown": "requested"}'
        )
        result = state.load_state(self.alcatraz_dir)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["daemon_shutdown"], "requested")

    def test_returns_empty_when_alcatraz_dir_missing(self):
        """Before `alcatrazer init` has ever run, the directory doesn't
        exist yet — still best-effort, no crash."""
        nonexistent = self.alcatraz_dir / "never-created"
        self.assertEqual(state.load_state(nonexistent), {})


class UpdateStateTests(unittest.TestCase):
    """update_state is read-modify-write + atomic rename. Future fields
    merge in additively; existing fields survive unrelated updates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alcatraz_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _read(self) -> dict:
        return json.loads((self.alcatraz_dir / "state.json").read_text())

    def test_creates_file_when_missing(self):
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        self.assertEqual(self._read()["daemon_shutdown"], "requested")

    def test_always_stamps_schema_version(self):
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        self.assertEqual(self._read()["schema_version"], state.SCHEMA_VERSION)

    def test_merges_with_existing_unrelated_fields(self):
        """A new field's update must not clobber earlier unrelated
        fields — callers own non-overlapping concerns, file is shared."""
        (self.alcatraz_dir / "state.json").write_text(
            '{"schema_version": 1, "future_field": "kept"}'
        )
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        after = self._read()
        self.assertEqual(after["daemon_shutdown"], "requested")
        self.assertEqual(after["future_field"], "kept")

    def test_overwrites_same_field_on_repeat(self):
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        state.update_state(self.alcatraz_dir, daemon_shutdown="done")
        self.assertEqual(self._read()["daemon_shutdown"], "done")

    def test_creates_alcatraz_dir_if_absent(self):
        """Edge case: state write happens before any `.alcatrazer/`
        bootstrap step has run. Don't crash; mkdir -p."""
        deep = self.alcatraz_dir / "nested" / ".alcatrazer"
        state.update_state(deep, daemon_shutdown="requested")
        self.assertTrue((deep / "state.json").is_file())

    def test_atomic_write_preserves_old_content_on_rename_failure(self):
        """Simulate a crash during os.replace (e.g., disk full) — the
        existing file stays intact, not truncated / half-written.
        This is the guarantee that lets concurrent readers (the daemon)
        never observe a partially-written state."""
        (self.alcatraz_dir / "state.json").write_text(
            '{"schema_version": 1, "daemon_shutdown": "done"}\n'
        )
        with (
            patch("alcatrazer.state.os.replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        # Old content still there, not clobbered.
        self.assertEqual(self._read()["daemon_shutdown"], "done")

    def test_no_tmp_file_lingers_after_successful_write(self):
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested")
        self.assertFalse((self.alcatraz_dir / "state.json.tmp").exists())

    def test_multiple_fields_in_one_call(self):
        """Future callers may want to set several fields atomically
        rather than issuing multiple update_state calls."""
        state.update_state(self.alcatraz_dir, daemon_shutdown="requested", other="x")
        after = self._read()
        self.assertEqual(after["daemon_shutdown"], "requested")
        self.assertEqual(after["other"], "x")


class SchemaVersionTests(unittest.TestCase):
    """Schema version is stamped by every write — readers can migrate
    forward when the schema changes (post-v1, not today)."""

    def test_schema_version_is_one_for_this_release(self):
        self.assertEqual(state.SCHEMA_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
