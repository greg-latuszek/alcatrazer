"""Tests for installer template files (Step 2 of the install plan).

The installer ships two templates inside the package:
  - coding-environment.toml — agent-visible, zero alcatrazer branding (Principle 2)
  - alcatrazer-config.toml  — gitignored, promotion identity + daemon defaults

The old pre-config-split template (alcatrazer.toml) must be removed.
"""

import tomllib
import unittest
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class CodingEnvironmentTemplateTests(unittest.TestCase):
    """coding-environment.toml lands in the target repo working tree — must not
    reveal the tool to agents (Principle 2)."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "coding-environment.toml"

    def test_exists(self):
        self.assertTrue(self.path.is_file(), f"Missing template: {self.path}")

    def test_is_valid_toml(self):
        with open(self.path, "rb") as f:
            tomllib.load(f)

    def test_zero_alcatrazer_branding(self):
        content = self.path.read_text().lower()
        self.assertNotIn(
            "alcatraz",
            content,
            "coding-environment.toml is visible in the agent workspace — "
            "it must contain zero alcatrazer branding (Principle 2).",
        )


class AlcatrazerConfigTemplateTests(unittest.TestCase):
    """alcatrazer-config.toml becomes .alcatrazer/config.toml in the target repo.
    It is gitignored and invisible to agents — branding is fine here, but the
    schema must match what promote.py and the daemon will consume."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "alcatrazer-config.toml"

    def test_exists(self):
        self.assertTrue(self.path.is_file(), f"Missing template: {self.path}")

    def test_is_valid_toml(self):
        with open(self.path, "rb") as f:
            tomllib.load(f)

    def test_points_to_coding_environment_file(self):
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        self.assertEqual(
            data.get("coding_environment_file"),
            "coding-environment.toml",
        )

    def test_has_promotion_section(self):
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        self.assertIn("promotion", data)
        self.assertIn("name", data["promotion"])
        self.assertIn("email", data["promotion"])

    def test_has_promotion_daemon_defaults(self):
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        daemon = data.get("promotion-daemon", {})
        self.assertEqual(daemon.get("interval"), 5)
        self.assertEqual(daemon.get("branches"), "all")
        self.assertEqual(daemon.get("mode"), "mirror")
        self.assertEqual(daemon.get("verbosity"), "normal")
        self.assertEqual(daemon.get("max_log_size"), 512)


class OldTemplateRemovedTests(unittest.TestCase):
    """The pre-config-split alcatrazer.toml template is replaced by the two
    files above — it must not linger in the package."""

    def test_old_template_removed(self):
        old = TEMPLATES_DIR / "alcatrazer.toml"
        self.assertFalse(
            old.exists(),
            "Old alcatrazer.toml template must be removed — replaced by "
            "coding-environment.toml and alcatrazer-config.toml.",
        )


if __name__ == "__main__":
    unittest.main()
