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
    """Schema version is stamped by every write — readers refuse files
    written by older alcatrazers rather than silently mis-reading them
    (see change_promotion_machinery.md "Breaking-change posture")."""

    def test_schema_version_is_two_for_this_release(self):
        """v0.1.1 reworked promotion (mirror via format-patch/am,
        unified state fields replacing promoted-tips/paused-branches
        side files). Old state.json layouts are not auto-migrated;
        the bump is what the gate keys off."""
        self.assertEqual(state.SCHEMA_VERSION, 2)


class ValidateSchemaVersionTests(unittest.TestCase):
    """Phase 7 schema gate (change_promotion_machinery.md
    "Breaking-change posture"): state.json from a pre-v0.1.1 workspace
    must be refused with a transparent upgrade message rather than
    silently mis-read — the v0.1.0 layout had scattered files
    (promoted-tips.json, paused-branches.json), no `pinned_branch` /
    `inner_root` / `last_promoted` / `paused` in state.json, and a
    different daemon contract.

    `load_state` stays best-effort (returns {} on missing/corrupt so
    fresh workspaces still bootstrap cleanly). The version check is a
    separate, mandatory step the refusal callers each invoke; the
    shutdown-intent reader stays best-effort because it's
    failure-tolerant by design."""

    def _msg_for(self, data: dict) -> str:
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.validate_schema_version(data)
        return str(cm.exception)

    def test_silent_when_data_empty(self):
        """Empty dict = state.json missing (fresh workspace) OR
        unreadable. Either way: nothing incompatible to flag — refusing
        here would block `alcatrazer init` on every brand-new project."""
        state.validate_schema_version({})  # must not raise

    def test_silent_when_schema_version_matches_current(self):
        """A state.json stamped by this same alcatrazer passes through.
        Reads schema_version from the constant so the test remains valid
        if SCHEMA_VERSION bumps again later."""
        state.validate_schema_version(
            {"schema_version": state.SCHEMA_VERSION, "pinned_branch": "feat/X"}
        )  # must not raise

    def test_raises_when_schema_version_is_pre_v0_1_1(self):
        """v0.1.0 wrote schema_version=1. Whole layout has shifted —
        reading the old file would leave pinned_branch / inner_root /
        last_promoted unset and silently degrade promotion."""
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.validate_schema_version({"schema_version": 1, "daemon_shutdown": "requested"})

    def test_raises_when_schema_version_is_future(self):
        """Forward-incompat: a future alcatrazer's state.json shouldn't
        be silently mis-read by this version either."""
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.validate_schema_version({"schema_version": state.SCHEMA_VERSION + 1})

    def test_raises_when_schema_version_field_absent_but_data_non_empty(self):
        """Every legitimate writer stamps schema_version (update_state
        does it unconditionally). A populated dict missing the field is
        hand-crafted or from an unreleased intermediate — refuse rather
        than guess."""
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.validate_schema_version({"daemon_shutdown": "requested"})

    # --- message content (per change_promotion_machinery.md "Breaking-change posture") ---

    def test_message_names_the_offending_schema_version(self):
        """User needs to know which version produced this layout so the
        match between 'schema 1' here and CHANGELOG entries is obvious."""
        self.assertIn("schema 1", self._msg_for({"schema_version": 1}))

    def test_message_announces_breaking_change(self):
        """Tell the user this isn't a recoverable bug — the upgrade path
        is wipe-and-reinit, not migrate-in-place."""
        self.assertIn("not backwards compatible", self._msg_for({"schema_version": 1}).lower())

    def test_message_includes_five_upgrade_steps(self):
        """Per the spec block: stop daemon, sudo-remove the inner
        workspace (root-owned container output requires sudo),
        rm -rf .alcatrazer/, init, start. All five cited explicitly so
        the user can copy-paste without re-reading the design doc."""
        msg = self._msg_for({"schema_version": 1})
        self.assertIn("alcatrazer stop", msg)
        self.assertIn("sudo rm -rf", msg)
        self.assertIn("workspace-dir", msg)
        self.assertIn(".alcatrazer", msg)
        self.assertIn("coding-environment.toml", msg)
        self.assertIn("alcatrazer init", msg)
        self.assertIn("alcatrazer start", msg)

    def test_message_points_to_changelog(self):
        """CHANGELOG carries the 'what changed and why' that motivates
        the upgrade — the message must direct the user there."""
        self.assertIn("CHANGELOG", self._msg_for({"schema_version": 1}))


if __name__ == "__main__":
    unittest.main()
