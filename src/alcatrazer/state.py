"""Shared CLI ↔ daemon cooperation file backing `.alcatrazer/state.json`.

Scope today — one flag (`daemon_shutdown`) used by `alcatrazer stop` /
`clear` to tell the promotion daemon its shutdown was CLI-initiated.
The daemon reads it once during shutdown to pick a log prefix; behavior
doesn't branch on intent (final sync is always attempted, with eventual
consistency restoring anything the unexpected-shutdown case might miss
on next start). Only observability differs.

Scope tomorrow — this module is deliberately the seed of the future
infocenter layer (see docs/features/refactor_for_infocenter.md). The
file exists once initialized and persists across cycles; new fields
merge in additively without touching file lifecycle logic. API surface
is kept minimal (two functions) until real composite queries land and
we've seen enough callers to design the proper abstraction.

Atomicity: update_state writes to a `.tmp` sibling and `os.replace`s
into the target name so concurrent readers (the daemon polling during
a CLI write) never observe a half-written file.
"""

import json
import os
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
