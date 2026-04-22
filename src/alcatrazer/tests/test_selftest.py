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
    (SelftestAlcatraz via the factory, TestAlcatrazSmokeCI via smoke
    inheritance) can rely on it.

    Scope: security invariants only. Tooling and workflow checks live in
    smoke-only mixins (test_smoke._AlcatrazToolingInvariants /
    _AlcatrazWorkflowInvariants). `--run-selftest` deliberately runs only
    this tier — the other tiers either don't belong (tooling is an
    integration concern, not a security promise) or write state that
    would pollute the user's live repo (workflow)."""

    EXPECTED_ASSERTIONS = frozenset(
        {
            # 1. User identity
            "test_alcatraz_runs_as_phantom_uid",
            "test_alcatraz_user_is_agent",
            # 2. Host credential isolation
            "test_no_ssh_directory",
            "test_no_gnupg_directory",
            "test_global_git_config_no_alcatraz_branding",
            "test_no_host_signing_key_paths_in_global_git_config",
            "test_global_signing_key_empty_or_unset",
            "test_global_commit_signing_disabled",
            # 3. Environment discipline
            "test_no_leaked_secret_env_vars",
            # 4. Workspace git identity is the agent (anti-leak)
            "test_workspace_git_user_name_is_agent",
            "test_workspace_git_user_email_is_agent",
            "test_workspace_git_config_no_alcatraz_branding",
            "test_workspace_initial_commit_authored_by_agent",
            # 5. Filesystem ownership
            "test_workspace_directory_owned_by_phantom_uid",
            # 6. Attack surface
            "test_docker_socket_not_mounted",
            "test_workspace_has_no_git_remotes",
            # 7. Zero branding inside the Alcatraz
            "test_no_alcatraz_branding_in_environment",
            "test_no_alcatraz_branding_in_mount_points",
        }
    )

    def test_declares_expected_assertion_surface(self):
        actual = {
            name for name in dir(selftest._AlcatrazSecurityInvariants) if name.startswith("test_")
        }
        self.assertEqual(actual, self.EXPECTED_ASSERTIONS)


if __name__ == "__main__":
    unittest.main()
