"""End-to-end integration test for whether an AI agent inside the Alcatraz
can actually USE the credentials Claude Code needs to authenticate.

This drives a real `DockerPrison` + real container brought up by
`alcatrazer start`, with the host's Claude token redirected to a synthetic
file so the flow runs identically on any machine (CI included) regardless
of whether the operator happens to be logged into Claude. No mocks at the
prison layer.

The promise under check: the agent — which runs as a phantom UID that can
never own the host's `0600` token file — can nonetheless read its own
`~/.claude/.credentials.json` inside the sandbox. The original read-only
bind mount could NOT deliver this (mounting a `0600` host-owned file
across a UID boundary leaves it unreadable to the agent); credential
injection writes the file AS the agent instead, so ownership and
permissions land correctly and the host file is read once rather than
attached for the session.

Distinct purpose from test_smoke.py (security invariants + tooling) and the
promotion-flow files, so it lives in its own file per the three-tier
integration-test discipline. Gated behind Docker; run with `mise test-smoke`.

The prose method names exceed ruff's line-length; E501 is ignored for
integration_tests/** (see pyproject) because the test name IS the spec.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alcatrazer import start as start_mod
from alcatrazer.docker_prison import DockerPrison
from alcatrazer.integration_tests.test_smoke import (
    CODING_ENV,
    _docker_available,
    _nuke_phantom_uid_files,
    _seed_project,
)

# A stand-in for ~/.claude/.credentials.json. A real Claude token is JSON;
# the exact bytes are irrelevant here — only that the file round-trips into
# the sandbox readable by the agent, owned agent:agent, mode 0600.
HOST_TOKEN = '{"claudeAiOauth": {"accessToken": "test-token-not-real"}}'

SANDBOX_CREDENTIALS_PATH = "/home/agent/.claude/.credentials.json"


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestClaudeCredentialReadiness(unittest.TestCase):
    """A fresh Alcatraz brought up with a host Claude token present — the
    one Given every scenario here shares. setUpClass runs the bring-up once;
    each self-naming test observes the credential file the agent ended up
    with inside the running container."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        # The host token lives in its own tempdir, never touching the
        # operator's real ~/.claude — start() reads it via the patched
        # _claude_creds_path below.
        cls._host_home = tempfile.TemporaryDirectory()
        cls.host_token_path = Path(cls._host_home.name) / ".credentials.json"
        cls.host_token_path.write_text(HOST_TOKEN)

        cls.prison = DockerPrison(cls.project_dir)
        cls._given_alcatrazer_started_with_a_host_token()

    @classmethod
    def tearDownClass(cls):
        with contextlib.suppress(Exception):
            cls.prison.stop()
        with contextlib.suppress(Exception):
            cls.prison.remove()
        _nuke_phantom_uid_files(cls.project_dir)
        with contextlib.suppress(Exception):
            cls._tmp.cleanup()
        with contextlib.suppress(Exception):
            cls._host_home.cleanup()

    # ── The shared Given ──────────────────────────────────────────────

    @classmethod
    def _given_alcatrazer_started_with_a_host_token(cls) -> None:
        """Init then start the Alcatraz with the host Claude token in place.

        `_claude_creds_path` is redirected to the synthetic token for the
        duration of start so credential injection runs deterministically
        — independent of whether the host machine is logged into Claude."""
        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Outer Developer", "outer.dev@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod.cmd_init(cls.project_dir)
            if rc != 0:
                raise RuntimeError(f"cmd_init failed (rc={rc})")
            with patch.object(start_mod, "_claude_creds_path", return_value=cls.host_token_path):
                rc = start_mod.cmd_start(cls.project_dir, prison=cls.prison)
        if rc != 0:
            raise RuntimeError(f"cmd_start failed (rc={rc})")

    # ── Observations inside the running sandbox ───────────────────────

    def _long_listing_of_credentials(self) -> str:
        result = self.prison.query(["ls", "-la", SANDBOX_CREDENTIALS_PATH])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout.strip()

    def _credentials_as_the_agent_reads_them(self):
        return self.prison.query(["cat", SANDBOX_CREDENTIALS_PATH])

    def _mountinfo_inside_sandbox(self) -> str:
        result = self.prison.query(["cat", "/proc/self/mountinfo"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout

    # ── The scenarios ─────────────────────────────────────────────────

    def test_the_long_listing_shows_the_credentials_owned_by_agent_and_readable_only_by_their_owner(
        self,
    ):
        """Given Alcatraz started with a host Claude token; When we `ls -la`
        the credential file inside the sandbox; Then it reads
        `-rw------- 1 agent agent <size> <date> <path>` — owner-only
        permissions, owned by agent:agent — exactly the layout the agent
        needs and exactly what the old read-only mount could not produce
        across the phantom-UID boundary.

        Coverage gap: the read-only mount shipped with no test that the
        agent UID could actually READ the mounted file; this is the missing
        assertion that surfaces the permission/ownership failure."""
        # Example line this anchors on:
        #   -rw------- 1 agent agent 53 Jun  1 08:09 /home/agent/.claude/.credentials.json
        # When: list the credential file as it exists inside the sandbox.
        listing = self._long_listing_of_credentials()

        # Then: owner-only perms, owned by agent:agent. Size/date are
        # environment-specific, so they're matched loosely.
        self.assertRegex(listing, r"^-rw-------\s+\d+\s+agent\s+agent\s")

    def test_the_agent_can_read_back_the_exact_token_the_host_provided(self):
        """Given Alcatraz started with a host Claude token; When the agent
        reads the credential file; Then it succeeds and the contents are
        byte-for-byte the host token — proving the file is genuinely
        readable by the user the agent runs as, not merely present.

        This is the behavioural heart of the fix: a `0600` host-owned file
        bind-mounted in would fail this read with permission denied."""
        # When: read the file as the agent user.
        result = self._credentials_as_the_agent_reads_them()

        # Then: the read succeeds and returns the host token verbatim.
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, HOST_TOKEN)

    def test_the_host_token_file_is_not_bind_mounted_into_the_sandbox(self):
        """Given Alcatraz started with a host Claude token; When we inspect
        the sandbox's mount table; Then no `.credentials.json` bind mount
        appears — injection replaced the mount, so the live host inode is
        never attached for the session (read once at start, then gone).

        Coverage gap: locks the design decision that the host credential
        file is no longer the same inode living inside the container."""
        # When: read the sandbox's own mount table.
        mountinfo = self._mountinfo_inside_sandbox()

        # Then: the credential file is not among the mounts.
        self.assertNotIn(".credentials.json", mountinfo)


@unittest.skipUnless(_docker_available(), "Docker not available")
class TestClaudeCredentialCopyBack(unittest.TestCase):
    """A real Alcatraz brought up with a host token, used to prove the
    teardown copy-back: a token refreshed INSIDE the sandbox is offered back
    as a `~/.claude/.credentials.json.fresh` sidecar (never overwriting the
    host file), while an unchanged token offers nothing.

    Its own container (own setUpClass) so it never disturbs the readiness
    scenarios. One stateful two-phase method by design — the phases share
    the container and must run in order, so splitting them would mean
    rebuilding the same state twice (mirrors test_smoke's lifecycle test)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.project_dir = Path(cls._tmp.name)
        _seed_project(cls.project_dir)

        cls._host_home = tempfile.TemporaryDirectory()
        cls.host_token_path = Path(cls._host_home.name) / ".credentials.json"
        cls.host_token_path.write_text(HOST_TOKEN)
        cls.sidecar_path = cls.host_token_path.parent / (cls.host_token_path.name + ".fresh")

        cls.prison = DockerPrison(cls.project_dir)
        with (
            patch.object(
                start_mod,
                "ask_promotion_identity",
                return_value=("Outer Developer", "outer.dev@example.com"),
            ),
            patch.object(start_mod, "ask_coding_environment", return_value=CODING_ENV),
        ):
            rc = start_mod.cmd_init(cls.project_dir)
            if rc != 0:
                raise RuntimeError(f"cmd_init failed (rc={rc})")
            with patch.object(start_mod, "_claude_creds_path", return_value=cls.host_token_path):
                rc = start_mod.cmd_start(cls.project_dir, prison=cls.prison)
        if rc != 0:
            raise RuntimeError(f"cmd_start failed (rc={rc})")

    @classmethod
    def tearDownClass(cls):
        with contextlib.suppress(Exception):
            cls.prison.stop()
        with contextlib.suppress(Exception):
            cls.prison.remove()
        _nuke_phantom_uid_files(cls.project_dir)
        with contextlib.suppress(Exception):
            cls._tmp.cleanup()
        with contextlib.suppress(Exception):
            cls._host_home.cleanup()

    def _offer_copy_back(self) -> None:
        with patch.object(start_mod, "_claude_creds_path", return_value=self.host_token_path):
            start_mod.offer_refreshed_claude_credentials(self.prison, self.project_dir)

    def _simulate_token_refresh_inside_sandbox(self, token: str) -> None:
        result = self.prison.query(
            ["sh", "-c", f"umask 077; printf %s {token!r} > {SANDBOX_CREDENTIALS_PATH}"]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_a_refreshed_sandbox_token_is_offered_as_a_sidecar_while_an_unchanged_one_is_not(self):
        """Given Alcatraz started with a host token (injected, unchanged
        inside); When copy-back runs at teardown with no refresh having
        happened; Then no sidecar is offered (the sandbox token is identical
        to the host's). When the token is then refreshed inside the sandbox
        and copy-back runs again; Then the refreshed token is written to
        `~/.claude/.credentials.json.fresh`, and the real host file is left
        byte-for-byte untouched — the user applies the sidecar themselves."""
        # Phase 1 — nothing refreshed inside: the sandbox token equals the
        # host's, so copy-back offers nothing.
        self._offer_copy_back()
        self.assertFalse(
            self.sidecar_path.exists(),
            "no sidecar should be offered when the sandbox token matches the host",
        )

        # Phase 2 — the token is refreshed inside the sandbox (as Claude
        # would when its access token expires mid-session).
        refreshed = '{"claudeAiOauth": {"accessToken": "refreshed-inside-sandbox"}}'
        self._simulate_token_refresh_inside_sandbox(refreshed)
        self._offer_copy_back()

        # The refreshed token is offered as a sidecar...
        self.assertEqual(self.sidecar_path.read_text(), refreshed)
        self.assertEqual(self.sidecar_path.stat().st_mode & 0o777, 0o600)
        # ...and the user's real host file is never modified.
        self.assertEqual(self.host_token_path.read_text(), HOST_TOKEN)


if __name__ == "__main__":
    unittest.main()
