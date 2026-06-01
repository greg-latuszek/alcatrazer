"""Tests for alcatrazer.schema — the loader for schemas.json.

The JSON file is the source of truth, this module exposes typed
views. Tests fall into three groups:

  1. The loader parses schemas.json into the expected dataclass shape
     (one Schema per declared schema, each with an ascending history
     of SchemaRevision entries).
  2. Schema invariants hold: versions are unique within each history,
     ordered ascending, and the lookup helper returns the right entry.
  3. The version constants in state.py / start.py are derived from
     this module (single source of truth — adding an entry to
     schemas.json IS the bump). Cross-check guards against drift.

See docs/coding_conventions.md "Schema changes must land in
schemas.json + CHANGELOG before release" for the convention these
tests enforce.
"""

import json
import unittest
from pathlib import Path

from alcatrazer import schema, start, state


class SchemaJsonShapeTests(unittest.TestCase):
    """schemas.json's top-level shape is part of the contract — CI
    diff-checks and future non-Python clients depend on the keys being
    stable. Lock the shape in here so a stray rename surfaces immediately."""

    def setUp(self):
        self.raw = json.loads((Path(schema.__file__).parent / "schemas.json").read_text())

    def test_top_level_has_schemas_object(self):
        self.assertIn("schemas", self.raw)
        self.assertIsInstance(self.raw["schemas"], dict)

    def test_three_declared_schemas(self):
        """Three schemas tracked: state.json, .alcatrazer/config.toml,
        coding-environment.toml. Each gets a stable key in the JSON."""
        self.assertEqual(
            set(self.raw["schemas"].keys()),
            {"state", "alcatrazer_config", "coding_env"},
        )

    def test_each_schema_declares_file_role_and_history(self):
        """Every schema entry must carry the per-file metadata + at
        least one revision in its history — empty history is meaningless."""
        for name, entry in self.raw["schemas"].items():
            with self.subTest(schema=name):
                self.assertIn("file", entry)
                self.assertIn("role", entry)
                self.assertIn("history", entry)
                self.assertGreater(len(entry["history"]), 0)


class SchemaLoaderTests(unittest.TestCase):
    """The loader projects schemas.json into frozen dataclasses so
    callers get IDE-friendly access without re-parsing JSON. Validate
    the projection on the declared schemas."""

    def test_state_schema_is_a_schema_instance(self):
        self.assertIsInstance(schema.STATE, schema.Schema)
        self.assertEqual(schema.STATE.name, "state")
        self.assertEqual(schema.STATE.file, ".alcatrazer/state.json")

    def test_alcatrazer_config_schema_is_a_schema_instance(self):
        self.assertIsInstance(schema.ALCATRAZER_CONFIG, schema.Schema)
        self.assertEqual(schema.ALCATRAZER_CONFIG.name, "alcatrazer_config")
        self.assertEqual(schema.ALCATRAZER_CONFIG.file, ".alcatrazer/config.toml")

    def test_coding_env_schema_is_a_schema_instance(self):
        self.assertIsInstance(schema.CODING_ENV, schema.Schema)
        self.assertEqual(schema.CODING_ENV.name, "coding_env")
        self.assertEqual(schema.CODING_ENV.file, "coding-environment.toml")

    def test_history_entries_are_schema_revision_instances(self):
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                for rev in declared.history:
                    self.assertIsInstance(rev, schema.SchemaRevision)

    def test_schema_revision_field_lists_are_tuples(self):
        """Tuples not lists — the SchemaRevision dataclass is frozen
        and tuples are the immutable companion. Catches a JSON-load
        regression that would let callers mutate history at runtime."""
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            for rev in declared.history:
                with self.subTest(schema=declared.name, version=rev.version):
                    self.assertIsInstance(rev.fields_added, tuple)
                    self.assertIsInstance(rev.fields_removed, tuple)
                    self.assertIsInstance(rev.fields_changed, tuple)


