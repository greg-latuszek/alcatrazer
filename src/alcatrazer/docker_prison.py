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
from pathlib import Path

from alcatrazer.alcatraz import Alcatraz
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

    os_block = _render_apt_install(data.get("os", {}).get("packages", []))
    if os_block:
        dev_stage.append("")
        dev_stage.append(os_block.rstrip())

    languages = data.get("languages", {})
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

    def build(self) -> None:
        raise NotImplementedError("DockerPrison.build lands in Step 3i")

    def image_exists(self) -> bool:
        raise NotImplementedError("DockerPrison.image_exists lands in Step 4")

    def start(self) -> None:
        raise NotImplementedError("DockerPrison.start lands in Step 3k")

    def stop(self) -> None:
        raise NotImplementedError("DockerPrison.stop lands in Step 5")

    def is_running(self) -> bool:
        raise NotImplementedError("DockerPrison.is_running lands in Step 4")

    def exec(self, command: list[str]) -> int:
        raise NotImplementedError("DockerPrison.exec lands in Step 3k")

    def remove(self) -> None:
        raise NotImplementedError("DockerPrison.remove lands in Step 4")
