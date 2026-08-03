"""Graph edge builders over the raw mirror.

Both maps share one node vocabulary — ``TYPE.NAME`` — so a caller that needs the
whole ordering graph can merge them directly. They are separate because Oracle
stores the two halves in unrelated dictionary views: ``USER_DEPENDENCIES`` holds
PL/SQL and view dependencies and records nothing about tables, so foreign keys
have to be reconstructed from ``USER_CONSTRAINTS``.
"""

from __future__ import annotations

from typing import Any

from adt_ai.dependencies import queries


def uses_edges(connection: Any) -> dict[str, list[str]]:
    """Forward internal uses-map (``{node: [referenced…]}``) for lineage."""
    rows = connection.execute(queries.USES_EDGES_QUERY).fetchall()
    edges: dict[str, list[str]] = {}
    for row in rows:
        edges.setdefault(f"{row['t']}.{row['n']}", []).append(f"{row['rt']}.{row['rn']}")
    return edges


def foreign_key_edges(connection: Any) -> dict[str, list[str]]:
    """Forward FK map (``{TABLE.child: [TABLE.parent…]}``).

    The table-to-table half of the dependency graph, which ``uses_edges``
    structurally cannot see. A child table must be created after the table its
    foreign key references, so this is what makes a generated install script
    runnable rather than merely sorted.
    """
    rows = connection.execute(queries.FOREIGN_KEY_EDGES_QUERY).fetchall()
    edges: dict[str, list[str]] = {}
    for row in rows:
        edges.setdefault(f"TABLE.{row['child']}", []).append(f"TABLE.{row['parent']}")
    return edges
