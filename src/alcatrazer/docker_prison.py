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

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from alcatrazer import identity
from alcatrazer.alcatraz import Alcatraz, PrisonBuildError, PrisonStartError
from alcatrazer.languages import SUPPORTED_LANGUAGES

# --- Per-repo identity (Phase 1.2.5) -----------------------------------------
#
# DockerPrison's image tag and container name are derived from the project's
# canonical absolute path so multiple alcatrazers on one machine coexist
# without collision (the previous hardcoded `alcatraz-workspace:local` and
# `workspace` would clash across repos). The hash gives mathematical
# uniqueness; the sanitized basename gives glance-readability in `docker ps`.

_BASENAME_INVALID_CHARS = re.compile(r"[^a-z0-9.-]+")
_BASENAME_RUNS_OF_HYPHENS = re.compile(r"-{2,}")


def _sanitize_basename(name: str) -> str:
    """Reduce a path basename to Docker-tag-safe characters.

    Steps: lowercase → replace any non-`[a-z0-9.-]` with `-` → collapse
    runs of `-` → strip leading/trailing `-` and `.` → fall back to
    `repo` if the result is empty. Stable mapping; same input always
    produces the same output.
    """
    out = _BASENAME_INVALID_CHARS.sub("-", name.lower())
    out = _BASENAME_RUNS_OF_HYPHENS.sub("-", out)
    out = out.strip("-.")
    return out or "repo"


def _identity_for_project(project_dir: Path) -> str:
    """Return `<sanitized-basename>-<12-hex-hash>` for a project path.

    The hash is SHA-256 of the canonical absolute path (resolves
    symlinks), truncated to 12 hex chars (~48 bits — birthday bound at
    ~16M repos on one laptop, effectively never collides). Used by
    `DockerPrison` to derive image tag and container name so two
    alcatrazers on different repos coexist without naming collision.
    """
    canonical = project_dir.resolve()
    sanitized = _sanitize_basename(canonical.name)
    digest = hashlib.sha256(str(canonical).encode()).hexdigest()[:12]
    return f"{sanitized}-{digest}"


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
    """Render the `mise use --global` block covering runtimes + non-bundled managers.

    Phase 1.2.4: install rule changed from "non-default manager" to
    "non-bundled manager". `bundled_managers` (per-language tuple of
    managers that ship with the runtime — `("pip",)` for python, etc.)
    drives the decision. Empty tuple (java) means EVERY picked manager
    installs separately, including the default `maven`. Fixes the bug
    where accepting Java's default left Maven uninstalled.
    """
    if not languages:
        return ""
    uses: list[str] = []
    for lang, cfg in languages.items():
        uses.append(f"{lang}@{cfg['version']}")
        manager = cfg.get("manager") or SUPPORTED_LANGUAGES[lang]["default_manager"]
        if manager not in SUPPORTED_LANGUAGES[lang].get("bundled_managers", ()):
            uses.append(manager)
    commands = [f"mise use --global {u}" for u in uses]
    return "RUN " + " && \\\n    ".join(commands) + "\n"


def _render_verify_block(languages: dict) -> str:
    """Render the post-install verify RUN — always-present tools + per-language checks."""
    always_present = ["git --version", "mise --version", "claude --version"]
    per_language = [SUPPORTED_LANGUAGES[name]["version_check"] for name in languages]
    commands = always_present + per_language
    return "RUN " + " && \\\n    ".join(commands) + "\n"


def _render_dockerfile_body(data: dict) -> str:
    """Render the Dockerfile WITHOUT the alcatrazer.config_hash LABEL.

    Phase 1.2.6: this is the canonical body used both as the input for
    `_compute_config_hash` (avoids chicken-and-egg with the LABEL line)
    AND as the body of the final Dockerfile. The public
    `_render_dockerfile` calls this, computes the hash, and appends the
    LABEL.
    """
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


