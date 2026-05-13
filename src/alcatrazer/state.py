"""`.alcatrazer/state.json` — the running state of Alcatrazer.

Records the workspace's pin + replay + paused state across the daemon
poll loop, the CLI commands, and shutdown. Fields the v0.1.1 schema
declares:

  - schema_version, daemon_shutdown      (carried over from v=1)
  - inner_root, pinned_branch            (stamped at first snapshot)
  - last_promoted, last_promotion_time   (advanced after each successful
                                          format-patch/am cycle)
  - paused                               (set on apply conflict; cleared
                                          on next successful cycle)

Lifecycle. The file is created by the snapshot step during the first
`alcatrazer start` — not by `alcatrazer init` alone, which only writes
`config.toml` + the workspace-dir pointer. Absence is a valid state:
the schema-compatibility gate (`require_compatible_workspace`) treats a
missing file as "fresh workspace, proceed". Once written, the file
persists across daemon cycles and CLI commands for the workspace's
lifetime; new fields merge in additively via `update_state`.

This module is the read/write API plus the Phase 7 compatibility gate.
It's deliberately the seed of the future infocenter layer (see
docs/features/refactor_for_infocenter.md); API surface is kept narrow
until enough composite-query callers exist to design the proper
abstraction.

Atomicity. `update_state` writes to a `.tmp` sibling and `os.replace`s
into the target name so concurrent readers (the daemon polling during a
CLI write) never observe a half-written file.
"""

import json
import os
import tomllib
from pathlib import Path

from alcatrazer import schema

# Derived from schemas.json (single source of truth — see
# docs/coding_conventions.md "Schema changes must land in
# schemas.json + CHANGELOG before release"). Adding a new entry to
# `state.history` in schemas.json IS the version bump; no drift.
SCHEMA_VERSION = schema.STATE.current_version

_STATE_FILE = "state.json"
_TMP_SUFFIX = ".tmp"


def load_state(alcatraz_dir: Path) -> dict:
    """Best-effort read of `<alcatraz_dir>/state.json`.

    Returns an empty dict when the file is missing, unreadable, corrupt,
    or not a JSON object at the top level. Callers consume fields via
    `.get(name)` without catching IO / JSON errors.
    """
    path = alcatraz_dir / _STATE_FILE
    try:
        content = path.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def update_state(alcatraz_dir: Path, **fields) -> None:
    """Merge `fields` into the current state and atomic-write.

    Always stamps `schema_version` so a future migration can detect
    older files. Reads the existing state first (best-effort) so
    unrelated fields survive unrelated updates.

    Atomic via write-to-tmp + `os.replace` — a crash between write and
    rename leaves the old file intact; readers never see a partially
    written state.json.
    """
    state = load_state(alcatraz_dir)
    state["schema_version"] = SCHEMA_VERSION
    state.update(fields)

    alcatraz_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = alcatraz_dir / (_STATE_FILE + _TMP_SUFFIX)
    target_path = alcatraz_dir / _STATE_FILE
    tmp_path.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp_path, target_path)


# --- Schema-compatibility gate -----------------------------------------------
#
# Phase 7: refuse pre-v0.1.1 workspaces with a transparent upgrade
# message rather than silently mis-reading their layout. See
# docs/features/change_promotion_machinery.md "Breaking-change posture".
#
# Two entry points:
#   - validate_schema_version(data) — pure validator on a loaded dict.
#     Used in isolation when callers already have a state dict (or in
#     tests that want to assert message content from synthetic data).
#   - require_compatible_workspace(alcatraz_dir) — workspace-level gate
#     that composes the dict validator with legacy-artifact signals.
#     This is what the refusal callers (daemon startup, cmd_start,
#     cmd_status, cmd_clear) actually invoke. Catches v0.1.0
#     workspaces that never wrote state.json (init+start without
#     stop/clear lazily-created the file only on shutdown).


class UnsupportedStateSchemaVersionError(Exception):
    """The workspace at `.alcatrazer/` was set up by an alcatrazer
    whose state layout this version can't read. Carries the upgrade
    instructions verbatim in `str(exc)`; raise-site composes the
    message via `_upgrade_message`."""


# Legacy v0.1.0 side files. Their presence in `.alcatrazer/` is an
# unambiguous fingerprint of a pre-v0.1.1 workspace — they were
# written by the old fast-export / fast-import promotion path that
# v0.1.1 retired (Phase 6) in favour of format-patch / am with
# state.json-resident bookkeeping.
_LEGACY_ARTIFACTS = (
    "paused-branches.json",
    "promoted-tips.json",
    "promote-export-marks",
    "promote-import-marks",
)

# Obsolete keys under [promotion-daemon] in .alcatrazer/config.toml.
# v0.1.0 declared these; v0.1.1 removes them because promotion is now
# always bound to the branch alcatrazer started from. Their presence
# in a config trips the gate even when schema_version is current-stamped
# (defence-in-depth against half-finished manual upgrades).
_LEGACY_CONFIG_KEYS = ("mode", "branches")

_CONFIG_FILE = "config.toml"


