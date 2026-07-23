from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adt_ai.shared.config import DEFAULT_PATH_OBJECTS
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.identity import load_identity, session_identifier
from adt_ai.shared.sql_like import matches_sql_like

if TYPE_CHECKING:
    from adt_ai.export_db.runner import ExportDbRequest

GatewayFactory = Callable[[str], QueryGateway]


@dataclass(frozen=True)
class AuditConfig:
    """A project's DDL-audit source for resolving who changed an object.

    Points export_db at a project-defined log table/view (columns for object name
    and the developer who changed it) so ``-by``/``-my`` can filter the export set
    without requiring DBA-level audit-trail access.
    """

    source: str
    object_name_column: str
    changed_by_column: str


def _with_default_layout(config: dict[str, Any]) -> dict[str, Any]:
    if "path_objects" in config:
        return config
    return {**config, "path_objects": DEFAULT_PATH_OBJECTS}

def _cached_gateway_factory(gateway_factory: GatewayFactory) -> GatewayFactory:
    gateways: dict[str, QueryGateway] = {}

    def for_schema(schema: str) -> QueryGateway:
        if schema not in gateways:
            gateways[schema] = gateway_factory(schema)
        return gateways[schema]

    return for_schema

def _configured_object_types(config: dict[str, Any]) -> list[str]:
    raw_types = config.get("object_types", {})
    if not isinstance(raw_types, dict):
        return []
    return [
        object_type
        for object_type in raw_types
        if object_type not in {"DATA", "GRANT"}
    ]

def _requested_object_type_matches(
    object_type: str,
    requested_types: list[str] | None,
) -> bool:
    if requested_types is None:
        return True
    return any(
        matches_sql_like(object_type, requested_type)
        for requested_type in requested_types
    )

def _has_runtime_filter(request: ExportDbRequest) -> bool:
    return any(
        (
            request.object_types is not None,
            request.names is not None,
            # `recent`, not `recent_days`: bare -recent narrows the selection to a
            # watermark cutoff and yields no day count, so testing the day count
            # would let a watermark run delete files for objects it never listed.
            request.recent is not None,
            request.authors is not None,
        )
    )

def _audit_config(config: dict[str, Any]) -> AuditConfig | None:
    raw = config.get("audit")
    if not isinstance(raw, dict):
        return None
    source = raw.get("source")
    object_name_column = raw.get("object_name") or raw.get("object_name_column")
    changed_by_column = raw.get("changed_by") or raw.get("changed_by_column")
    if not (source and object_name_column and changed_by_column):
        return None
    return AuditConfig(
        source             = str(source),
        object_name_column = str(object_name_column),
        changed_by_column  = str(changed_by_column),
    )


class AuthorFilterError(ValueError):
    """A ``-by``/``-my`` invocation that cannot be honored (no audit/identity)."""


def resolve_author_filter(
    by: str | None,
    my: bool,
    config: dict[str, Any],
    config_search_paths: Iterable[str],
) -> tuple[str | None, bool, list[str] | None]:
    """Resolve ``-by``/``-my`` into ``(changed_by, my_changes, authors)``.

    ``-my`` reads the current developer's db schema from the gitignored
    config/IDENTITY.yaml. Either flag needs the project's ``audit:`` source
    configured. Returns ``authors=None`` when no author filter was requested;
    raises ``AuthorFilterError`` on a bad request.
    """
    changed_by = by or None
    my_changes = bool(my)
    if not (my_changes or changed_by is not None):
        return changed_by, my_changes, None
    if _audit_config(config) is None:
        raise AuthorFilterError(
            "export_db: -by/-my needs an 'audit:' source "
            "(source/object_name/changed_by) configured in config.yaml."
        )
    authors: list[str] = []
    if my_changes:
        db_schema = session_identifier(load_identity(config_search_paths))
        if not db_schema:
            raise AuthorFilterError(
                "export_db: -my needs config/IDENTITY.yaml with a db_schema entry."
            )
        authors.append(db_schema)
    if changed_by is not None:
        authors.append(changed_by)
    return changed_by, my_changes, authors

def _split_patterns(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]