def _compute_config_hash(data: dict) -> str:
    """Phase 1.2.6: SHA-256 of the Dockerfile body (sans LABEL) — first
    16 hex chars. Baked into the rendered Dockerfile via a LABEL so the
    running image carries its own identity, and recomputed at start time
    to detect when the on-disk recipe has drifted from what built the
    image (covers `rm -rf .alcatrazer/` recovery and similar)."""
    body = _render_dockerfile_body(data)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _render_dockerfile(data: dict) -> str:
    """Build the full Dockerfile text — body + alcatrazer.config_hash LABEL.

    The LABEL is appended to stage 3 (the dev stage) by string-splicing
    just before `_DOCKERFILE_ENTRYPOINT_TAIL` so it lands inside the
    final image's metadata. Computed via `_compute_config_hash` so the
    running image's label matches what `recipe_hash` would return for
    the same input."""
    body = _render_dockerfile_body(data)
    config_hash = _compute_config_hash(data)
    label_line = f'\nLABEL alcatrazer.config_hash="{config_hash}"\n'
    # Insert the LABEL between the dev stage and the entrypoint tail
    # (which starts with "# Back to root for entrypoint"). This places
    # the LABEL inside stage 3 just before the USER root switch.
    entrypoint_marker = "# Back to root for entrypoint"
    insert_at = body.index(entrypoint_marker)
    return body[:insert_at] + label_line.lstrip("\n") + "\n" + body[insert_at:]


# --- Adapter -----------------------------------------------------------------


class DockerPrison(Alcatraz):
    """Docker-backed sandbox adapter."""

    def __init__(
        self,
        project_dir: Path,
        image_tag: str | None = None,
        container_name: str | None = None,
    ):
        super().__init__(project_dir)
        # Phase 1.2.5: defaults derive from a path-hash identity so two
        # alcatrazers on different repos get different image tags and
        # container names. Tests that want stable literals can still
        # pass explicit overrides.
        ident = _identity_for_project(project_dir)
        self.image_tag = image_tag if image_tag is not None else f"alcatraz-workspace:{ident}"
        self.container_name = container_name if container_name is not None else f"workspace-{ident}"

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

    def recipe_hash(self, coding_environment: dict) -> str:
        """Return the alcatrazer.config_hash that this adapter would bake
        into a freshly-built image for the given coding_environment.

        Phase 1.2.6: paired with `image_matches` so cmd_start can ask
        "is the running image built from the same recipe I'd build now?"
        without the caller knowing the underlying hash basis (Dockerfile
        body for DockerPrison; whatever a future backend's recipe is)."""
        return _compute_config_hash(coding_environment)

    def image_matches(self, expected_hash: str) -> bool:
        """True iff the running image carries an `alcatrazer.config_hash`
        LABEL equal to `expected_hash`.

        Returns False on missing image, missing label (older alcatrazer
        builds — triggers a one-time rebuild after upgrade), or any
        mismatch. The label is set by `_render_dockerfile` at build
        time and read here via `docker inspect --format`.
        """
        if not self.image_exists():
            return False
        result = subprocess.run(
            [
                "docker",
                "inspect",
                self.image_tag,
                "--format",
                '{{ index .Config.Labels "alcatrazer.config_hash" }}',
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        actual_hash = result.stdout.strip()
        return actual_hash == expected_hash

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

    def shell(self) -> None:
        """Open an interactive bash as agent inside the running container.

        Phase 1.2.5. Implementation uses ``os.execvp`` so the alcatrazer
        Python process is replaced by ``docker exec``; signals (Ctrl+C,
        Ctrl+D) flow through and the user's exit status is whatever bash
        exits with — same as if they'd typed the docker command directly.

        Raises ``PrisonStartError`` when not running. Never returns on
        success.
        """
        if not self.is_running():
            raise PrisonStartError(
                "Alcatraz is not running.",
            )
        os.execvp(
            "docker",
            [
                "docker",
                "exec",
                "-it",
                "-u",
                "agent",
                "-w",
                "/workspace",
                self.container_name,
                "bash",
            ],
        )