def _upgrade_message(
    schema_label: str,
    summary: str | None = None,
    legacy_artifact: str | None = None,
) -> str:
    """Compose the verbatim upgrade message displayed to the user.

    `schema_label` names what was refused (e.g. "schema 1" or a legacy
    artifact). `summary` is the prose from schemas.json that names the
    layout change, surfaced so the user understands what's different.
    `legacy_artifact`, when set, names the side file that tripped the
    gate (only set on artifact-based refusal).

    Step ordering matches docs/features/change_promotion_machinery.md
    "Breaking-change posture". Step 2's `sudo` is required because the
    inner workspace is owned by the container's agent user.
    """
    lines = [
        f"alcatrazer: this directory was set up by an older version ({schema_label}).",
        "v0.1.1 reworks promotion and is not backwards compatible.",
    ]
    if summary:
        lines += ["", f"What changed: {summary}"]
    if legacy_artifact:
        lines += ["", f"Detected legacy artifact: {legacy_artifact}"]
    lines += [
        "",
        "To upgrade:",
        "  1. If a daemon is running: alcatrazer stop      (using your previous version)",
        "  2. sudo rm -rf `cat .alcatrazer/workspace-dir`  (inner git repo for agents coding)",
        "  3. rm -rf .alcatrazer/                          "
        "(your coding-environment.toml is preserved)",
        "  4. alcatrazer init                              (using v0.1.1)",
        "  5. alcatrazer start",
        "",
        "See CHANGELOG for what changed and why.",
    ]
    return "\n".join(lines)


def validate_schema_version(data: dict) -> None:
    """Raise if `data` (a loaded state.json dict) is from an older
    alcatrazer.

    Silent on an empty dict — that means state.json is missing or
    unreadable (fresh workspace), which the workspace-level gate
    handles via separate legacy-artifact signals. Silent when
    `schema_version` matches `SCHEMA_VERSION`. Otherwise raises with
    the upgrade message, naming the offending version and pulling the
    layout-change summary from `schemas.json` when available.
    """
    if not data:
        return
    version = data.get("schema_version")
    if version == SCHEMA_VERSION:
        return
    label = f"schema {version}" if version is not None else "no schema_version field"
    rev = schema.STATE.revision(version) if isinstance(version, int) else None
    summary = rev.summary if rev else None
    raise UnsupportedStateSchemaVersionError(_upgrade_message(label, summary))


def _check_alcatrazer_config(alcatraz_dir: Path) -> None:
    """Refuse a `.alcatrazer/config.toml` whose layout predates v0.1.1.

    Three sub-signals, in order of "what trips first":

      1. Obsolete `[promotion-daemon].mode` or `.branches` key present
         (defence-in-depth — catches a hand-edited config that bumped
         schema_version but kept the deprecated keys).
      2. `schema_version` field absent (v0.1.0 had none).
      3. `schema_version != alcatrazer_config.current_version` —
         either too old, or a hand-crafted intermediate.

    Silent when the config is missing (mid-init), unreadable, or
    matches the current shape with no legacy keys.
    """
    config_path = alcatraz_dir / _CONFIG_FILE
    if not config_path.exists():
        return
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        # Best-effort: a corrupt config gets surfaced by the regular
        # config-loading path (daemon.load_config, etc.) with a more
        # specific error than this gate could give.
        return

    summary = schema.ALCATRAZER_CONFIG.revision(1)
    summary_text = summary.summary if summary else None

    promotion_daemon = config.get("promotion-daemon", {})
    for key in _LEGACY_CONFIG_KEYS:
        if key in promotion_daemon:
            raise UnsupportedStateSchemaVersionError(
                _upgrade_message(
                    f"obsolete [promotion-daemon].{key} in config.toml",
                    summary=summary_text,
                    legacy_artifact=f"config.toml: [promotion-daemon].{key}",
                )
            )

    current = schema.ALCATRAZER_CONFIG.current_version
    version = config.get("schema_version")
    if version == current:
        return
    if version is None:
        label = "config.toml has no schema_version field"
    else:
        label = f"config.toml schema {version}"
    raise UnsupportedStateSchemaVersionError(_upgrade_message(label, summary=summary_text))


def require_compatible_workspace(alcatraz_dir: Path) -> None:
    """Refuse a workspace that was set up by a pre-v0.1.1 alcatrazer.

    Called once at the entry of each refusal caller (daemon startup,
    cmd_start, cmd_status, cmd_clear) — before any state read happens,
    so a refusal short-circuits the rest of the operation.

    Four classes of signal, checked in order:
      1. `alcatraz_dir` doesn't exist → silent (pre-init).
      2. A legacy v0.1.0 side file is present in `alcatraz_dir` →
         raise, message names the artifact. Checked first because
         legacy artifacts are written every daemon cycle in v0.1.0
         (regardless of state.json), so they're the signal that
         catches workspaces v0.1.0 never wrote state.json for.
      3. `.alcatrazer/config.toml` carries legacy keys or an old
         `schema_version` → raise via `_check_alcatrazer_config`.
      4. state.json is present and stamped with `schema_version <
         SCHEMA_VERSION` → raise via `validate_schema_version`.

    Returns silently when none of the above fires — that's either a
    fresh workspace (no files yet) or a v0.1.1-current workspace.
    """
    if not alcatraz_dir.is_dir():
        return

    for name in _LEGACY_ARTIFACTS:
        if (alcatraz_dir / name).exists():
            v1 = schema.STATE.revision(1)
            raise UnsupportedStateSchemaVersionError(
                _upgrade_message(
                    "schema 1",
                    summary=v1.summary if v1 else None,
                    legacy_artifact=name,
                )
            )

    _check_alcatrazer_config(alcatraz_dir)

    validate_schema_version(load_state(alcatraz_dir))