class SchemaInvariantsTests(unittest.TestCase):
    """Invariants every schema's history must satisfy. Catches a
    malformed schemas.json edit (duplicate version, out-of-order
    bump) at first import, not at runtime in a refusal path."""

    def test_versions_are_unique_within_each_schema(self):
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                versions = [r.version for r in declared.history]
                self.assertEqual(len(versions), len(set(versions)))

    def test_versions_are_ascending_within_each_schema(self):
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                versions = [r.version for r in declared.history]
                self.assertEqual(versions, sorted(versions))

    def test_first_revision_is_version_one(self):
        """v=1 is the initial-shipped version of any schema. A history
        starting at v=2+ means we lost the v=1 record — refuse silently."""
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                self.assertEqual(declared.history[0].version, 1)

    def test_current_version_is_history_tail(self):
        """The 'current' version a writer stamps is always the latest
        history entry. Adding a new entry == bumping the version; no
        separate constant needed."""
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                self.assertEqual(declared.current_version, declared.history[-1].version)

    def test_revision_lookup_returns_matching_entry(self):
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            for rev in declared.history:
                with self.subTest(schema=declared.name, version=rev.version):
                    self.assertIs(declared.revision(rev.version), rev)

    def test_revision_lookup_returns_none_for_unknown_version(self):
        for declared in (schema.STATE, schema.ALCATRAZER_CONFIG, schema.CODING_ENV):
            with self.subTest(schema=declared.name):
                self.assertIsNone(declared.revision(999))


class ConstantsDerivedFromSchemasTests(unittest.TestCase):
    """state.SCHEMA_VERSION and start.CODING_ENV_SCHEMA_VERSION must
    derive from schemas.json (the user's "single source of truth"
    choice). The test catches a regression where a maintainer
    re-hardcodes the constant and forgets to bump schemas.json."""

    def test_state_constant_equals_state_schema_current_version(self):
        self.assertEqual(state.SCHEMA_VERSION, schema.STATE.current_version)

    def test_coding_env_constant_equals_coding_env_schema_current_version(self):
        self.assertEqual(start.CODING_ENV_SCHEMA_VERSION, schema.CODING_ENV.current_version)


class V1FieldsLockInTests(unittest.TestCase):
    """The v=1 entries describe what shipped in v0.1.0. They are
    historical records — once written, immutable. These tests pin the
    expected fields so a future "while you're in there" edit to v=1
    can't silently rewrite history."""

    def test_state_v1_fields_match_v0_1_0_writer(self):
        """v0.1.0's state.update_state stamped schema_version, the
        only caller (daemon_lifecycle.shutdown_sync_daemon) wrote
        daemon_shutdown. No other fields existed."""
        rev = schema.STATE.revision(1)
        self.assertEqual(rev.release, "v0.1.0")
        self.assertEqual(set(rev.fields_added), {"schema_version", "daemon_shutdown"})

    def test_alcatrazer_config_v1_fields_match_v0_1_0_template(self):
        """v0.1.0's templates/alcatrazer-config.toml. Note `[promotion-daemon].mode`
        and `.branches` exist here in v=1 and are removed in v=2."""
        rev = schema.ALCATRAZER_CONFIG.revision(1)
        self.assertEqual(rev.release, "v0.1.0")
        expected = {
            "coding_environment_file",
            "[promotion].name",
            "[promotion].email",
            "[promotion-daemon].interval",
            "[promotion-daemon].branches",
            "[promotion-daemon].mode",
            "[promotion-daemon].verbosity",
            "[promotion-daemon].max_log_size",
        }
        self.assertEqual(set(rev.fields_added), expected)

    def test_coding_env_v1_fields_match_v0_1_0_wizard(self):
        """v0.1.0's coding-environment.toml as produced by ask_coding_environment."""
        rev = schema.CODING_ENV.revision(1)
        self.assertEqual(rev.release, "v0.1.0")
        expected = {
            "schema_version",
            "[os].packages",
            "[languages.<name>].version",
            "[languages.<name>].manager",
            "[startup].commands",
        }
        self.assertEqual(set(rev.fields_added), expected)


if __name__ == "__main__":
    unittest.main()
