"""DockerPrison — the docker-backed Alcatraz adapter.

Currently the only Alcatraz implementation. Shells out to `docker build / run /
start / stop / ps / exec / rm` subprocesses for operational sandbox methods,
and renders a three-stage Dockerfile + copies entrypoint.sh for
`generate_prison()`.

Operational methods are skeletons for now; each is filled in by the installer
step that first needs it:

- `build` — Step 3i
- `start`, `exec` — Step 3k
- `image_exists`, `is_running`, `remove` — Step 4 (subsequent-run detection)
- `stop` — Step 5
"""

import shutil
import subprocess
from pathlib import Path

from alcatrazer import identity
from alcatrazer.alcatraz import Alcatraz, PrisonBuildError, PrisonStartError
from alcatrazer.languages import SUPPORTED_LANGUAGES

# --- Dockerfile generation ---------------------------------------------------

_DOCKERFILE_DEV_BASE = """\
# =============================================================================
# Stage 1: dev-base — security + core infrastructure (always identical).
# Contains only what alcatrazer's machinery itself needs. Dev-ergonomics
# packages (tmux, ripgrep, ...) and project build deps (build-essential,
# libpq-dev, ...) belong in the user's [os] section, not here.
# =============================================================================

FROM ubuntu:24.04 AS dev-base

ENV DEBIAN_FRONTEND=noninteractive

# Phantom UID — does not exist on the host. If the container escapes, the
# process cannot write to any host files.
ARG USER_UID
RUN test -n "${USER_UID}" || { echo "ERROR: USER_UID build arg is required."; exit 1; }

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl ca-certificates gosu \\
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with the phantom UID.
RUN groupadd --gid ${USER_UID} agent && \\
    useradd --uid ${USER_UID} --gid ${USER_UID} --create-home --shell /bin/bash agent

USER agent
ENV HOME=/home/agent
ENV PATH="/home/agent/.local/bin:${PATH}"

# Install mise (multi-language version manager) and activate its shims.
RUN curl https://mise.run | sh
ENV PATH="/home/agent/.local/share/mise/shims:${PATH}"

# Disable commit signing — no access to host signing keys inside the container.
RUN git config --global commit.gpgsign false
"""


_DOCKERFILE_AI_BASE = """\
# =============================================================================
# Stage 2: ai-base — AI agent CLI layer. Hardcoded for MVP (Claude Code).
# A future step will generate this stage from an [ai] section of
# coding-environment.toml so a repo can pick Claude, another agent, or none.
# =============================================================================

FROM dev-base AS ai-base

USER agent

RUN curl -fsSL https://claude.ai/install.sh | bash
"""


_DOCKERFILE_ENTRYPOINT_TAIL = """\
# Back to root for entrypoint — it drops to the agent user via gosu.
USER root

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
CMD ["/bin/bash"]
"""


def _render_apt_install(packages: list[str]) -> str:
    """Render a Stage 3 apt-get block for [os] packages. Empty when none."""
    if not packages:
        return ""
    pkgs = " ".join(packages)
    return (
        "USER root\n"
        f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs} \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
        "USER agent\n"
    )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Return ``items`` with subsequent duplicates dropped, original order kept.

    Used for merging user-declared and tool-injected lists (currently apt
    package lists, but the helper is generic). The user's list comes first
    in user order; tool-injected items appended after. ``sorted(set(...))``
    is deliberately avoided — silent reordering of user input can break
    dependency ordering for order-sensitive tooling, and it overrides
    explicit user intent.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _render_mise_uses(languages: dict) -> str:
    """Render the `mise use --global` block covering runtimes + non-default managers."""
    if not languages:
        return ""
    uses: list[str] = []
    for lang, cfg in languages.items():
        uses.append(f"{lang}@{cfg['version']}")
        if "manager" in cfg:
            uses.append(cfg["manager"])
    commands = [f"mise use --global {u}" for u in uses]
    return "RUN " + " && \\\n    ".join(commands) + "\n"


def _render_verify_block(languages: dict) -> str:
    """Render the post-install verify RUN — always-present tools + per-language checks."""
    always_present = ["git --version", "mise --version", "claude --version"]
    per_language = [SUPPORTED_LANGUAGES[name]["version_check"] for name in languages]
    commands = always_present + per_language
    return "RUN " + " && \\\n    ".join(commands) + "\n"


