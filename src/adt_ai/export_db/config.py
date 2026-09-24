from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adt_ai.shared.config import DEFAULT_PATH_OBJECTS
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.identity import load_identity, session_identifier
from adt_ai.shared.sql_like import matches_sql_like, split_patterns

if TYPE_CHECKING:
    from adt_ai.export_db.runner import ExportDbRequest

GatewayFactory = Callable[[str], QueryGateway]


@dataclass(frozen=True)
class AuditConfig:
    """A project's DDL-audit source for resolving who changed an object.

    Points export_db at a project-defined log table/view (columns for object name
    and the developer who changed it) so ``-by``/``-my`` can filter the export set
    without requiring DBA-level audit-trail access.

    ``changed_at_column`` is optional and is what makes the filter time-aware: a
    log with no timestamp can only be asked *who ever touched this*, so without it
    ``-by``/``-my`` cannot tell your change from the one a colleague made after it.
    """

    source: str
    object_name_column: str
    changed_by_column: str
    changed_at_column: str | None = None


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

# What `file_empty_lines` means when a project does not set it: one blank line
# closing every exported file, which is the shape a slash-terminated object has
# always been written with, so an existing export does not move on the run that
# first reads the key (`#687`).
DEFAULT_EMPTY_LINES = 1

def _configured_empty_lines(config: dict[str, Any]) -> int:
    """How many blank lines close each file `export_db` writes.

    Unreadable input takes the default rather than raising: this decides the
    shape of a file, never whether the export can run, and a typo in one key is
    not a reason to refuse an export the connection is otherwise ready for. A
    negative count means the same thing as `0`, there being no such file.
    """
    configured = config.get("file_empty_lines")
    if configured is None or isinstance(configured, bool):
        return DEFAULT_EMPTY_LINES
    try:
        return max(0, int(str(configured).strip()))
    except (TypeError, ValueError):
        return DEFAULT_EMPTY_LINES

def _configured_object_types(config: dict[str, Any]) -> list[str]:
    raw_types = config.get("object_types", {})
    if not isinstance(raw_types, dict):
        return []
    return [
        object_type
        for object_type in raw_types
        if object_type not in {"DATA", "GRANT"}
    ]

def _exported_object_types(config: dict[str, Any]) -> list[str]:
    """Every ``object_types`` key, ``DATA`` and ``GRANT`` included.

    The map is what the file resolver reads to place an object, so this is the
    honest answer to *does export_db have somewhere to put this type*, and it is
    the one both ``-type`` refusals below are asking.

    Deliberately NOT ``_configured_object_types``, whose job is the discovery
    filter and which therefore drops the two pseudo-types: they have no
    ``user_objects`` row to discover, and they are still perfectly good ``-type``
    values, ``exports_grants`` reading `-type GRANT` to decide whether the four
    privilege reads run at all. Refusing them here made `-type GRANT` an error on
    the fix for `#739`, which is the defect this split repairs.
    """
    raw_types = config.get("object_types", {})
    if not isinstance(raw_types, dict):
        return []
    return list(raw_types)


class ObjectTypeFilterError(ValueError):
    """A ``-type`` selection reaching object types ``export_db`` cannot write."""


def unexported_requested_types(
    requested_types: list[str] | None,
    config: dict[str, Any],
) -> list[str]:
    """The ``-type`` patterns matching nothing the config says to export (`#739`).

    The runner takes ``request.object_types or _configured_object_types(config)``,
    so a requested type REPLACES the map instead of narrowing it, and a type the
    map does not carry reaches discovery, the overview and then a wall: the file
    resolver has no destination for it, or ``DBMS_METADATA`` refuses the type
    outright. Both arrive as a screen for a defect in ADT.

    Answered from the config alone, so the refusal lands before the run connects.
    ``-type`` is a LIKE pattern, hence the match rather than a set membership: a
    pattern covering a real type is a narrowing the export can honor, and one
    covering none of them is a request nothing could satisfy, whether it names a
    type ADT does not carry or is simply a typo.
    """
    if not requested_types:
        return []
    exported = _exported_object_types(config)
    return [
        requested
        for requested in requested_types
        if not any(
            matches_sql_like(object_type, requested) for object_type in exported
        )
    ]


def unexportable_object_types(
    discovered_types: Iterable[str],
    config: dict[str, Any],
) -> list[str]:
    """Discovered object types the config gives no destination, in listing order.

    The second route into the same wall, and the one no pre-check can close:
    ``-type %`` matches every exported type, so the request is honorable, and on
    a 26ai schema discovery still comes back holding a domain. What the database
    actually returned is the earliest this can be known, so the refusal waits for
    the overview rather than the command line.

    Reads the whole ``object_types`` map, ``DATA`` and ``GRANT`` included, for the
    reason ``_exported_object_types`` gives.
    """
    exported = {object_type.upper() for object_type in _exported_object_types(config)}
    unexportable: list[str] = []
    for object_type in discovered_types:
        if object_type.upper() in exported or object_type in unexportable:
            continue
        unexportable.append(object_type)
    return unexportable


def unexportable_object_types_message(object_types: Iterable[str]) -> str:
    """The one sentence both checks above refuse with.

    Names the types and says export_db does not carry them, so the reader can act
    on it, and points at the map rather than telling anyone to add a row to it:
    which of the 26ai types ADT should carry is the approval-gated question
    `#738` holds, and `DOMAIN` would not export from a new row anyway.

    A short uppercase headline, the types and the map under it (ADT #934).
    """
    return (
        "-type SELECTS TYPES export_db DOES NOT EXPORT\n\n"
        f"{', '.join(object_types)}: its exported types are the 'object_types' "
        "keys in config.yaml."
    )


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
    changed_at_column = raw.get("changed_at") or raw.get("changed_at_column")
    if not (source and object_name_column and changed_by_column):
        return None
    return AuditConfig(
        source             = str(source),
        object_name_column = str(object_name_column),
        changed_by_column  = str(changed_by_column),
        changed_at_column  = str(changed_at_column) if changed_at_column else None,
    )


class AuthorFilterError(ValueError):
    """A ``-by``/``-my`` invocation that cannot be honored (no audit/identity)."""


def resolve_author_filter(
    by: str | None,
    my: bool,
    config: dict[str, Any],
    config_search_paths: Iterable[str | Path],
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
            "-by/-my NEEDS AN 'audit:' SOURCE\n\n"
            "Configure one (source/object_name/changed_by) in config.yaml."
        )
    authors: list[str] = []
    if my_changes:
        db_schema = session_identifier(load_identity(config_search_paths))
        if not db_schema:
            raise AuthorFilterError(
                "-my NEEDS A db_schema IN config/IDENTITY.yaml"
            )
        authors.append(db_schema)
    if changed_by is not None:
        authors.append(changed_by)
    return changed_by, my_changes, authors

# One splitter with `export_data`, which read the same config key its own way
# (ADT #474). Re-exported under the old private name so no call site moved.
_split_patterns = split_patterns
