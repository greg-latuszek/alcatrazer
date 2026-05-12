"""
Tests for src/promote.py — promotion from inner (alcatraz) to outer repo.

Integration tests use real git repos (fast-export/fast-import pipeline).
Unit tests use mocking for identity resolution and stream rewriting.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# TODO: Remove once pyproject.toml handles src layout (Step 0.9)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))
from alcatrazer import promote as promote_mod

SEED_SCRIPT = str(Path(__file__).resolve().parent / "seed_alcatraz.sh")

ALCATRAZ_NAME = "Alcatraz Agent"
ALCATRAZ_EMAIL = "alcatraz@localhost"
PROMOTED_NAME = "Test User"
PROMOTED_EMAIL = "test@example.com"


def git(repo: str, *args: str) -> str:
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ── Unit tests (no git repos needed) ────────────────────────────────


class TestRewriteFromHeader(unittest.TestCase):
    """Phase 2 (change_promotion_machinery.md L805-810): primitive
    `rewrite_from_header` substitutes the `From:` line in an
    mbox-format patch stream. Operates on bytes; preserves the
    `From <sha>` separator (no colon) and any binary hunks
    byte-for-byte.
    """

    def test_substitutes_from_in_real_mbox_with_binary(self):
        """Real `git format-patch` output containing a binary file,
        rewritten and applied to an outer repo:

        - Header rewritten — outer's resulting commit has Alice as
          author, Patricia appears nowhere in the log.
        - Binary file content survives the rewrite byte-for-byte
          — outer's blob.bin equals the bytes inner committed.
        - Text file content survives (sanity).
        - git am succeeded, which implies the mbox stream was
          well-formed after rewrite_from_header (a corrupted
          separator or malformed binary section would fail parsing).

        Method follows Step 2.7's pattern (and the body-line test
        below): rewrite + apply via plain `git am` + query git/
        filesystem for the semantic end effect. Plain `git am`
        (not apply_patch_stream) keeps this test focused on
        rewrite_from_header in isolation.
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner: initial empty commit (inner_root) + agent commit
            # adding a binary blob and a text file.
            subprocess.run(
                ["git", "init", "-b", "main", inner],
                capture_output=True,
                check=True,
            )
            git(inner, "config", "user.name", "Patricia Garcia")
            git(inner, "config", "user.email", "patricia@inner.example.com")
            git(inner, "config", "commit.gpgsign", "false")
            git(inner, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(inner, "rev-parse", "HEAD")
            # All 256 byte values — exercises non-UTF-8 bytes through
            # the rewrite. format-patch --binary emits a GIT binary
            # patch section that must survive intact end-to-end.
            original_binary = bytes(range(256))
            Path(inner, "blob.bin").write_bytes(original_binary)
            Path(inner, "text.txt").write_text("hello\n")
            git(inner, "add", "-A")
            git(inner, "commit", "-m", "agent: add binary blob and text")

            stream = promote_mod.format_patch_stream(Path(inner), inner_root)
            rewritten = promote_mod.rewrite_from_header(
                stream, "Alice Example", "alice@example.com"
            )

            # Outer repo with one initial commit, ready to receive
            # the rewritten patch on top.
            subprocess.run(
                ["git", "init", "-b", "main", outer],
                capture_output=True,
                check=True,
            )
            git(outer, "config", "user.name", "Outer User")
            git(outer, "config", "user.email", "user@outer.example.com")
            git(outer, "config", "commit.gpgsign", "false")
            Path(outer, "README.md").write_text("# Project\n")
            git(outer, "add", "README.md")
            git(outer, "commit", "-m", "initial outer commit")

            # Apply via plain `git am`. Success itself is an assertion:
            # if rewrite_from_header had corrupted the separator or
            # the binary section, git's parser would refuse here.
            subprocess.run(
                ["git", "-C", outer, "am", "--keep-non-patch", "--whitespace=nowarn"],
                input=rewritten,
                capture_output=True,
                check=True,
            )

            # Ask git directly: top commit's author is Alice.
            self.assertEqual(
                git(outer, "log", "-1", "--format=%an <%ae>"),
                "Alice Example <alice@example.com>",
            )
            # Inner identity absent from author/committer log entirely
            # — three layers (Patricia name, patricia email local-part
            # lowercase, inner.example.com domain). Matches Step 2.7's
            # pattern in TestApplyPatchStream. Single-case check would
            # miss the lowercase email if it ever leaked.
            log = git(outer, "log", "--all", "--format=%an %ae %cn %ce")
            self.assertNotIn("Patricia", log)
            self.assertNotIn("patricia", log)
            self.assertNotIn("inner.example.com", log)
            # Ask the filesystem: binary content matches what inner
            # committed, byte-for-byte. This is the strongest binary-
            # passthrough check available — it verifies the entire
            # rewrite -> format-patch -> git am pipeline preserved
            # every byte, not just one stage.
            self.assertEqual(
                Path(outer, "blob.bin").read_bytes(),
                original_binary,
            )
            # Text file too — sanity.
            self.assertEqual(Path(outer, "text.txt").read_text(), "hello\n")

    def test_does_not_rewrite_from_in_commit_message_body(self):
        """Regression test (caught by code review of Phase 2 Step 2.2):
        the `From: ` rewrite must NOT touch lines starting with
        `From: ` that appear inside a commit's MESSAGE BODY — only
        the header line directly after the `From <sha>` mbox separator
        gets rewritten.

        Realistic scenario: agent (inner identity Patricia Garcia)
        writes a commit whose message body legitimately references its
        own email — e.g. mentioning the email used for the agent
        identity, or pasting a config snippet. After promotion to the
        outer repo, the agent's identity is rewritten to the
        developer's identity in the HEADER but the body's reference
        to the agent's email MUST survive intact (we'd otherwise
        rewrite content the agent deliberately wrote).

        Method: produce the rewritten patch stream, apply it to a
        real outer repo via plain `git am`, then ask git directly
        who the author is and what the commit body says. This
        sidesteps stream-byte-matching entirely — we test the
        SEMANTIC end effect, not the intermediate mbox shape.
        Plain `git am` is used (not apply_patch_stream) to keep
        this test focused on rewrite_from_header in isolation.
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner workspace: identity = Patricia, and the commit
            # message body deliberately references that same email
            # as a config snippet.
            subprocess.run(
                ["git", "init", "-b", "main", inner],
                capture_output=True,
                check=True,
            )
            git(inner, "config", "user.name", "Patricia Garcia")
            git(inner, "config", "user.email", "patricia@inner.example.com")
            git(inner, "config", "commit.gpgsign", "false")
            git(inner, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(inner, "rev-parse", "HEAD")
            Path(inner, "file.txt").write_text("content\n")
            git(inner, "add", "file.txt")
            git(
                inner,
                "commit",
                "-m",
                "Subject line\n\n"
                "Body deliberately mentions the agent's own email, "
                "e.g. as part of a config snippet:\n"
                "From: patricia@inner.example.com\n"
                "More body content after the deliberate reference.",
            )

            stream = promote_mod.format_patch_stream(Path(inner), inner_root)
            rewritten = promote_mod.rewrite_from_header(
                stream, "Alice Example", "alice@example.com"
            )

            # Outer repo with one initial commit so git am applies the
            # patch on top.
            subprocess.run(
                ["git", "init", "-b", "main", outer],
                capture_output=True,
                check=True,
            )
            git(outer, "config", "user.name", "Outer User")
            git(outer, "config", "user.email", "user@outer.example.com")
            git(outer, "config", "commit.gpgsign", "false")
            Path(outer, "README.md").write_text("# Project\n")
            git(outer, "add", "README.md")
            git(outer, "commit", "-m", "initial outer commit")

            # Apply the rewritten stream via plain `git am` (NOT
            # apply_patch_stream — we want to isolate rewrite_from_header
            # here; apply_patch_stream is tested separately).
            subprocess.run(
                ["git", "-C", outer, "am", "--keep-non-patch", "--whitespace=nowarn"],
                input=rewritten,
                capture_output=True,
                check=True,
            )

            # Ask git directly: who is the author of the top commit?
            # If rewrite_from_header did its job, it's Alice.
            self.assertEqual(
                git(outer, "log", "-1", "--format=%an <%ae>"),
                "Alice Example <alice@example.com>",
            )
            # Ask git directly: what is the body of the top commit?
            # If the regex over-matched, the body's "From: patricia@..."
            # would have been rewritten to "From: Alice ...", losing
            # the agent's deliberate content. The semantic check is
            # "the agent's email reference survives in the commit body".
            body = git(outer, "log", "-1", "--format=%B")
            self.assertIn("patricia@inner.example.com", body)
            self.assertIn("From: patricia@inner.example.com", body)
            # Inner identity must NOT appear as author or committer
            # anywhere in the outer's log. Three layers (matching
            # Step 2.7's pattern in TestApplyPatchStream):
            #   - "Patricia"             — the name (capital P)
            #   - "patricia"             — the email local-part (lower)
            #   - "inner.example.com"    — the email domain
            # Single-case check would miss the lowercase email; domain
            # check catches any other lowercase leakage too.
            log = git(outer, "log", "--all", "--format=%an %ae %cn %ce")
            self.assertNotIn("Patricia", log)
            self.assertNotIn("patricia", log)
            self.assertNotIn("inner.example.com", log)


class TestPromoteOnce(unittest.TestCase):
    """Phase 3 Steps 3.3-3.8 (change_promotion_machinery.md L857-876):
    `promote_once` orchestrates a single promotion cycle: read state,
    check pin, format-patch the inner range since last_promoted,
    rewrite identity, apply via git am, write back state. Returns
    PromotionResult so the daemon can log meaningfully.
    """

    def _make_inner_with_agent_commits(self, inner: Path, count: int = 2) -> tuple[str, str]:
        """Initialise inner with an Initial commit (would-be inner_root)
        + `count` agent commits on top. Returns (inner_root, inner_tip).
        """
        inner.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(inner)],
            capture_output=True,
            check=True,
        )
        git(str(inner), "config", "user.name", "Patricia Garcia")
        git(str(inner), "config", "user.email", "patricia@inner.example.com")
        git(str(inner), "config", "commit.gpgsign", "false")
        git(str(inner), "commit", "--allow-empty", "-m", "Initial commit")
        inner_root = git(str(inner), "rev-parse", "HEAD")
        for i in range(count):
            Path(inner, f"agent{i}.py").write_text(f"# agent {i}\n")
            git(str(inner), "add", ".")
            git(str(inner), "commit", "-m", f"agent: commit {i}")
        inner_tip = git(str(inner), "rev-parse", "HEAD")
        return inner_root, inner_tip

    def _make_outer_on_branch(self, outer: Path, branch: str) -> None:
        """Initialise outer as a git repo on `branch` with one commit."""
        outer.mkdir()
        subprocess.run(
            ["git", "init", "-b", branch, str(outer)],
            capture_output=True,
            check=True,
        )
        git(str(outer), "config", "user.name", "Outer User")
        git(str(outer), "config", "user.email", "user@outer.example.com")
        git(str(outer), "config", "commit.gpgsign", "false")
        git(str(outer), "commit", "--allow-empty", "-m", "initial outer commit")

    def test_active_path_applies_patches_and_advances_state(self):
        """Active path (outer on pinned branch, agent has new commits):
        - patches applied to outer
        - last_promoted advanced to inner's tip
        - last_promotion_time written
        - paused cleared (set to None even if it had a prior reason)
        - returns PromotionResult(PROMOTED, count=N)

        Spec: change_promotion_machinery.md L857-859 (Step 3.3).
        """
        from alcatrazer import state

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            inner_root, inner_tip = self._make_inner_with_agent_commits(inner, count=2)
            self._make_outer_on_branch(outer, "feat/X")
            # Pre-set a stale "paused" to verify the active path clears it.
            state.update_state(
                alcatraz_dir,
                pinned_branch="feat/X",
                inner_root=inner_root,
                paused={"reason": "previous conflict"},
            )

            result = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )

            # Outcome: PROMOTED, 2 commits applied.
            self.assertEqual(result.outcome, promote_mod.PromotionOutcome.PROMOTED)
            self.assertEqual(result.commit_count, 2)
            # Outer branch advanced (1 initial + 2 promoted = 3 commits).
            self.assertEqual(int(git(str(outer), "rev-list", "--count", "HEAD")), 3)
            # State advanced.
            new_state = state.load_state(alcatraz_dir)
            self.assertEqual(new_state.get("last_promoted"), inner_tip)
            self.assertIn("last_promotion_time", new_state)
            # Paused cleared.
            self.assertIsNone(new_state.get("paused"))

    def test_held_when_off_pin_no_state_change(self):
        """Outer on a different branch than pinned: promote_once must
        not call `git am`, must not mutate state, and must return
        HELD with pin_status=OFF_PIN.

        Spec: change_promotion_machinery.md L861-862 (Step 3.4).
        """
        from alcatrazer import state

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            inner_root, _ = self._make_inner_with_agent_commits(inner, count=2)
            # Outer on `main`; pinned_branch is feat/X but feat/X must
            # exist so the failure mode is OFF_PIN, not PIN_DELETED.
            self._make_outer_on_branch(outer, "main")
            subprocess.run(
                ["git", "-C", str(outer), "branch", "feat/X"],
                capture_output=True,
                check=True,
            )
            state.update_state(alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root)

            pre_head = git(str(outer), "rev-parse", "HEAD")
            pre_state = state.load_state(alcatraz_dir)

            result = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )

            self.assertEqual(result.outcome, promote_mod.PromotionOutcome.HELD)
            self.assertEqual(result.pin_status, promote_mod.PinStatus.OFF_PIN)
            # Outer HEAD untouched, no stale am state.
            self.assertEqual(git(str(outer), "rev-parse", "HEAD"), pre_head)
            self.assertFalse((outer / ".git" / "rebase-apply").exists())
            # State byte-identical (no mutation in HELD path).
            self.assertEqual(state.load_state(alcatraz_dir), pre_state)
            # And specifically: no last_promoted / last_promotion_time.
            post_state = state.load_state(alcatraz_dir)
            self.assertNotIn("last_promoted", post_state)
            self.assertNotIn("last_promotion_time", post_state)

    def test_resumes_after_recheckout_applies_piled_commits(self):
        """Across two cycles separated by a recheckout:
        1. Outer is off-pin, agent has one commit → HELD, no state change.
        2. Agent commits two more (now 3 piled). User recheckouts the
           pin. → next promote_once applies all 3 in one am, advances
           state.last_promoted to inner's tip.

        Spec: change_promotion_machinery.md L864-866 (Step 3.5).
        """
        from alcatrazer import state

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            inner_root, _ = self._make_inner_with_agent_commits(inner, count=1)
            self._make_outer_on_branch(outer, "main")
            subprocess.run(
                ["git", "-C", str(outer), "branch", "feat/X"],
                capture_output=True,
                check=True,
            )
            state.update_state(alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root)

            # Cycle 1: HELD (outer on main, not on feat/X).
            result1 = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )
            self.assertEqual(result1.outcome, promote_mod.PromotionOutcome.HELD)

            # Agent makes more commits while held.
            for i in range(1, 3):
                Path(inner, f"agent{i}.py").write_text(f"# agent {i}\n")
                git(str(inner), "add", ".")
                git(str(inner), "commit", "-m", f"agent: late commit {i}")
            inner_tip = git(str(inner), "rev-parse", "HEAD")

            # User recheckouts the pinned branch.
            subprocess.run(
                ["git", "-C", str(outer), "checkout", "feat/X"],
                capture_output=True,
                check=True,
            )

            # Cycle 2: PROMOTED with all 3 piled commits.
            result2 = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )
            self.assertEqual(result2.outcome, promote_mod.PromotionOutcome.PROMOTED)
            self.assertEqual(result2.commit_count, 3)
            # State.last_promoted == inner's tip after the apply.
            self.assertEqual(state.load_state(alcatraz_dir).get("last_promoted"), inner_tip)

    def test_inner_merge_appears_as_individual_side_commits(self):
        """When inner's main has a merge commit bringing in N
        side-branch commits, the merge itself produces NO patch
        (git format-patch's design) but each side commit produces
        an individual patch. Outer's history therefore has the
        side commits as separate atomic entries — reviewable by
        the developer one-by-one before push. This is the revised
        behavior per Step 3.6 (the original spec assumed
        `--first-parent` could squash merges; empirically it
        can't, and individual atomic commits are also product-
        better for parallel-agent workflows).

        Spec: change_promotion_machinery.md L868-893 (Step 3.6, revised).
        """
        from alcatrazer import state

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            # Inner: initial commit + side branch with 2 commits +
            # merge back to main. main's first-parent line is
            # inner_root -> merge. Side commits exist but only on
            # the second-parent line of the merge.
            inner.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(inner)],
                capture_output=True,
                check=True,
            )
            git(str(inner), "config", "user.name", "Patricia Garcia")
            git(str(inner), "config", "user.email", "patricia@inner.example.com")
            git(str(inner), "config", "commit.gpgsign", "false")
            git(str(inner), "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(str(inner), "rev-parse", "HEAD")

            subprocess.run(
                ["git", "-C", str(inner), "checkout", "-b", "side"],
                capture_output=True,
                check=True,
            )
            Path(inner, "side1.py").write_text("# side 1\n")
            git(str(inner), "add", "side1.py")
            git(str(inner), "commit", "-m", "side: commit 1")
            Path(inner, "side2.py").write_text("# side 2\n")
            git(str(inner), "add", "side2.py")
            git(str(inner), "commit", "-m", "side: commit 2")
            subprocess.run(
                ["git", "-C", str(inner), "checkout", "main"],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(inner),
                    "merge",
                    "--no-ff",
                    "-m",
                    "merge: pull in side work",
                    "side",
                ],
                capture_output=True,
                check=True,
            )

            self._make_outer_on_branch(outer, "feat/X")
            state.update_state(alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root)

            result = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )

            # 2 patches applied — one per side commit. The merge
            # commit itself does NOT produce a patch.
            self.assertEqual(result.outcome, promote_mod.PromotionOutcome.PROMOTED)
            self.assertEqual(result.commit_count, 2)
            # Outer: 1 initial + 2 promoted = 3 commits.
            self.assertEqual(int(git(str(outer), "rev-list", "--count", "HEAD")), 3)
            # Both side files landed in outer's working tree.
            self.assertTrue(Path(outer, "side1.py").exists())
            self.assertTrue(Path(outer, "side2.py").exists())
            # Side-branch commits ARE in outer's log as separate atomic
            # entries — each reviewable by the developer one-by-one
            # before push. This is the reviewability property
            # parallel-agent workflows depend on.
            log = git(str(outer), "log", "--all", "--format=%s")
            self.assertIn("side: commit 1", log)
            self.assertIn("side: commit 2", log)
            # The merge commit's subject does NOT appear (format-patch
            # skipped it — it has no individual patch representation).
            self.assertNotIn("merge: pull in side work", log)

    def test_paused_on_conflict_writes_paused_preserves_advance_state(self):
        """Conflict path: outer has a local change on the same file
        the agent modified, so apply_patch_stream raises
        PromotionConflictError. promote_once must:
        - write state.paused = {"reason": "<message>"}
        - leave state.last_promoted and state.last_promotion_time
          UNTOUCHED (they reflect the LAST SUCCESSFUL promotion, not
          this failed one)
        - return PromotionResult(PAUSED, conflict_message=...)
        - outer's HEAD unchanged (apply_patch_stream rolled back)

        Spec: change_promotion_machinery.md L872-873 (Step 3.7).
        """
        from alcatrazer import state

        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "inner"
            outer = Path(tmp) / "outer"
            alcatraz_dir = Path(tmp) / ".alcatrazer"
            alcatraz_dir.mkdir()

            # Inner: shared.py = "v1" at initial, agent edits to "v2".
            inner.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(inner)],
                capture_output=True,
                check=True,
            )
            git(str(inner), "config", "user.name", "Patricia Garcia")
            git(str(inner), "config", "user.email", "patricia@inner.example.com")
            git(str(inner), "config", "commit.gpgsign", "false")
            Path(inner, "shared.py").write_text("v1\n")
            git(str(inner), "add", "shared.py")
            git(str(inner), "commit", "-m", "Initial commit")
            inner_root = git(str(inner), "rev-parse", "HEAD")
            Path(inner, "shared.py").write_text("v2 - agent\n")
            git(str(inner), "add", ".")
            git(str(inner), "commit", "-m", "agent: modify shared")

            # Outer on feat/X with a conflicting local edit on shared.py.
            outer.mkdir()
            subprocess.run(
                ["git", "init", "-b", "feat/X", str(outer)],
                capture_output=True,
                check=True,
            )
            git(str(outer), "config", "user.name", "Outer User")
            git(str(outer), "config", "user.email", "user@outer.example.com")
            git(str(outer), "config", "commit.gpgsign", "false")
            Path(outer, "shared.py").write_text("v1\n")
            git(str(outer), "add", "shared.py")
            git(str(outer), "commit", "-m", "initial outer")
            Path(outer, "shared.py").write_text("v2 - user\n")
            git(str(outer), "add", ".")
            git(str(outer), "commit", "-m", "user: modify shared differently")

            state.update_state(alcatraz_dir, pinned_branch="feat/X", inner_root=inner_root)
            pre_head = git(str(outer), "rev-parse", "HEAD")

            result = promote_mod.promote_once(
                inner, outer, alcatraz_dir, "Alice Example", "alice@example.com"
            )

            # Outcome PAUSED with a non-empty conflict message.
            self.assertEqual(result.outcome, promote_mod.PromotionOutcome.PAUSED)
            self.assertNotEqual(result.conflict_message, "")
            # State.paused recorded with a reason.
            post_state = state.load_state(alcatraz_dir)
            self.assertIsNotNone(post_state.get("paused"))
            self.assertIn("reason", post_state["paused"])
            # State.last_promoted / last_promotion_time NOT advanced
            # — they should only reflect SUCCESSFUL promotions.
            self.assertNotIn("last_promoted", post_state)
            self.assertNotIn("last_promotion_time", post_state)
            # Outer HEAD unchanged (apply rolled back via git am --abort).
            self.assertEqual(git(str(outer), "rev-parse", "HEAD"), pre_head)
            self.assertFalse((outer / ".git" / "rebase-apply").exists())


class TestCheckPin(unittest.TestCase):
    """Phase 3 (change_promotion_machinery.md L851-855): `check_pin`
    classifies the outer's current HEAD against the recorded pin
    into one of four `PinStatus` values:

    - `OK`          — outer is on the pinned branch
    - `OFF_PIN`     — outer is on a different (existing) branch
    - `DETACHED`    — outer is on detached HEAD
    - `PIN_DELETED` — pinned branch no longer exists in outer

    This is the gate for promote_once: only OK lets patches apply;
    the other three states put promotion on hold (no work done, no
    error, no state mutation).
    """

    def _make_target_with_pinned_branch(self, target: str, pinned: str) -> None:
        """Initialize `target` as a git repo on `pinned`, with one
        commit so HEAD is real (not a "no commits yet" state)."""
        subprocess.run(
            ["git", "init", "-b", pinned, target],
            capture_output=True,
            check=True,
        )
        git(target, "config", "user.name", "Outer User")
        git(target, "config", "user.email", "user@outer.example.com")
        git(target, "config", "commit.gpgsign", "false")
        git(target, "commit", "--allow-empty", "-m", "initial outer commit")

    def test_returns_OK_when_on_pinned_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "outer")
            self._make_target_with_pinned_branch(target, "feat/X")
            self.assertEqual(
                promote_mod.check_pin(Path(target), "feat/X"),
                promote_mod.PinStatus.OK,
            )

    def test_returns_OFF_PIN_when_on_different_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "outer")
            self._make_target_with_pinned_branch(target, "feat/X")
            # Switch to a different existing branch.
            subprocess.run(
                ["git", "-C", target, "checkout", "-b", "main"],
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                promote_mod.check_pin(Path(target), "feat/X"),
                promote_mod.PinStatus.OFF_PIN,
            )

    def test_returns_DETACHED_when_head_is_detached(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "outer")
            self._make_target_with_pinned_branch(target, "feat/X")
            sha = git(target, "rev-parse", "HEAD")
            subprocess.run(
                ["git", "-C", target, "checkout", "--detach", sha],
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                promote_mod.check_pin(Path(target), "feat/X"),
                promote_mod.PinStatus.DETACHED,
            )

    def test_returns_PIN_DELETED_when_pinned_branch_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "outer")
            # Set up with `main` checked out; pin claims `feat/X` but
            # that branch is never created — represents the "user
            # deleted the pinned branch" state.
            self._make_target_with_pinned_branch(target, "main")
            self.assertEqual(
                promote_mod.check_pin(Path(target), "feat/X"),
                promote_mod.PinStatus.PIN_DELETED,
            )


class TestFormatPatchStream(unittest.TestCase):
    """Phase 2 (change_promotion_machinery.md L812-818): primitive
    `format_patch_stream(source, since_sha) -> bytes` returns an
    mbox-format patch stream for the range
    `<since_sha>..refs/heads/main` — i.e. the commit at `since_sha`
    itself is excluded; only its descendants on main produce patches.
    """

    def test_excludes_inner_root_commit_from_stream(self):
        """Workspace with one initial commit (inner_root) + N agent
        commits → stream has exactly N patches, not N+1. Counting
        patches via the mbox `From <sha> Mon Sep 17 ...` separator.
        """
        import re

        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "workspace")
            subprocess.run(
                ["git", "init", "-b", "main", workspace],
                capture_output=True,
                check=True,
            )
            git(workspace, "config", "user.name", "Test")
            git(workspace, "config", "user.email", "t@test")
            git(workspace, "config", "commit.gpgsign", "false")
            # Initial commit — this is what Phase 1 records as inner_root.
            git(workspace, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(workspace, "rev-parse", "HEAD")

            # Three agent commits on top of inner_root.
            for i in range(3):
                Path(workspace, f"file{i}.txt").write_text(f"content {i}\n")
                git(workspace, "add", ".")
                git(workspace, "commit", "-m", f"agent commit {i}")

            stream = promote_mod.format_patch_stream(Path(workspace), inner_root)

            patches = re.findall(rb"^From [0-9a-f]{40} Mon Sep 17", stream, re.MULTILINE)
            self.assertEqual(len(patches), 3)
            # Sanity: inner_root's commit message must not appear (it's
            # excluded from the range).
            self.assertNotIn(b"Initial commit", stream)


class TestApplyPatchStream(unittest.TestCase):
    """Phase 2 Steps 2.5-2.10 (change_promotion_machinery.md L820-847):
    primitive `apply_patch_stream(target, stream, name, email)` is the
    write-side counterpart to format_patch_stream. It runs `git am` on
    the mbox stream against `target`, rewriting author + committer to
    (name, email), preserving outer history, dropping empty patches,
    and aborting cleanly on conflict.
    """

    def _make_inner_with_one_agent_commit(self, workspace: str) -> tuple[str, bytes]:
        """Bootstrap an inner workspace: initial commit + one agent
        commit adding `feature.py`. Returns (inner_root_sha, patch_stream).
        """
        subprocess.run(
            ["git", "init", "-b", "main", workspace],
            capture_output=True,
            check=True,
        )
        git(workspace, "config", "user.name", "Patricia Garcia")
        git(workspace, "config", "user.email", "patricia@inner.example.com")
        git(workspace, "config", "commit.gpgsign", "false")
        git(workspace, "commit", "--allow-empty", "-m", "Initial commit")
        inner_root = git(workspace, "rev-parse", "HEAD")
        Path(workspace, "feature.py").write_text("def feature():\n    return 42\n")
        git(workspace, "add", "feature.py")
        git(workspace, "commit", "-m", "agent: add feature")
        stream = promote_mod.format_patch_stream(Path(workspace), inner_root)
        return inner_root, stream

    def _make_outer_with_one_commit(self, outer: str) -> None:
        """Bootstrap outer with one user commit so apply_patch_stream
        applies on top, not as the very first commit."""
        subprocess.run(
            ["git", "init", "-b", "main", outer],
            capture_output=True,
            check=True,
        )
        git(outer, "config", "user.name", "Outer User")
        git(outer, "config", "user.email", "user@outer.example.com")
        git(outer, "config", "commit.gpgsign", "false")
        Path(outer, "README.md").write_text("# Project\n")
        git(outer, "add", "README.md")
        git(outer, "commit", "-m", "initial outer commit")

    def test_advances_target_branch_and_updates_working_tree(self):
        """Outer with one commit + apply patches for one agent commit
        adding `feature.py` → branch has 2 commits, `feature.py` is
        in the working tree, `git status` is clean.

        Spec: change_promotion_machinery.md L820-823 (Step 2.5).
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner_ws = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")
            _, stream = self._make_inner_with_one_agent_commit(inner_ws)
            self._make_outer_with_one_commit(outer)

            promote_mod.apply_patch_stream(
                Path(outer), stream, "Outer User", "user@outer.example.com"
            )

            # Branch advanced from 1 to 2 commits.
            count = int(git(outer, "rev-list", "--count", "HEAD"))
            self.assertEqual(count, 2)
            # feature.py landed in the working tree.
            self.assertTrue(Path(outer, "feature.py").exists())
            self.assertEqual(
                Path(outer, "feature.py").read_text(),
                "def feature():\n    return 42\n",
            )
            # Working tree clean (no dirty staging or unstaged changes).
            status = git(outer, "status", "--porcelain")
            self.assertEqual(status, "")

    def test_preserves_outer_history_when_applying_patches(self):
        """Outer with O1, apply N patches for agent commits
        → branch becomes O1 -> A1 -> ... -> AN, with O1 still an
        ancestor of HEAD. Direct regression test for the manual-test
        bug the new format-patch/am pipeline was designed to fix.

        Spec: change_promotion_machinery.md L825-828 (Step 2.6).
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner_ws = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner: initial + 2 agent commits.
            subprocess.run(
                ["git", "init", "-b", "main", inner_ws],
                capture_output=True,
                check=True,
            )
            git(inner_ws, "config", "user.name", "Patricia Garcia")
            git(inner_ws, "config", "user.email", "patricia@inner.example.com")
            git(inner_ws, "config", "commit.gpgsign", "false")
            git(inner_ws, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(inner_ws, "rev-parse", "HEAD")
            for i in range(2):
                Path(inner_ws, f"agent{i}.py").write_text(f"# agent {i}\n")
                git(inner_ws, "add", ".")
                git(inner_ws, "commit", "-m", f"agent: commit {i}")
            stream = promote_mod.format_patch_stream(Path(inner_ws), inner_root)

            # Outer: single commit O1.
            self._make_outer_with_one_commit(outer)
            o1_sha = git(outer, "rev-parse", "HEAD")

            promote_mod.apply_patch_stream(
                Path(outer), stream, "Outer User", "user@outer.example.com"
            )

            # Branch length: O1 + 2 agent patches = 3 commits.
            self.assertEqual(int(git(outer, "rev-list", "--count", "HEAD")), 3)
            # O1 still ancestor of HEAD — outer history preserved.
            result = subprocess.run(
                ["git", "-C", outer, "merge-base", "--is-ancestor", o1_sha, "HEAD"],
                capture_output=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                "O1 must remain an ancestor of HEAD after apply_patch_stream",
            )

    def test_rewrites_both_author_and_committer_identity(self):
        """Inner authored by Patricia; after applying, outer's commits
        show BOTH author and committer = the configured outer user.
        Patricia appears nowhere in the outer's log.

        Spec: change_promotion_machinery.md L830-832 (Step 2.7).
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner_ws = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")
            _, stream = self._make_inner_with_one_agent_commit(inner_ws)
            self._make_outer_with_one_commit(outer)

            promote_mod.apply_patch_stream(
                Path(outer), stream, "Outer User", "user@outer.example.com"
            )

            # No Patricia / inner-identity strings anywhere in author or
            # committer fields of the outer's log.
            authors = git(outer, "log", "--all", "--format=%an <%ae>").splitlines()
            committers = git(outer, "log", "--all", "--format=%cn <%ce>").splitlines()
            for line in authors + committers:
                self.assertNotIn("Patricia", line)
                self.assertNotIn("patricia", line)
                self.assertNotIn("inner.example.com", line)
            # Top commit (the agent-derived one) — author + committer
            # are explicitly the configured outer user.
            self.assertEqual(
                git(outer, "log", "-1", "--format=%an <%ae>"),
                "Outer User <user@outer.example.com>",
            )
            self.assertEqual(
                git(outer, "log", "-1", "--format=%cn <%ce>"),
                "Outer User <user@outer.example.com>",
            )

    def test_aborts_cleanly_on_conflict(self):
        """Outer has its own conflicting edit on the same file; apply
        raises PromotionConflictError and the outer's HEAD + working
        tree are byte-identical to the pre-call state. No leftover
        `.git/rebase-apply/` directory (git am --abort cleaned up).

        Spec: change_promotion_machinery.md L834-837 (Step 2.8).
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner_ws = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner: initial commit with shared.py = "original",
            # then agent modifies it to "agent change".
            subprocess.run(
                ["git", "init", "-b", "main", inner_ws],
                capture_output=True,
                check=True,
            )
            git(inner_ws, "config", "user.name", "Patricia Garcia")
            git(inner_ws, "config", "user.email", "patricia@inner.example.com")
            git(inner_ws, "config", "commit.gpgsign", "false")
            Path(inner_ws, "shared.py").write_text("original\n")
            git(inner_ws, "add", "shared.py")
            git(inner_ws, "commit", "-m", "Initial commit")
            inner_root = git(inner_ws, "rev-parse", "HEAD")
            Path(inner_ws, "shared.py").write_text("agent change\n")
            git(inner_ws, "add", ".")
            git(inner_ws, "commit", "-m", "agent: modify shared")
            stream = promote_mod.format_patch_stream(Path(inner_ws), inner_root)

            # Outer: shared.py starts at "original" (matching the patch's
            # base) but is then modified by the user to "user change".
            # The patch will conflict because its base contents diverge
            # from outer's HEAD contents.
            subprocess.run(
                ["git", "init", "-b", "main", outer],
                capture_output=True,
                check=True,
            )
            git(outer, "config", "user.name", "Outer User")
            git(outer, "config", "user.email", "user@outer.example.com")
            git(outer, "config", "commit.gpgsign", "false")
            Path(outer, "shared.py").write_text("original\n")
            git(outer, "add", "shared.py")
            git(outer, "commit", "-m", "initial outer")
            Path(outer, "shared.py").write_text("user change\n")
            git(outer, "add", ".")
            git(outer, "commit", "-m", "user: modify shared")

            pre_head = git(outer, "rev-parse", "HEAD")
            pre_file = Path(outer, "shared.py").read_text()

            with self.assertRaises(promote_mod.PromotionConflictError):
                promote_mod.apply_patch_stream(
                    Path(outer), stream, "Outer User", "user@outer.example.com"
                )

            # HEAD unchanged.
            self.assertEqual(git(outer, "rev-parse", "HEAD"), pre_head)
            # File contents unchanged.
            self.assertEqual(Path(outer, "shared.py").read_text(), pre_file)
            # No stale am state — `git am --abort` ran cleanly.
            self.assertFalse((Path(outer) / ".git" / "rebase-apply").exists())

    def test_inner_split_author_committer_collapsed_to_outer_identity(self):
        """Lock-in test for the asymmetric author/committer handling:

        When the agent commits inside the workspace with SPLIT
        author/committer (via GIT_AUTHOR_*/GIT_COMMITTER_* env vars
        at commit time — e.g. author="Jane", committer="John"), the
        resulting outer commit must show the configured outer
        identity in BOTH fields. The agent's inner committer is
        not allowed to leak through.

        Underlying mechanism (this test pins it):
        - `git format-patch` carries author in the `From:` header
          but emits NO Committer header — committer info is dropped
          at the patch boundary. (Verified empirically:
          docs/git_patch_example.log shows no Committer line; the
          conversation around this test documents the experiment.)
        - `rewrite_from_header` rewrites the patch's `From:` line,
          so author becomes the configured outer identity.
        - `apply_patch_stream` sets GIT_COMMITTER_NAME/EMAIL env
          when calling `git am`, so the new commit's committer is
          the configured outer identity.

        The asymmetric design (stream rewrite for author, env for
        committer) is forced by git: there is no `git am` flag to
        override the author, and there is no committer info in the
        format-patch stream to rewrite.
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner: initial empty commit (inner_root) + agent commit
            # with SPLIT author/committer.
            subprocess.run(
                ["git", "init", "-b", "main", inner],
                capture_output=True,
                check=True,
            )
            git(inner, "config", "user.name", "Default Inner")
            git(inner, "config", "user.email", "default@inner.example.com")
            git(inner, "config", "commit.gpgsign", "false")
            git(inner, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(inner, "rev-parse", "HEAD")

            Path(inner, "file.txt").write_text("content\n")
            git(inner, "add", "file.txt")
            split_env = os.environ.copy()
            split_env["GIT_AUTHOR_NAME"] = "Jane Author"
            split_env["GIT_AUTHOR_EMAIL"] = "jane@inner.example.com"
            split_env["GIT_COMMITTER_NAME"] = "John Committer"
            split_env["GIT_COMMITTER_EMAIL"] = "john@inner.example.com"
            subprocess.run(
                ["git", "-C", inner, "commit", "-m", "agent: split-identity commit"],
                env=split_env,
                capture_output=True,
                check=True,
            )
            # Sanity: inner really has the split identity we set up.
            self.assertEqual(
                git(inner, "log", "-1", "--format=%an <%ae>"),
                "Jane Author <jane@inner.example.com>",
            )
            self.assertEqual(
                git(inner, "log", "-1", "--format=%cn <%ce>"),
                "John Committer <john@inner.example.com>",
            )

            stream = promote_mod.format_patch_stream(Path(inner), inner_root)

            self._make_outer_with_one_commit(outer)
            promote_mod.apply_patch_stream(
                Path(outer), stream, "Alice Example", "alice@example.com"
            )

            # Both fields of the promoted commit are Alice.
            self.assertEqual(
                git(outer, "log", "-1", "--format=%an <%ae>"),
                "Alice Example <alice@example.com>",
            )
            self.assertEqual(
                git(outer, "log", "-1", "--format=%cn <%ce>"),
                "Alice Example <alice@example.com>",
            )
            # Neither inner identity (Jane / John, or the domain)
            # appears anywhere in the outer's log — five-layer leak
            # check (each name in both casings + domain).
            log = git(outer, "log", "--all", "--format=%an %ae %cn %ce")
            for token in ("Jane", "jane", "John", "john", "inner.example.com"):
                self.assertNotIn(token, log, f"inner identity leak: {token!r}")

    def test_drops_empty_patches(self):
        """Inner has an empty agent commit (created with --allow-empty);
        its representation in the mbox stream lacks a diff section.
        apply_patch_stream uses `git am --empty=drop` to skip the empty
        e-mail message silently: no error raised, no new commit added.

        Spec: change_promotion_machinery.md L839-841 (Step 2.9).
        """
        with tempfile.TemporaryDirectory() as tmp:
            inner_ws = str(Path(tmp) / "inner")
            outer = str(Path(tmp) / "outer")

            # Inner: initial + an EMPTY agent commit (no diff).
            subprocess.run(
                ["git", "init", "-b", "main", inner_ws],
                capture_output=True,
                check=True,
            )
            git(inner_ws, "config", "user.name", "Patricia Garcia")
            git(inner_ws, "config", "user.email", "patricia@inner.example.com")
            git(inner_ws, "config", "commit.gpgsign", "false")
            git(inner_ws, "commit", "--allow-empty", "-m", "Initial commit")
            inner_root = git(inner_ws, "rev-parse", "HEAD")
            git(inner_ws, "commit", "--allow-empty", "-m", "agent: empty work")
            stream = promote_mod.format_patch_stream(Path(inner_ws), inner_root)

            # Sanity: the stream contains the empty patch's metadata
            # (otherwise we're not actually exercising --empty=drop).
            self.assertIn(b"agent: empty work", stream)

            self._make_outer_with_one_commit(outer)
            pre_count = int(git(outer, "rev-list", "--count", "HEAD"))

            # Must NOT raise.
            promote_mod.apply_patch_stream(
                Path(outer), stream, "Outer User", "user@outer.example.com"
            )

            # No new commit added — empty patch was dropped.
            post_count = int(git(outer, "rev-list", "--count", "HEAD"))
            self.assertEqual(post_count, pre_count)


class TestRewriteIdentity(unittest.TestCase):
    """Unit tests for the fast-export stream rewriting."""

    def test_rewrites_author_and_committer(self):
        stream = (
            b"commit refs/heads/main\n"
            b"author Old Name <old@email.com> 1234567890 +0000\n"
            b"committer Old Name <old@email.com> 1234567890 +0000\n"
            b"data 5\nhello\n"
        )
        result = promote_mod.rewrite_identity(stream, "New Name", "new@email.com")
        self.assertIn(b"author New Name <new@email.com> 1234567890 +0000", result)
        self.assertIn(b"committer New Name <new@email.com> 1234567890 +0000", result)

    def test_preserves_timestamps(self):
        stream = b"author X <x@x> 9999999999 +0530\n"
        result = promote_mod.rewrite_identity(stream, "Y", "y@y")
        self.assertIn(b"9999999999 +0530", result)

    def test_handles_multiple_commits(self):
        stream = (
            b"author A <a@a> 111 +0000\n"
            b"committer A <a@a> 111 +0000\n"
            b"author B <b@b> 222 +0000\n"
            b"committer B <b@b> 222 +0000\n"
        )
        result = promote_mod.rewrite_identity(stream, "Z", "z@z")
        self.assertEqual(result.count(b"author Z <z@z>"), 2)
        self.assertEqual(result.count(b"committer Z <z@z>"), 2)

    def test_does_not_touch_data_sections(self):
        stream = b"author A <a@a> 111 +0000\ndata 20\nauthor line in body\n"
        result = promote_mod.rewrite_identity(stream, "Z", "z@z")
        # The "author line in body" doesn't match the pattern (no timestamp)
        self.assertIn(b"author line in body", result)


class TestResolveIdentity(unittest.TestCase):
    """Unit tests for the three-layer identity resolution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = os.path.join(self.tmpdir, "target")
        os.makedirs(self.target)
        subprocess.run(["git", "init", self.target], capture_output=True, check=True)
        git(self.target, "config", "commit.gpgsign", "false")
        # Local fixture path — `resolve_identity` accepts any path, doesn't
        # care where it lives. Mirrors the real post-refactor location
        # (.alcatrazer/config.toml) for consistency with production callers.
        alcatraz_dir = Path(self.tmpdir) / ".alcatrazer"
        alcatraz_dir.mkdir()
        self.toml_file = alcatraz_dir / "config.toml"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_layer1_git_config(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        name, email = promote_mod.resolve_identity(
            Path(self.target),
            self.toml_file,
            "",
            "",
        )
        self.assertEqual(name, "Git User")
        self.assertEqual(email, "git@user.com")

    def test_layer2_toml_overrides_git(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        self.toml_file.write_text('[promotion]\nname = "TOML User"\nemail = "toml@user.com"\n')
        name, email = promote_mod.resolve_identity(
            Path(self.target),
            self.toml_file,
            "",
            "",
        )
        self.assertEqual(name, "TOML User")
        self.assertEqual(email, "toml@user.com")

    def test_layer3_cli_overrides_all(self):
        git(self.target, "config", "user.name", "Git User")
        git(self.target, "config", "user.email", "git@user.com")
        self.toml_file.write_text('[promotion]\nname = "TOML User"\nemail = "toml@user.com"\n')
        name, email = promote_mod.resolve_identity(
            Path(self.target),
            self.toml_file,
            "CLI User",
            "cli@user.com",
        )
        self.assertEqual(name, "CLI User")
        self.assertEqual(email, "cli@user.com")

    def test_missing_identity_exits(self):
        # Override HOME to isolate from global git config
        import os

        fake_home = os.path.join(self.tmpdir, "fakehome")
        os.makedirs(fake_home)
        env_patch = {"HOME": fake_home, "GIT_CONFIG_GLOBAL": "/dev/null"}
        with patch.dict(os.environ, env_patch), self.assertRaises(SystemExit):
            promote_mod.resolve_identity(
                Path(self.target),
                self.toml_file,
                "",
                "",
            )


# ── Integration tests (real git repos) ──────────────────────────────


class PromotionTestBase(unittest.TestCase):
    """Base: creates source + target repos, seeds source."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.source = Path(self.tmpdir) / "source"
        self.target = Path(self.tmpdir) / "target"
        self.marks = Path(self.tmpdir) / "marks"

        # Create and seed source repo
        self.source.mkdir()
        subprocess.run(["git", "init", str(self.source)], capture_output=True, check=True)
        git(str(self.source), "config", "user.name", ALCATRAZ_NAME)
        git(str(self.source), "config", "user.email", ALCATRAZ_EMAIL)
        git(str(self.source), "config", "commit.gpgsign", "false")
        subprocess.run([SEED_SCRIPT, str(self.source)], capture_output=True, check=True)

        # Create target repo
        self.target.mkdir()
        subprocess.run(["git", "init", str(self.target)], capture_output=True, check=True)
        git(str(self.target), "config", "user.name", PROMOTED_NAME)
        git(str(self.target), "config", "user.email", PROMOTED_EMAIL)
        git(str(self.target), "config", "commit.gpgsign", "false")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def do_promote(self):
        """Call promote() directly."""
        promote_mod.promote(self.source, self.target, self.marks, PROMOTED_NAME, PROMOTED_EMAIL)

    def do_dry_run(self) -> str:
        """Call dry_run() and capture stdout."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            promote_mod.dry_run(self.source, self.marks, PROMOTED_NAME, PROMOTED_EMAIL)
        return buf.getvalue()


class TestInitialPromotion(PromotionTestBase):
    """Tests after the first full promotion."""

    def setUp(self):
        super().setUp()
        self.do_promote()

    def test_same_commit_count(self):
        src = int(git(str(self.source), "rev-list", "--all", "--count"))
        tgt = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(src, tgt)

    def test_same_branches(self):
        src = sorted(git(str(self.source), "branch", "--format=%(refname:short)").splitlines())
        tgt = sorted(git(str(self.target), "branch", "--format=%(refname:short)").splitlines())
        self.assertEqual(src, tgt)

    def test_same_commit_messages(self):
        src = sorted(
            git(str(self.source), "log", "--all", "--topo-order", "--format=%s").splitlines()
        )
        tgt = sorted(
            git(str(self.target), "log", "--all", "--topo-order", "--format=%s").splitlines()
        )
        self.assertEqual(src, tgt)

    def test_merge_topology_preserved(self):
        def topology(repo):
            lines = git(repo, "log", "--all", "--topo-order", "--format=%s|%P").splitlines()
            result = []
            for line in lines:
                msg, _, parents = line.partition("|")
                pcount = len(parents.split()) if parents.strip() else 0
                result.append(f"{msg}|{pcount}")
            return sorted(result)

        self.assertEqual(topology(str(self.source)), topology(str(self.target)))

    def test_same_files_on_main(self):
        src = sorted(git(str(self.source), "ls-tree", "-r", "--name-only", "main").splitlines())
        tgt = sorted(git(str(self.target), "ls-tree", "-r", "--name-only", "main").splitlines())
        self.assertEqual(src, tgt)

    def test_file_contents_match(self):
        files = git(str(self.source), "ls-tree", "-r", "--name-only", "main").splitlines()
        for f in files:
            src = git(str(self.source), "show", f"main:{f}")
            tgt = git(str(self.target), "show", f"main:{f}")
            self.assertEqual(src, tgt, f"Content differs: {f}")

    def test_source_has_alcatraz_identity(self):
        authors = set(git(str(self.source), "log", "--all", "--format=%an <%ae>").splitlines())
        self.assertEqual(authors, {f"{ALCATRAZ_NAME} <{ALCATRAZ_EMAIL}>"})

    def test_target_has_promoted_author(self):
        authors = set(git(str(self.target), "log", "--all", "--format=%an <%ae>").splitlines())
        self.assertEqual(authors, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})

    def test_target_has_promoted_committer(self):
        committers = set(git(str(self.target), "log", "--all", "--format=%cn <%ce>").splitlines())
        self.assertEqual(committers, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})


class TestIncrementalPromotion(PromotionTestBase):
    """Tests for second promotion run after adding new commits."""

    def setUp(self):
        super().setUp()
        self.do_promote()

        git(str(self.source), "checkout", "main")
        (self.source / "new_feature.py").write_text("new feature\n")
        git(str(self.source), "add", "new_feature.py")
        git(str(self.source), "commit", "-m", "add new feature after first promotion")

        self.do_promote()

    def test_new_commit_promoted(self):
        src = int(git(str(self.source), "rev-list", "--all", "--count"))
        tgt = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(src, tgt)

    def test_new_commit_message_present(self):
        msgs = git(str(self.target), "log", "--all", "--format=%s").splitlines()
        self.assertIn("add new feature after first promotion", msgs)

    def test_new_commit_has_promoted_identity(self):
        author = git(str(self.target), "log", "-1", "--format=%an <%ae>", "main")
        self.assertEqual(author, f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>")


class TestDryRun(PromotionTestBase):
    """Tests for dry_run()."""

    def setUp(self):
        super().setUp()
        self.do_promote()

    def test_dry_run_up_to_date(self):
        output = self.do_dry_run()
        self.assertIn("Nothing to promote", output)

    def test_dry_run_does_not_modify_target(self):
        count_before = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.do_dry_run()
        count_after = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)

    def test_dry_run_with_pending_commits(self):
        git(str(self.source), "checkout", "main")
        (self.source / "pending1.py").write_text("pending 1\n")
        git(str(self.source), "add", "pending1.py")
        git(str(self.source), "commit", "-m", "pending commit 1")

        git(str(self.source), "checkout", "-b", "dry-run-test-branch")
        (self.source / "pending2.py").write_text("pending 2\n")
        git(str(self.source), "add", "pending2.py")
        git(str(self.source), "commit", "-m", "pending commit 2 on branch")
        git(str(self.source), "checkout", "main")

        output = self.do_dry_run()
        self.assertIn("2 commit(s) would be promoted", output)
        self.assertIn(f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>", output)

    def test_dry_run_pending_does_not_modify_target(self):
        git(str(self.source), "checkout", "main")
        (self.source / "pending.py").write_text("pending\n")
        git(str(self.source), "add", "pending.py")
        git(str(self.source), "commit", "-m", "pending")

        count_before = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.do_dry_run()
        count_after = int(git(str(self.target), "rev-list", "--all", "--count"))
        self.assertEqual(count_before, count_after)


class TestBinaryBlobPromotion(PromotionTestBase):
    """Promote a repo whose history contains a non-UTF-8 blob.

    Regression guard for a bug where `subprocess.run(..., text=True)` on
    `git fast-export` raised `UnicodeDecodeError` the moment the stream
    hit a byte outside UTF-8 (e.g. `0xff` in a PNG / compressed blob),
    leaving the marks files desynced and the daemon unable to recover.
    See docs/features/install_method.md → "Manual tests and
    troubleshooting" for the field incident that motivated this test.
    """

    # Bytes that are invalid as UTF-8 — 0xff is the exact byte that
    # tripped the production daemon; 0xfe / 0x80 cover the other common
    # shapes (BOM-ish, stray continuation byte).
    BINARY_PAYLOAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\xfe\x80\x81\x82\x83\x84\x85\x86\x87"

    def setUp(self):
        super().setUp()
        blob_path = self.source / "logo.bin"
        blob_path.write_bytes(self.BINARY_PAYLOAD * 64)
        git(str(self.source), "add", "logo.bin")
        git(str(self.source), "commit", "-m", "add binary blob to history")

    def test_promote_succeeds_with_binary_blob_in_history(self):
        self.do_promote()

    def test_binary_blob_round_trips_byte_for_byte(self):
        self.do_promote()
        src_bytes = subprocess.run(
            ["git", "-C", str(self.source), "show", "main:logo.bin"],
            capture_output=True,
            check=True,
        ).stdout
        tgt_bytes = subprocess.run(
            ["git", "-C", str(self.target), "show", "main:logo.bin"],
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(src_bytes, tgt_bytes)

    def test_incremental_promote_after_binary_blob(self):
        """Second poll must not wedge on mark desync — the bug's tail."""
        self.do_promote()
        (self.source / "followup.txt").write_text("after the blob\n")
        git(str(self.source), "add", "followup.txt")
        git(str(self.source), "commit", "-m", "text commit after blob")
        self.do_promote()
        msgs = git(str(self.target), "log", "--all", "--format=%s").splitlines()
        self.assertIn("text commit after blob", msgs)


class TestNamespacePromotion(PromotionTestBase):
    """Tests for promoting into a namespace (alcatraz-tree mode)."""

    def test_namespace_rewrites_branch_names(self):
        """Promote with namespace should map main -> alcatraz/main."""
        promote_mod.promote(
            self.source,
            self.target,
            self.marks,
            PROMOTED_NAME,
            PROMOTED_EMAIL,
            namespace="alcatraz",
        )
        target_branches = sorted(
            git(str(self.target), "branch", "--format=%(refname:short)").splitlines()
        )
        self.assertIn("alcatraz/main", target_branches)
        self.assertIn("alcatraz/feature/auth", target_branches)
        self.assertNotIn("main", target_branches)

    def test_namespace_preserves_content(self):
        """Namespaced promotion should have the same file content."""
        promote_mod.promote(
            self.source,
            self.target,
            self.marks,
            PROMOTED_NAME,
            PROMOTED_EMAIL,
            namespace="alcatraz",
        )
        src_files = sorted(
            git(str(self.source), "ls-tree", "-r", "--name-only", "main").splitlines()
        )
        tgt_files = sorted(
            git(str(self.target), "ls-tree", "-r", "--name-only", "alcatraz/main").splitlines()
        )
        self.assertEqual(src_files, tgt_files)

    def test_namespace_rewrites_identity(self):
        """Namespaced promotion should still rewrite identity."""
        promote_mod.promote(
            self.source,
            self.target,
            self.marks,
            PROMOTED_NAME,
            PROMOTED_EMAIL,
            namespace="alcatraz",
        )
        authors = set(git(str(self.target), "log", "--all", "--format=%an <%ae>").splitlines())
        self.assertEqual(authors, {f"{PROMOTED_NAME} <{PROMOTED_EMAIL}>"})


if __name__ == "__main__":
    unittest.main()
