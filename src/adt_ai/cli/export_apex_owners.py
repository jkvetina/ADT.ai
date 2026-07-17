from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from adt_ai.cli.constants import (
    ApexDiscovery,
    ConnectionConfigError,
    ConnectionResult,
)
from adt_ai.shared.yaml_io import load_yaml_mapping


def _apex_reveal_connection_schema(
    connections: ConnectionResult,
    environment: str,
    schemas: list[str],
) -> str:
    try:
        default_schemas = connections.default_schemas(environment, kind="apex")
    except ConnectionConfigError:
        default_schemas = []
    return default_schemas[0] if default_schemas else schemas[0]


def _apex_app_id_value(app_id: str | int) -> str | int:
    try:
        return int(app_id)
    except (TypeError, ValueError):
        return app_id


def _resolve_apex_metadata_owners(
    root: Path,
    app_ids: list[str],
    default_schema: str,
    schema_names: list[str],
) -> dict[str, list[str]]:
    """Map requested app ids to a recorded *non-default* owner schema.

    Reads the cached ``config/apex_apps.yaml`` (written by
    ``export_apex/metadata._store_application_metadata``, keyed by ``app_id``
    with an ``owner`` field) so the command can connect straight to an app's
    owner schema and skip the wasted default-schema connection + live
    owner-discovery round-trip. Apps absent from the file, recorded against the
    default schema, or whose owner is not a configured schema are left out so the
    caller falls back to the previous discover-then-resolve behavior. Returns an
    empty mapping when there are no app ids or the file is missing.
    """
    if not app_ids:
        return {}
    app_metadata = load_yaml_mapping(root / "config" / "apex_apps.yaml")
    if not app_metadata:
        return {}
    schema_lookup = {name.upper(): name for name in schema_names}
    routes: dict[str, list[str]] = {}
    for app_id in app_ids:
        entry = app_metadata.get(_apex_app_id_value(app_id))
        if not isinstance(entry, Mapping):
            continue
        owner = str(entry.get("owner") or "")
        owner_schema = schema_lookup.get(owner.upper())
        if owner_schema is None or owner_schema == default_schema:
            continue
        routes.setdefault(owner_schema, []).append(app_id)
    return routes


def _resolve_apex_app_owners(
    discovery: ApexDiscovery,
    missing_app_ids: list[str],
    schema_names: list[str],
) -> tuple[dict[str, list[str]], list[tuple[str, str]], list[str]]:
    schema_lookup = {name.upper(): name for name in schema_names}
    owner_to_app_ids: dict[str, list[str]] = {}
    not_configured: list[tuple[str, str]] = []
    not_found: list[str] = []
    for app_id in missing_app_ids:
        owner = discovery.application_owner(_apex_app_id_value(app_id))
        if not owner:
            not_found.append(app_id)
            continue
        owner_schema = schema_lookup.get(owner.upper())
        if owner_schema is None:
            not_configured.append((app_id, owner))
            continue
        owner_to_app_ids.setdefault(owner_schema, []).append(app_id)
    return owner_to_app_ids, not_configured, not_found


__all__ = [name for name in globals() if not name.startswith("__")]
