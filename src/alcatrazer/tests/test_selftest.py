"""Tests for alcatrazer.selftest — factory + shared security-invariant base.

The assertions inside `_AlcatrazSecurityInvariants` are exercised only
against a running Alcatraz (integration test path); this file covers the
structural contract of the factory + base class.
"""

import unittest
from pathlib import Path

from alcatrazer import selftest


class MakeAlcatrazSelftestTestcaseTests(unittest.TestCase):
    """Factory returns a TestCase subclass bound to project_dir via closure —
    no global state, and `unittest discover` never picks the class up by
    accident (it only exists inside the factory call scope)."""

    def test_returns_a_unittest_testcase_subclass(self):
        TestCase = selftest.make_alcatraz_selftest_testcase(Path("/tmp"))
        self.assertTrue(issubclass(TestCase, unittest.TestCase))

    def test_subclasses_shared_security_invariants_base(self):
        TestCase = selftest.make_alcatraz_selftest_testcase(Path("/tmp"))
        self.assertTrue(issubclass(TestCase, selftest._AlcatrazSecurityInvariants))

    def test_separate_calls_return_distinct_classes(self):
        a = selftest.make_alcatraz_selftest_testcase(Path("/tmp/a"))
        b = selftest.make_alcatraz_selftest_testcase(Path("/tmp/b"))
        self.assertIsNot(a, b)

    def test_returned_class_is_not_module_level(self):
        """Safety: the class is defined inside the factory, so `unittest
        discover` scanning module-level names never finds it — no risk of
        the selftest TestCase running as part of the default suite."""
        TestCase = selftest.make_alcatraz_selftest_testcase(Path("/tmp"))
        self.assertFalse(hasattr(selftest, TestCase.__name__))


class AlcatrazSecurityInvariantsSurfaceTests(unittest.TestCase):
    """_AlcatrazSecurityInvariants is the single source of truth for the
    security-invariant assertion list. Lock in the surface so subclasses
    can rely on it."""

    EXPECTED_ASSERTIONS = frozenset(
        {
            "test_alcatraz_runs_as_phantom_uid",
            "test_alcatraz_user_is_agent",
            "test_no_ssh_directory",
            "test_no_gnupg_directory",
            "test_global_git_config_no_alcatraz",
            "test_no_host_signing_key_paths",
            "test_signing_key_empty",
            "test_commit_signing_disabled",
            "test_no_leaked_secret_env_vars",
            "test_python_available",
            "test_node_available",
            "test_git_available",
            "test_mise_available",
            "test_claude_available",
            "test_mise_manages_python",
            "test_mise_manages_node",
            "test_workspace_git_name_matches_identity",
            "test_workspace_git_email_matches_identity",
            "test_workspace_git_config_no_alcatraz",
            "test_commit_identity_matches",
            "test_commit_identity_no_alcatraz",
            "test_branching_and_merging_works",
            "test_python_execution",
            "test_node_execution",
            "test_files_owned_by_phantom_uid",
            "test_docker_socket_not_mounted",
            "test_no_git_remotes",
        }
    )

    def test_declares_expected_assertion_surface(self):
        actual = {
            name for name in dir(selftest._AlcatrazSecurityInvariants) if name.startswith("test_")
        }
        self.assertEqual(actual, self.EXPECTED_ASSERTIONS)


if __name__ == "__main__":
    unittest.main()
