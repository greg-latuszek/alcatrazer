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
        "build",
        "image_exists",
        "start",
        "stop",
        "is_running",
        "exec",
        "remove",
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

    def test_build_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.build()

    def test_image_exists_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.image_exists()

    def test_start_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.start()

    def test_stop_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.stop()

    def test_is_running_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.is_running()

    def test_exec_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.exec(["echo", "hi"])

    def test_remove_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.prison.remove()


if __name__ == "__main__":
    unittest.main()
