"""Resolve PL/Scope rows into column-level references and the committed
``columns.yaml`` document (Phase 1b).

``PLSCOPE_IDENTIFIERS_QUERY`` / ``PLSCOPE_STATEMENTS_QUERY`` (see
:mod:`adt_ai.dependencies.queries`) return flat rows sharing one
``usage_id`` / ``usage_context_id`` space per object. ``parse`` walks each
``COLUMN`` identifier's context chain upward to find its owning relation
(nearest ``TABLE`` / ``VIEW`` / ``MATERIALIZED VIEW`` ancestor) and its
enclosing SQL statement types. ``columns_doc`` groups the refs per unit and
derives view-column lineage from the uses-edges; ``column_rows`` is the
inverse, producing the flat ``view_name``/``column_name``/``src_table``/
``src_column`` rows that :func:`adt_ai.dependencies.store.build_db` loads
into the ``columns`` table. PL/Scope is opt-in: no rows simply means no refs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from adt_ai.dependencies.model import ColumnRef

_RELATION_TYPES = {"TABLE", "VIEW", "MATERIALIZED VIEW"}

_IDENTIFIER = "identifier"
_STATEMENT = "statement"


def parse(
    identifier_rows: Iterable[Mapping[str, Any]],
    statement_rows: Iterable[Mapping[str, Any]],
) -> list[ColumnRef]:
    """Parse PL/Scope rows into deduplicated, sorted :class:`ColumnRef`s.

    Duplicate ``(unit, relation, column)`` refs merge their operations; column
    usages with no relation ancestor in their context chain are skipped.
    """
    usage_maps: dict[str, dict[int, tuple[str, Mapping[str, Any]]]] = {}
    for kind, rows in ((_IDENTIFIER, identifier_rows), (_STATEMENT, statement_rows)):
        for row in rows:
            unit = f"{row['OBJECT_TYPE']}.{row['OBJECT_NAME']}"
            usage_maps.setdefault(unit, {})[int(row["USAGE_ID"])] = (kind, row)

    merged: dict[tuple[str, str, str], set[str]] = {}
    for unit, usage_map in usage_maps.items():
        for kind, row in usage_map.values():
            if kind != _IDENTIFIER or str(row["TYPE"]).upper() != "COLUMN":
                continue
            relation, operations = _walk_context(row, usage_map)
            if relation is None:
                continue
            key = (unit, relation, str(row["NAME"]))
            merged.setdefault(key, set()).update(operations)

    return [
        ColumnRef(unit, relation, column, tuple(sorted(operations)))
        for (unit, relation, column), operations in sorted(merged.items())
    ]


def _walk_context(
    row: Mapping[str, Any],
    usage_map: Mapping[int, tuple[str, Mapping[str, Any]]],
) -> tuple[str | None, set[str]]:
    """Walk a column usage's context chain to the root.

    Returns the owning relation node (nearest relation-typed ancestor, or
    ``None``) and every enclosing statement type, the statement usually sits
    above the relation, so the walk continues past the first match.
    """
    relation: str | None = None
    operations: set[str] = set()
    seen: set[int] = set()
    context = int(row.get("USAGE_CONTEXT_ID") or 0)
    while context and context not in seen:
        seen.add(context)
        entry = usage_map.get(context)
        if entry is None:
            break
        kind, parent = entry
        parent_type = str(parent["TYPE"]).upper()
        if kind == _STATEMENT:
            operations.add(parent_type)
        elif relation is None and parent_type in _RELATION_TYPES:
            relation = f"{parent_type}.{parent['NAME']}"
        context = int(parent.get("USAGE_CONTEXT_ID") or 0)
    return relation, operations


def columns_doc(
    refs: Iterable[ColumnRef],
    uses_edges: Mapping[str, list[str]] | None,
) -> dict[str, Any]:
    """Group column refs into the ``columns.yaml`` document.

    Output shape::

        refs:
          <UNIT>:
            <RELATION>:
              <COLUMN>: [SELECT, UPDATE, ...]
        views:
          <VIEW_NAME>:
            <COLUMN>: TABLE_NAME.COLUMN_NAME   # null when unresolved

    View lineage cross-references the view's uses-edges with the columns
    PL/Scope saw on each source: exactly one source exposing the column
    resolves it; zero or several leave it flagged ``None``, never dropped.
    """
    refs_section: dict[str, dict[str, dict[str, list[str]]]] = {}
    known_columns: dict[str, set[str]] = {}
    for ref in refs:
        unit = refs_section.setdefault(ref.unit, {})
        unit.setdefault(ref.relation, {})[ref.column] = list(ref.operations)
        known_columns.setdefault(ref.relation, set()).add(ref.column)

    views: dict[str, dict[str, str | None]] = {}
    for relation in sorted(known_columns):
        if not relation.startswith("VIEW."):
            continue
        view_name = relation.split(".", 1)[1]
        sources = (uses_edges or {}).get(relation, []) or []
        lineage: dict[str, str | None] = {}
        for column in sorted(known_columns[relation]):
            candidates = sorted(
                {source for source in sources if column in known_columns.get(source, ())}
            )
            if len(candidates) == 1:
                lineage[column] = f"{candidates[0].split('.', 1)[1]}.{column}"
            else:
                lineage[column] = None
        views[view_name] = lineage
    return {"refs": refs_section, "views": views}


def column_rows(doc: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten a ``columns.yaml`` document into derived SQLite rows.

    Each row is ``{view_name, column_name, src_table, src_column}``; unresolved
    lineage keeps the view column with ``None`` source fields. Sorted by view,
    then column.
    """
    rows: list[dict[str, Any]] = []
    views = (doc or {}).get("views", {}) or {}
    for view_name in sorted(views):
        lineage = views[view_name] or {}
        for column_name in sorted(lineage):
            source = lineage[column_name]
            src_table = src_column = None
            if source:
                src_table, _, src_column = str(source).partition(".")
            rows.append(
                {
                    "view_name": view_name,
                    "column_name": column_name,
                    "src_table": src_table or None,
                    "src_column": src_column or None,
                }
            )
    return rows
