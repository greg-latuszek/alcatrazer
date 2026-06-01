"""Tests for the Alcatraz hexagonal sandboxing architecture.

`Alcatraz` is the abstract port defining the operations the installer needs
from any sandboxing backend. `DockerPrison` is the only concrete adapter for
MVP; each method is a skeleton that raises NotImplementedError until the
installer step that needs it lands.
"""

import unittest
from abc import ABC
from pathlib import Path

from alcatrazer.alcatraz import Alcatraz
from alcatrazer.docker_prison import DockerPrison

ALCATRAZ_OPERATIONS = frozenset(
    {
        "generate_prison",
        "needs_rebuild",
        "build",
        "image_exists",
        "start",
        "resume",
        "stop",
        "is_running",
        "exists",
        "exec",
        "query",
        "copy_out",
        "remove",
        # Phase 1.2.5: open an interactive shell as agent inside the
        # running sandbox. Backend-agnostic — DockerPrison implements
        # via `docker exec -it`, future backends do whatever their
        # interactive-attach equivalent is.
        "shell",
        # Phase 1.2.6: identity of the recipe the adapter would build
        # right now (used by cmd_start to know what hash to expect from
        # the running image), and the corresponding "is the running
        # image built from THIS recipe?" check.
        "recipe_hash",
        "image_matches",
        # Phase 9: wipe the workspace bind-mount's contents during
        # `alcatrazer clear` so the next `start` is a fresh first-run
        # on the user's current branch. Backend-agnostic — DockerPrison
        # implements via a one-shot side container with --entrypoint
        # find + -u agent; future backends do whatever their
        # disposable-context-with-bind-mount equivalent is.
        "wipe_workspace_contents",
    }
)


class AlcatrazAbcTests(unittest.TestCase):
    """Alcatraz must be a true abstract base class with the full operation surface."""

    def test_is_an_abstract_base_class(self):
        self.assertTrue(issubclass(Alcatraz, ABC))

    def test_cannot_instantiate_directly(self):
        with self.assertRaises(TypeError):
            Alcatraz(project_dir=Path("/tmp"))  # type: ignore[abstract]

    def test_declares_the_full_operation_surface(self):
        self.assertEqual(Alcatraz.__abstractmethods__, ALCATRAZ_OPERATIONS)


class DockerPrisonSkeletonTests(unittest.TestCase):
    """DockerPrison inherits Alcatraz and is instantiable; every operation is
    a skeleton that raises NotImplementedError until its step lands."""

    def setUp(self):
        self.prison = DockerPrison(project_dir=Path("/tmp"))

    def test_inherits_from_alcatraz(self):
        self.assertIsInstance(self.prison, Alcatraz)

    def test_stores_project_dir(self):
        self.assertEqual(self.prison.project_dir, Path("/tmp"))


class WipeWorkspaceContentsAbcTests(unittest.TestCase):
    """Phase 9 Step 9.1 — `Alcatraz.wipe_workspace_contents` is a backend-
    neutral port method that removes everything inside the workspace bind-
    mount from inside the container (as the agent UID, no chown, no
    sudo). cmd_clear calls it to make `clear` terminal: drop the inner
    repo + working files so the next `start` is a fresh first-run with
    a new pin.

    Backend-neutrality matters: the call site (cmd_clear) doesn't know
    whether the backend is Docker, Podman, a future Sysbox, or a VM-
    based prison. The port declares the operation; each backend
    implements it however its container model allows.

    See docs/features/change_promotion_machinery.md Phase 9 for the
    rationale (wipe-from-inside vs chown-back: stealth) and the
    ordering rules (resume → wipe → stop → remove) cmd_clear uses
    around it."""

    def test_alcatraz_declares_wipe_workspace_contents_as_abstract(self):
        self.assertIn("wipe_workspace_contents", Alcatraz.__abstractmethods__)


if __name__ == "__main__":
    unittest.main()
