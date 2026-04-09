"""
Tests for src/alcatrazer/identity.py — random agent identity and workspace directory generation.

Phase 1: Identity generation and storage.
Phase 2: Workspace directory name generation and storage.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── Unit tests: generate_identity ────────────────────────────────────


class TestGenerateIdentity(unittest.TestCase):
    """Verify random identity generation from name/email pools."""

    def test_returns_name_and_email(self):
        from alcatrazer import identity
        name, email = identity.generate_identity()
        self.assertIsInstance(name, str)
        self.assertIsInstance(email, str)

    def test_name_has_first_and_last(self):
        from alcatrazer import identity
        name, _ = identity.generate_identity()
        parts = name.split()
        self.assertEqual(len(parts), 2, f"Expected 'First Last', got '{name}'")

    def test_email_has_at_and_domain(self):
        from alcatrazer import identity
        _, email = identity.generate_identity()
        self.assertIn("@", email)
        local, domain = email.split("@")
        self.assertIn(".", domain)
        self.assertTrue(len(local) > 0)

    def test_deterministic_with_seed(self):
        """Same seed produces same identity."""
        from alcatrazer import identity
        a = identity.generate_identity(seed=42)
        b = identity.generate_identity(seed=42)
        self.assertEqual(a, b)

    def test_different_seeds_differ(self):
        from alcatrazer import identity
        a = identity.generate_identity(seed=1)
        b = identity.generate_identity(seed=2)
        self.assertNotEqual(a, b)

    def test_no_alcatraz_in_name(self):
        """Generated names must never contain 'alcatraz'."""
        from alcatrazer import identity
        for seed in range(100):
            name, _ = identity.generate_identity(seed=seed)
            self.assertNotIn("alcatraz", name.lower())

    def test_no_alcatraz_in_email(self):
        from alcatrazer import identity
        for seed in range(100):
            _, email = identity.generate_identity(seed=seed)
            self.assertNotIn("alcatraz", email.lower())

    def test_email_domain_is_from_pool(self):
        from alcatrazer import identity
        for seed in range(50):
            _, email = identity.generate_identity(seed=seed)
            domain = email.split("@")[1]
            self.assertIn(domain, identity.EMAIL_DOMAINS)

    def test_first_name_from_pool(self):
        from alcatrazer import identity
        for seed in range(50):
            name, _ = identity.generate_identity(seed=seed)
            first = name.split()[0]
            self.assertIn(first, identity.FIRST_NAMES)

    def test_last_name_from_pool(self):
        from alcatrazer import identity
        for seed in range(50):
            name, _ = identity.generate_identity(seed=seed)
            last = name.split()[1]
            self.assertIn(last, identity.LAST_NAMES)

    def test_email_patterns(self):
        """Email local part follows one of the defined patterns."""
        from alcatrazer import identity
        import re
        for seed in range(100):
            name, email = identity.generate_identity(seed=seed)
            local = email.split("@")[0]
            first = name.split()[0].lower()
            last = name.split()[1].lower()
            initial = first[0]
            # Pattern 1: firstname.lastname{0-2 digits}
            # Pattern 2: f_lastname{0-2 digits} or flastname{0-2 digits}
            pattern1 = re.compile(
                rf"^{re.escape(first)}\.{re.escape(last)}\d{{0,2}}$"
            )
            pattern2 = re.compile(
                rf"^{re.escape(initial)}_?{re.escape(last)}\d{{0,2}}$"
            )
            self.assertTrue(
                pattern1.match(local) or pattern2.match(local),
                f"Email local part '{local}' doesn't match any pattern "
                f"(name='{name}')",
            )

    def test_pool_sizes(self):
        """Pools have the expected minimum sizes."""
        from alcatrazer import identity
        self.assertGreaterEqual(len(identity.FIRST_NAMES), 50)
        self.assertGreaterEqual(len(identity.LAST_NAMES), 50)
        self.assertGreaterEqual(len(identity.EMAIL_DOMAINS), 20)


# ── Unit tests: store and retrieve identity ──────────────────────────


class TestStoreRetrieveIdentity(unittest.TestCase):
    """Verify identity persistence in .alcatraz/agent-identity."""

    def test_store_and_read_back(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            identity.store_identity(tmp, "James Smith", "james.smith@gmail.com")
            name, email = identity.load_identity(tmp)
            self.assertEqual(name, "James Smith")
            self.assertEqual(email, "james.smith@gmail.com")

    def test_file_format_is_two_lines(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            identity.store_identity(tmp, "Jane Doe", "jdoe@outlook.com")
            content = Path(tmp, "agent-identity").read_text()
            lines = content.strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], "Jane Doe")
            self.assertEqual(lines[1], "jdoe@outlook.com")

    def test_load_returns_none_if_missing(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            result = identity.load_identity(tmp)
            self.assertIsNone(result)

    def test_ensure_identity_generates_if_missing(self):
        """ensure_identity creates and stores a new identity if none exists."""
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            name, email = identity.ensure_identity(tmp)
            self.assertIsNotNone(name)
            self.assertIsNotNone(email)
            # Verify it was persisted
            stored = identity.load_identity(tmp)
            self.assertEqual(stored, (name, email))

    def test_ensure_identity_reuses_existing(self):
        """ensure_identity returns existing identity without regenerating."""
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            identity.store_identity(tmp, "Existing User", "existing@test.com")
            name, email = identity.ensure_identity(tmp)
            self.assertEqual(name, "Existing User")
            self.assertEqual(email, "existing@test.com")

    def test_ensure_identity_is_stable(self):
        """Calling ensure_identity twice returns the same identity."""
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            first = identity.ensure_identity(tmp)
            second = identity.ensure_identity(tmp)
            self.assertEqual(first, second)


# ── Unit tests: generate_workspace_dir_name ──────────────────────────


class TestGenerateWorkspaceDirName(unittest.TestCase):
    """Verify random workspace directory name generation."""

    def test_returns_string(self):
        from alcatrazer import identity
        name = identity.generate_workspace_dir_name()
        self.assertIsInstance(name, str)

    def test_starts_with_dot(self):
        """Directory name is hidden (starts with dot)."""
        from alcatrazer import identity
        name = identity.generate_workspace_dir_name()
        self.assertTrue(name.startswith("."), f"Expected hidden dir, got '{name}'")

    def test_pattern_word_dash_hex(self):
        """Matches .{word}-{4hex} pattern."""
        from alcatrazer import identity
        import re
        for seed in range(50):
            name = identity.generate_workspace_dir_name(seed=seed)
            self.assertRegex(
                name, r"^\.[a-z]+-[0-9a-f]{4}$",
                f"Name '{name}' doesn't match .word-hex4 pattern",
            )

    def test_deterministic_with_seed(self):
        from alcatrazer import identity
        a = identity.generate_workspace_dir_name(seed=42)
        b = identity.generate_workspace_dir_name(seed=42)
        self.assertEqual(a, b)

    def test_different_seeds_differ(self):
        from alcatrazer import identity
        a = identity.generate_workspace_dir_name(seed=1)
        b = identity.generate_workspace_dir_name(seed=2)
        self.assertNotEqual(a, b)

    def test_no_alcatraz_in_name(self):
        from alcatrazer import identity
        for seed in range(200):
            name = identity.generate_workspace_dir_name(seed=seed)
            self.assertNotIn("alcatraz", name.lower())

    def test_word_from_pool(self):
        from alcatrazer import identity
        for seed in range(50):
            name = identity.generate_workspace_dir_name(seed=seed)
            # Extract word part: .word-hex -> word
            word = name[1:].split("-")[0]
            self.assertIn(word, identity.WORKSPACE_WORDS)

    def test_pool_size(self):
        from alcatrazer import identity
        self.assertGreaterEqual(len(identity.WORKSPACE_WORDS), 30)


# ── Unit tests: generate_workspace_choices ───────────────────────────


class TestGenerateWorkspaceChoices(unittest.TestCase):
    """Verify 3 random choices are presented to the user."""

    def test_returns_three_choices(self):
        from alcatrazer import identity
        choices = identity.generate_workspace_choices()
        self.assertEqual(len(choices), 3)

    def test_choices_are_unique(self):
        from alcatrazer import identity
        choices = identity.generate_workspace_choices()
        self.assertEqual(len(set(choices)), 3)

    def test_choices_are_sorted(self):
        from alcatrazer import identity
        choices = identity.generate_workspace_choices()
        self.assertEqual(choices, sorted(choices))

    def test_calls_generate_workspace_dir_name(self):
        """Verify it delegates to generate_workspace_dir_name."""
        from alcatrazer import identity
        with patch.object(
            identity, "generate_workspace_dir_name",
            side_effect=[".devspace-0001", ".sandbox-0002", ".codelab-0003"],
        ) as mock:
            choices = identity.generate_workspace_choices()
            self.assertEqual(mock.call_count, 3)
            self.assertEqual(
                choices,
                [".codelab-0003", ".devspace-0001", ".sandbox-0002"],
            )


# ── Unit tests: store and retrieve workspace dir ─────────────────────


class TestStoreRetrieveWorkspaceDir(unittest.TestCase):
    """Verify workspace directory selection persistence."""

    def test_store_and_read_back(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            identity.store_workspace_dir(tmp, ".devspace-7f3a")
            result = identity.load_workspace_dir(tmp)
            self.assertEqual(result, ".devspace-7f3a")

    def test_load_returns_none_if_missing(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            result = identity.load_workspace_dir(tmp)
            self.assertIsNone(result)

    def test_file_contains_just_the_name(self):
        from alcatrazer import identity
        with tempfile.TemporaryDirectory() as tmp:
            identity.store_workspace_dir(tmp, ".sandbox-ab12")
            content = Path(tmp, "workspace-dir").read_text()
            self.assertEqual(content.strip(), ".sandbox-ab12")


if __name__ == "__main__":
    unittest.main()
