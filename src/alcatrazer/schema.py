"""Read-side loader for Alcatrazer's schema history.

The source of truth lives at `src/alcatrazer/schemas.json` — a
language-neutral, machine-diffable record of every shape change ever
made to the three declared schemas:

  - `state`             → `.alcatrazer/state.json` (dynamic runtime state)
  - `alcatrazer_config` → `.alcatrazer/config.toml` (static tool config)
  - `coding_env`        → `coding-environment.toml` (user-facing,
                           version-controlled in the user's repo)

JSON keeps the contract usable by future non-Python clients (a rewrite
to another language, a hypothetical Alcatrazer HTTP/MCP API) and by
the pre-release CI diff-check planned in
`docs/coding_conventions.md` "Schema changes must land in
schemas.json + CHANGELOG before release". This module is one
consumer, not the source — keep it thin.

The version constants in `state.SCHEMA_VERSION` and
`start.CODING_ENV_SCHEMA_VERSION` derive from the last entry of each
schema's history, so adding a new revision entry to `schemas.json` IS
the version bump. No drift possible.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_SCHEMAS_PATH = Path(__file__).parent / "schemas.json"


@dataclass(frozen=True)
class SchemaRevision:
    """One entry in a schema's history.

    `release` is the release tag (e.g. `"v0.1.1"`) the revision shipped
    in. `summary` is the prose displayed verbatim in upgrade-refusal
    messages — write it for the end user, not the maintainer.
    """

    version: int
    release: str
    summary: str
    fields_added: tuple[str, ...]
    fields_removed: tuple[str, ...]
    fields_changed: tuple[str, ...]


@dataclass(frozen=True)
class Schema:
    """A named schema's metadata + revision history."""

    name: str
    file: str
    role: str
    history: tuple[SchemaRevision, ...]

    @property
    def current_version(self) -> int:
        """Highest declared version — what this alcatrazer writes."""
        return self.history[-1].version

    def revision(self, version: int) -> SchemaRevision | None:
        for r in self.history:
            if r.version == version:
                return r
        return None


def _to_revision(entry: dict) -> SchemaRevision:
    return SchemaRevision(
        version=entry["version"],
        release=entry["release"],
        summary=entry["summary"],
        fields_added=tuple(entry.get("fields_added", ())),
        fields_removed=tuple(entry.get("fields_removed", ())),
        fields_changed=tuple(entry.get("fields_changed", ())),
    )


def _to_schema(name: str, entry: dict) -> Schema:
    return Schema(
        name=name,
        file=entry["file"],
        role=entry["role"],
        history=tuple(_to_revision(r) for r in entry["history"]),
    )


def _load() -> dict[str, Schema]:
    raw = json.loads(_SCHEMAS_PATH.read_text())
    return {name: _to_schema(name, entry) for name, entry in raw["schemas"].items()}


SCHEMAS: dict[str, Schema] = _load()

STATE = SCHEMAS["state"]
ALCATRAZER_CONFIG = SCHEMAS["alcatrazer_config"]
CODING_ENV = SCHEMAS["coding_env"]