def _render_dockerfile(data: dict) -> str:
    """Build the full three-stage Dockerfile text."""
    parts: list[str] = [
        _DOCKERFILE_DEV_BASE.rstrip(),
        _DOCKERFILE_AI_BASE.rstrip(),
    ]

    dev_stage = [
        "# =============================================================================",
        "# Stage 3: dev — language runtimes and project bits, generated from",
        "# coding-environment.toml.",
        "# =============================================================================",
        "",
        "FROM ai-base AS dev",
        "",
        "USER agent",
    ]

    languages = data.get("languages", {})
    user_pkgs = data.get("os", {}).get("packages", [])
    # Merge user-declared packages with every declared language's
    # required_os_packages (deps that the runtime needs at startup, e.g.
    # .NET → libicu74). Order rule: user list first in user-declared
    # order, then language-injected packages in language-declaration
    # order. Dedupe is by first occurrence — never sort, because
    # silent reordering of a user-supplied list can break dependency
    # ordering for any package manager that's order-sensitive (apt
    # happens to tolerate it; we don't assume future tooling will).
    lang_pkgs: list[str] = []
    for name in languages:
        lang_pkgs.extend(SUPPORTED_LANGUAGES[name].get("required_os_packages", ()))
    all_pkgs = _dedupe_preserve_order(list(user_pkgs) + lang_pkgs)
    os_block = _render_apt_install(all_pkgs)
    if os_block:
        dev_stage.append("")
        dev_stage.append(os_block.rstrip())

    mise_block = _render_mise_uses(languages)
    if mise_block:
        dev_stage.append("")
        dev_stage.append(mise_block.rstrip())

    dev_stage.append("")
    dev_stage.append(_render_verify_block(languages).rstrip())

    parts.append("\n".join(dev_stage))
    parts.append(_DOCKERFILE_ENTRYPOINT_TAIL.rstrip())

    return "\n\n".join(parts) + "\n"


# --- Adapter -----------------------------------------------------------------


