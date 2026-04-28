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


class PrisonError(Exception):
    """Base for adapter operational errors carrying raw subprocess output.

    Per the "Build & Startup Error Handling" contract in install_method.md,
    callers surface `stdout` / `stderr` to the user along with a pointer to
    the phase that failed.
    """

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class PrisonBuildError(PrisonError):
    """Raised when an Alcatraz adapter's `build()` fails."""


class PrisonStartError(PrisonError):
    """Raised when an Alcatraz adapter's `start()` fails."""


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
    def needs_rebuild(self, coding_environment: dict) -> bool:
        """True when the on-disk recipe differs from what `coding_environment`
        would produce — i.e., the image must be rebuilt.

        The adapter owns the comparison (DockerPrison diffs the would-be
        Dockerfile against `.alcatrazer/Dockerfile`); the orchestrator just
        asks the boolean.
        """

    @abstractmethod
    def build(self) -> None:
        """Build the workspace image from `.alcatrazer/Dockerfile`."""

    @abstractmethod
    def image_exists(self) -> bool:
        """Whether the workspace image currently exists locally."""

    @abstractmethod
    def start(self) -> None:
        """Create a fresh Alcatraz instance and start it in detached mode.

        Always produces a new instance — any previous writable state (caches
        in the container's overlay layer, process-local /tmp, …) is discarded
        by the caller's earlier `remove()` before this runs. Use `resume()`
        when you want to preserve that state.
        """

    @abstractmethod
    def resume(self) -> None:
        """Bring a stopped Alcatraz back up with its writable state intact.

        Distinct from `start()`: resume re-enters an *existing* instance
        (stopped via `stop()`) and preserves its writable overlay layer,
        including any caches (mise / pip / npm) that live there per the
        "Ephemeral caches — no shared Docker volumes" rule. Raises
        `PrisonStartError` if no such instance exists or it fails to come up.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop the running workspace container. No-op if already stopped."""

    @abstractmethod
    def is_running(self) -> bool:
        """Whether the workspace container is currently running."""

    @abstractmethod
    def exists(self) -> bool:
        """Whether an Alcatraz instance with the configured identity exists.

        Covers BOTH running and stopped instances — unlike `is_running()`.
        The `_subsequent_run` lifecycle uses this to distinguish
        "no instance, must `start`" from "stopped instance, can `resume`".
        """

    @abstractmethod
    def exec(self, command: list[str]) -> int:
        """Run a command inside the running workspace container. Return exit code.

        Output streams directly to the caller's terminal — for human-facing
        work (startup commands, interactive attaches) where live progress
        matters. If you need to capture output for programmatic inspection,
        use `query` instead.
        """

    @abstractmethod
    def query(self, command: list[str]):
        """Run a command inside the workspace and return the captured result.

        Unlike `exec` (which streams output for humans), `query` captures
        stdout / stderr / exit code and returns them as a
        `subprocess.CompletedProcess`-shaped object so programs can read
        and decide — security self-tests, health checks, diagnostics.
        """

    @abstractmethod
    def remove(self) -> None:
        """Remove the workspace container (stop first if running). No-op if absent."""

    @abstractmethod
    def shell(self) -> None:
        """Open an interactive shell as agent inside the running sandbox.

        Replaces the calling process via execvp (or its backend equivalent)
        — never returns normally on success; signals (Ctrl+C, Ctrl+D) and
        the eventual exit status flow through to the user's terminal as if
        they'd run a shell directly.

        Raises ``PrisonStartError`` when the sandbox isn't running. The
        caller (``cmd_visit``) catches this and prints a friendly message
        rather than expecting auto-start.

        Backend-agnostic — DockerPrison implements via ``docker exec -it``;
        future backends (FirecrackerPrison, VMPrison, …) implement via
        whatever their interactive-attach mechanism is.
        """
