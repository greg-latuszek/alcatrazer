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
    def query(self, command: list[str], input: str | None = None):
        """Run a command inside the workspace and return the captured result.

        Unlike `exec` (which streams output for humans), `query` captures
        stdout / stderr / exit code and returns them as a
        `subprocess.CompletedProcess`-shaped object so programs can read
        and decide — security self-tests, health checks, diagnostics.

        When `input` is given, it is fed to the command on stdin rather than
        passed as an argument — so a secret (e.g. a credential token piped to
        `cat >file`) never appears in argv / a process listing. Backends
        implement this over their own stdin channel (DockerPrison adds
        `docker exec -i`; a future VM backend pipes to the remote shell's
        stdin), keeping credential provisioning backend-neutral.
        """

    @abstractmethod
    def remove(self) -> None:
        """Remove the workspace container (stop first if running). No-op if absent."""

    @abstractmethod
    def wipe_workspace_contents(self) -> None:
        """Remove every file inside the workspace bind-mount, leaving
        the mount-point directory itself in place so the next
        ``start`` can re-snapshot into the same target.

        Caller contract: the original container is **stopped** when
        this is called (``cmd_clear``'s race-safety dance — stop
        agents, drain via daemon final-sync, then wipe). The backend
        is free to choose how to perform the removal — via a one-shot
        side container, via the host with appropriate privileges,
        whatever — so long as it preserves Principle 2: no agent
        process must observe foreign UIDs, foreign processes, or any
        signal that betrays the Alcatrazer machinery from inside an
        active sandbox.

        Used by ``alcatrazer clear`` to make the teardown terminal:
        the next ``start`` becomes a fresh first-run with a new pin
        to the user's currently-checked-out branch, rather than
        reusing the old workspace + old pin.

        Backend-agnostic.

        Raises ``PrisonError`` if the removal fails so ``cmd_clear``
        can abort cleanly rather than discard the container with stale
        files on disk.
        """

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

    @abstractmethod
    def recipe_hash(self, coding_environment: dict) -> str:
        """Hash of the recipe the adapter would build right now for
        ``coding_environment``.

        Phase 1.2.6: paired with ``image_matches`` so ``cmd_start`` can
        decide whether the running image is current without knowing
        what kind of recipe the adapter uses (Dockerfile, VM cloud-init,
        snapshot config, …). DockerPrison hashes the Dockerfile body;
        future backends hash whatever they bake.
        """

    @abstractmethod
    def image_matches(self, expected_hash: str) -> bool:
        """Whether the running image was built from the recipe whose
        hash equals ``expected_hash``.

        Phase 1.2.6 staleness detection: closes the gap where today's
        ``image_exists()`` returns True for an image built from an
        older config (e.g. user wiped ``.alcatrazer/`` and re-ran init).
        Returns False on: missing image, image without the
        adapter's identity label, label-vs-expected mismatch.
        """