class DockerPrison(Alcatraz):
    """Docker-backed sandbox adapter."""

    def __init__(
        self,
        project_dir: Path,
        image_tag: str = "alcatraz-workspace:local",
        container_name: str = "workspace",
    ):
        super().__init__(project_dir)
        self.image_tag = image_tag
        self.container_name = container_name

    def generate_prison(self, coding_environment: dict) -> None:
        """Write `.alcatrazer/Dockerfile` + `.alcatrazer/entrypoint.sh` from
        the coding-environment data."""
        alcatrazer_dir = self.project_dir / ".alcatrazer"
        alcatrazer_dir.mkdir(parents=True, exist_ok=True)
        (alcatrazer_dir / "Dockerfile").write_text(_render_dockerfile(coding_environment))
        source = Path(__file__).parent / "container" / "entrypoint.sh"
        shutil.copy(source, alcatrazer_dir / "entrypoint.sh")

    def needs_rebuild(self, coding_environment: dict) -> bool:
        """True when the on-disk Dockerfile differs from what the current
        `coding_environment` would produce (or no Dockerfile exists yet)."""
        dockerfile = self.project_dir / ".alcatrazer" / "Dockerfile"
        if not dockerfile.exists():
            return True
        return _render_dockerfile(coding_environment) != dockerfile.read_text()

    def build(self) -> None:
        """Run `docker build` with the generated Dockerfile.

        Uses a tight build context (`.alcatrazer/` only), the phantom UID as
        the USER_UID build arg, and captures stdout/stderr so callers can
        present them per the error-reporting contract.
        """
        alcatraz_dir = self.project_dir / ".alcatrazer"
        uid = identity.ensure_phantom_uid(alcatraz_dir)
        cmd = [
            "docker",
            "build",
            "--build-arg",
            f"USER_UID={uid}",
            "-f",
            str(alcatraz_dir / "Dockerfile"),
            "-t",
            self.image_tag,
            str(alcatraz_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise PrisonBuildError(
                f"docker build failed (exit {result.returncode})",
                stdout=result.stdout,
                stderr=result.stderr,
            )

    def image_exists(self) -> bool:
        """True when `docker image inspect <tag>` succeeds."""
        result = subprocess.run(
            ["docker", "image", "inspect", self.image_tag],
            capture_output=True,
        )
        return result.returncode == 0

    def start(self) -> None:
        """Run the workspace container in detached mode.

        Bind-mounts the workspace dir at /workspace, mounts the host's
        Claude credentials read-only (when present), wires in `.env`, and
        uses `sleep infinity` as the long-lived CMD so the container stays
        alive for later `docker exec` attaches.

        **No named cache volumes** — mise / pip / npm caches live in the
        container's writable overlay layer, per the "Ephemeral caches —
        no shared Docker volumes" section in install_method.md. Sharing
        writable volumes across Alcatrazes would let one compromised
        agent poison every other Alcatraz on the laptop via cache
        tampering; keeping caches per-container closes that attack
        surface by construction. Caches clear on full recreate (rebuild
        / env change / `alcatrazer clear`) and persist across stop+start
        (resume).
        """
        alcatraz_dir = self.project_dir / ".alcatrazer"
        workspace_name = identity.load_workspace_dir(str(alcatraz_dir))
        if workspace_name is None:
            raise PrisonStartError(
                "No workspace dir recorded at .alcatrazer/workspace-dir; "
                "call create_workspace first."
            )
        workspace_path = self.project_dir / workspace_name
        env_file = self.project_dir / ".env"
        claude_creds = Path.home() / ".claude" / ".credentials.json"

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "-v",
            f"{workspace_path}:/workspace",
        ]
        if claude_creds.exists():
            cmd += [
                "-v",
                f"{claude_creds}:/home/agent/.claude/.credentials.json:ro",
            ]
        if env_file.exists():
            cmd += ["--env-file", str(env_file)]
        cmd += [self.image_tag, "sleep", "infinity"]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise PrisonStartError(
                f"docker run failed (exit {result.returncode})",
                stdout=result.stdout,
                stderr=result.stderr,
            )

    def resume(self) -> None:
        """Resume a previously-stopped Alcatraz via `docker start <name>`.

        Distinct from `start()`: resume re-enters the *same* container the
        user stopped earlier, preserving its writable overlay layer (and
        thus any caches the workspace has populated). `start()` always
        creates a fresh container via `docker run` and discards prior
        writable state.
        """
        result = subprocess.run(
            ["docker", "start", self.container_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PrisonStartError(
                f"docker start failed (exit {result.returncode})",
                stdout=result.stdout,
                stderr=result.stderr,
            )

    def stop(self) -> None:
        """Stop the container if running; no-op otherwise."""
        if not self.is_running():
            return
        subprocess.run(
            ["docker", "stop", self.container_name],
            capture_output=True,
            check=True,
        )

    def is_running(self) -> bool:
        """True when a container matching `container_name` is in state=running.

        Uses anchored regex (`name=^workspace$`) so it doesn't match
        containers whose names contain "workspace" as a substring.
        """
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name=^{self.container_name}$",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == self.container_name

    def exists(self) -> bool:
        """True when a container matching `container_name` exists (any state).

        Port method — backend-neutral name. Uses `docker ps -a` so stopped
        containers count too (distinct from `is_running()` which filters
        status=running). `_subsequent_run` depends on this distinction to
        tell "no container, must `start`" apart from "stopped container,
        can `resume`".
        """
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{self.container_name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == self.container_name

    def exec(self, command: list[str]) -> int:
        """Run `command` inside the running workspace container as the `agent` user.

        Output is NOT captured — it streams to the caller's terminal so
        long-running commands like `uv sync` show progress in real time.
        Returns the command's exit code; the caller decides how to react.
        """
        full = ["docker", "exec", "-u", "agent", self.container_name, *command]
        return subprocess.run(full).returncode

    def query(self, command: list[str]) -> subprocess.CompletedProcess:
        """Run `command` inside the workspace container as `agent` and return
        the captured result. stdout / stderr / returncode are inspectable
        on the returned object; non-zero exit does NOT raise — the caller
        decides (mirrors `exec`'s "return code, don't throw" contract).
        """
        full = ["docker", "exec", "-u", "agent", self.container_name, *command]
        return subprocess.run(full, capture_output=True, text=True)

    def remove(self) -> None:
        """Remove the container (force, so running containers go too). No-op if absent."""
        if not self.exists():
            return
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            check=True,
        )
