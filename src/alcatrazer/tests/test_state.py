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

from alcatrazer import schema, state


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


class RequireCompatibleWorkspaceTests(unittest.TestCase):
    """Phase 7 Step 7.2 — workspace-level refusal gate
    (change_promotion_machinery.md "Breaking-change posture").

    `state.validate_schema_version` (the dict-level validator) only
    catches v0.1.0 workspaces whose state.json was actually written.
    But v0.1.0 wrote state.json lazily — only on first
    `alcatrazer stop`/`clear`. A user who only ran `init` + `start` in
    v0.1.0 has NO state.json, so a schema-version check alone passes
    them through, and they hit a runtime AttributeError later when
    promote_once expects `inner_root` / `pinned_branch`.

    `require_compatible_workspace(alcatraz_dir)` is the workspace-level
    gate that composes all available signals:

      - state.json schema_version < SCHEMA_VERSION
      - presence of v0.1.0 side files: promoted-tips.json,
        paused-branches.json
      - presence of v0.1.0 marks files: promote-export-marks,
        promote-import-marks

    Each refusal caller (daemon._run_cycle_mirror, cmd_start,
    cmd_status, cmd_clear) invokes this once at entry. Silent on a
    truly fresh workspace (so `alcatrazer init` on a brand-new repo
    isn't blocked).

    setUp deliberately does NOT create alcatraz_dir — each test
    constructs the exact filesystem state its docstring describes so
    the precondition is visible at the call site, not hidden in
    setUp."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The path we'll pass to require_compatible_workspace. Not
        # created here — tests decide whether it should exist.
        self.alcatraz_dir = Path(self.tmp.name) / ".alcatrazer"
        self.addCleanup(self.tmp.cleanup)

    # --- silent (compatible / fresh) cases ---

    def test_silent_when_alcatraz_dir_does_not_exist(self):
        """Pre-init state: the `.alcatrazer/` directory has not been
        created yet. Refusing here would block every brand-new project."""
        self.assertFalse(self.alcatraz_dir.exists())
        state.require_compatible_workspace(self.alcatraz_dir)

    def test_silent_when_alcatraz_dir_is_empty(self):
        """Mid-init state: directory exists, no state files written yet."""
        self.alcatraz_dir.mkdir()
        state.require_compatible_workspace(self.alcatraz_dir)

    def test_silent_when_state_json_has_current_schema_version(self):
        """A workspace this alcatrazer just wrote. update_state stamps
        the current SCHEMA_VERSION, so the gate must accept it."""
        self.alcatraz_dir.mkdir()
        state.update_state(self.alcatraz_dir, pinned_branch="feat/X")
        state.require_compatible_workspace(self.alcatraz_dir)

    # --- state.json schema-version signal ---

    def test_raises_when_state_json_schema_version_is_old(self):
        """state.json from a v0.1.0 workspace where `alcatrazer stop`
        or `clear` ran at least once — the file exists with v=1."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "state.json").write_text(
            json.dumps({"schema_version": 1, "daemon_shutdown": "requested"})
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.require_compatible_workspace(self.alcatraz_dir)

    # --- legacy side-file signals (one test per artifact) ---

    def test_raises_when_promoted_tips_json_present(self):
        """v0.1.0 wrote promoted-tips.json as a side file tracking the
        last-promoted commit per branch. v0.1.1 folds this into
        state.json as `last_promoted`."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "promoted-tips.json").write_text("{}")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("promoted-tips.json", str(cm.exception))

    def test_raises_when_paused_branches_json_present(self):
        """v0.1.0 wrote paused-branches.json as a side file. v0.1.1
        folds this into state.json as `paused`."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "paused-branches.json").write_text("{}")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("paused-branches.json", str(cm.exception))

    def test_raises_when_promote_export_marks_present(self):
        """v0.1.0's promote.py wrote git-fast-export marks files for
        incremental export. v0.1.1 uses format-patch/am instead, with
        no marks. THIS is the signal that catches workspaces v0.1.0
        ran but never stopped — marks were written every daemon cycle,
        independent of state.json."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "promote-export-marks").write_text("")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("promote-export-marks", str(cm.exception))

    def test_raises_when_promote_import_marks_present(self):
        """Paired with promote-export-marks. Either one alone is enough
        to identify a v0.1.0 workspace."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "promote-import-marks").write_text("")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("promote-import-marks", str(cm.exception))

    # --- the design-intent test: legacy artifact WITHOUT state.json ---

    def test_raises_on_legacy_marks_even_when_state_json_absent(self):
        """The scenario the multi-signal gate exists for: v0.1.0 user
        ran init + start but never stop/clear, so state.json was never
        created. A schema-version-only gate would let this through; the
        marks-file signal catches it."""
        self.alcatraz_dir.mkdir()
        self.assertFalse((self.alcatraz_dir / "state.json").exists())
        (self.alcatraz_dir / "promote-export-marks").write_text("")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.require_compatible_workspace(self.alcatraz_dir)

    # --- upgrade message reaches the user in artifact-based refusals too ---

    def test_legacy_artifact_message_includes_upgrade_steps(self):
        """Whichever signal trips the gate, the user gets the same
        upgrade procedure. Otherwise an artifact-based refusal would
        leave the user stranded without recovery instructions."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "paused-branches.json").write_text("{}")
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        msg = str(cm.exception)
        self.assertIn("alcatrazer stop", msg)
        self.assertIn("alcatrazer init", msg)
        self.assertIn("alcatrazer start", msg)
        self.assertIn("CHANGELOG", msg)

    # --- .alcatrazer/config.toml signals (Step 7.4) ---
    #
    # v0.1.0's config.toml had no schema_version field at all; v0.1.1
    # introduces the field plus removes [promotion-daemon].mode and
    # [promotion-daemon].branches. The gate fires on three sub-signals
    # so workspaces upgraded by hand (rather than via re-init) still
    # get caught:
    #
    #   - schema_version field absent → v0.1.0 config
    #   - schema_version < current → manually bumped to wrong version
    #   - obsolete [promotion-daemon].mode / .branches keys still
    #     present → defence-in-depth against half-upgrades

    def _config_current_version(self) -> int:
        """The version the gate currently considers 'current' for
        .alcatrazer/config.toml. Used to keep the silent-when-current
        test in lockstep with future schemas.json bumps."""
        return schema.ALCATRAZER_CONFIG.current_version

    def test_raises_when_config_toml_lacks_schema_version(self):
        """v0.1.0's .alcatrazer/config.toml had no schema_version
        field. The absence IS the signal — no released alcatrazer ever
        omitted it on purpose."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            '[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("config.toml", str(cm.exception))

    def test_raises_when_config_toml_schema_version_is_old(self):
        """Hand-crafted intermediate or partial migration — defence-
        in-depth catches it even though no released alcatrazer ever
        wrote schema_version=1 in config.toml."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            'schema_version = 1\n[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError):
            state.require_compatible_workspace(self.alcatraz_dir)

    def test_raises_when_config_toml_has_legacy_mode_key(self):
        """Even a current-version-stamped config trips the gate when
        the obsolete [promotion-daemon].mode is still present —
        protects users who hand-bumped schema_version without removing
        the deprecated keys. The legacy key takes precedence over the
        version field."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            f"schema_version = {self._config_current_version()}\n"
            '[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
            '[promotion-daemon]\nmode = "mirror"\n'
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("mode", str(cm.exception))

    def test_raises_when_config_toml_has_legacy_branches_key(self):
        """Same defence-in-depth check for [promotion-daemon].branches."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            f"schema_version = {self._config_current_version()}\n"
            '[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
            '[promotion-daemon]\nbranches = "all"\n'
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        self.assertIn("branches", str(cm.exception))

    def test_silent_when_config_toml_is_current_with_no_legacy_keys(self):
        """A current-version, legacy-key-free config passes through.
        Uses schema.ALCATRAZER_CONFIG.current_version so the test
        tracks future bumps automatically."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            f"schema_version = {self._config_current_version()}\n"
            '[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
            "[promotion-daemon]\ninterval = 5\n"
        )
        state.require_compatible_workspace(self.alcatraz_dir)

    def test_config_toml_refusal_message_includes_upgrade_steps(self):
        """Config-toml-based refusal must surface the same upgrade
        steps as the state.json and legacy-artifact refusals."""
        self.alcatraz_dir.mkdir()
        (self.alcatraz_dir / "config.toml").write_text(
            '[promotion]\nname = "Alice"\nemail = "alice@example.com"\n'
        )
        with self.assertRaises(state.UnsupportedStateSchemaVersionError) as cm:
            state.require_compatible_workspace(self.alcatraz_dir)
        msg = str(cm.exception)
        self.assertIn("alcatrazer stop", msg)
        self.assertIn("alcatrazer init", msg)
        self.assertIn("alcatrazer start", msg)
        self.assertIn("CHANGELOG", msg)


if __name__ == "__main__":
    unittest.main()
