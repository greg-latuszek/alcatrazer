"""Alcatraz — the sandboxing backend port.

Abstract interface that any sandboxing backend must implement to serve as a
"prison" for AI agents. The current adapter is `alcatrazer.docker_prison.
DockerPrison` (shells out to the `docker` CLI). Future adapters could target
podman, sysbox, or other backends.

Installer functions that need sandbox operations accept an `Alcatraz`
instance with a `DockerPrison` default, so tests can inject a mock and future
backends plug in without editing callers. See install_method.md — "Hexagonal
sandboxing architecture" — for the design rationale.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class Alcatraz(ABC):
    """The sandboxing port — a prison that isolates AI agents from the host.

    Subclasses adapt this interface to a concrete sandboxing technology. All
    operations are parameter-light: the prison reads anything it needs (image
    tag, container name, mounts, env file, …) from `project_dir/.alcatrazer/`
    state, keeping callers decoupled from implementation details.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    @abstractmethod
    def generate_prison(self, coding_environment: dict) -> None:
        """Emit backend-specific artifacts (recipes, scripts, …) required by `build()`.

        The caller hands over the parsed coding-environment data; the adapter
        decides what to write. For DockerPrison that is `.alcatrazer/Dockerfile`
        and `.alcatrazer/entrypoint.sh`; another backend might emit a
        Containerfile, a podman-quadlet unit, a sysbox profile, etc.
        """

    @abstractmethod
    def build(self) -> None:
        """Build the workspace image from `.alcatrazer/Dockerfile`."""

    @abstractmethod
    def image_exists(self) -> bool:
        """Whether the workspace image currently exists locally."""

    @abstractmethod
    def start(self) -> None:
        """Start the workspace container in detached mode (build first if needed)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the running workspace container. No-op if already stopped."""

    @abstractmethod
    def is_running(self) -> bool:
        """Whether the workspace container is currently running."""

    @abstractmethod
    def exec(self, command: list[str]) -> int:
        """Run a command inside the running workspace container. Return exit code."""

    @abstractmethod
    def remove(self) -> None:
        """Remove the workspace container (stop first if running). No-op if absent."""
