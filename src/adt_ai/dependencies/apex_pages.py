from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from adt_ai.dependencies import queries
from adt_ai.shared.sql_like import matches_sql_like


def _page_clauses(
    column: str,
    explicit_ids: Sequence[int],
    ranges: Sequence[tuple[int, int | None]],
) -> tuple[str, list[Any]]:
    """`<column>` against `-page` ids and `MIN-MAX` / `MIN+` ranges, OR-joined.

    One spelling for the three readers below, which filter the same column the
    same way. An empty selection is an empty clause; each caller decides what
    that means for it.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if explicit_ids:
        clauses.append(f"{column} IN ({','.join('?' for _ in explicit_ids)})")
        params.extend(explicit_ids)
    for low, high in ranges:
        if high is None:
            clauses.append(f"{column} >= ?")
            params.append(low)
        else:
            clauses.append(f"({column} >= ? AND {column} <= ?)")
            params.extend((low, high))
    return " OR ".join(clauses), params


def apex_page_components(
    connection: sqlite3.Connection,
    app_id: int,
    explicit_ids: tuple[int, ...],
    ranges: tuple[tuple[int, int | None], ...],
) -> list[dict[str, Any]]:
    page_filter, page_params = _page_clauses("PAGE_ID", explicit_ids, ranges)
    if not page_filter:
        return []
    rows = connection.execute(
        queries.apex_page_components_query(page_filter),
        [app_id, *page_params],
    ).fetchall()
    return [
        {
            "component_name": row["component_name"],
            "component_type": row["component_type"],
            "page_id": row["page_id"],
        }
        for row in rows
    ]


def apex_page_db_objects(
    connection: sqlite3.Connection,
    app_id: int,
    explicit_ids: tuple[int, ...],
    ranges: tuple[tuple[int, int | None], ...],
) -> list[dict[str, Any]]:
    page_filter, page_params = _page_clauses("PAGE_ID", explicit_ids, ranges)
    if not page_filter:
        return []
    rows = connection.execute(
        queries.apex_page_db_objects_query(page_filter),
        [app_id, *page_params],
    ).fetchall()
    return [
        {
            "object_name": row["object_name"],
            "object_owner": row["object_owner"],
            "object_type": row["object_type"],
            "page_id": row["page_id"],
        }
        for row in rows
    ]


def apex_app_inventory(
    connection: sqlite3.Connection,
    app_ids: Iterable[int],
    *,
    page_ids: Sequence[int] = (),
    page_ranges: Sequence[tuple[int, int | None]] = (),
    types: Sequence[str] | None = None,
    names: Sequence[str] | None = None,
    owners: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Every DB object the applications reference, with the pages and components using it.

    The data behind `search -app` (`#30`). `PAGES` counts distinct pages and
    `COMPS` distinct components; a shared component sits on no page, so it adds
    to the second and not the first. A page selection keeps only the objects
    used on those pages and counts only them. `types` and `names` are SQL LIKE
    patterns through the one shared comparator, and `owners` an exact,
    case-insensitive list, the way every `-schema` reads.
    """
    apps = sorted(set(app_ids))
    page_filter, page_params = _page_clauses("p.PAGE_ID", page_ids, page_ranges)
    rows = connection.execute(
        queries.apex_app_inventory_query(len(apps), page_filter),
        [*apps, *page_params],
    ).fetchall()
    wanted_owners = {owner.upper() for owner in owners or ()}
    return [
        {
            "app_id": row["app_id"],
            "object_owner": row["object_owner"],
            "object_type": row["object_type"],
            "object_name": row["object_name"],
            "pages": row["pages"],
            "comps": row["comps"],
        }
        for row in rows
        if (not wanted_owners or row["object_owner"].upper() in wanted_owners)
        and _matches_any(row["object_type"], types)
        and _matches_any(row["object_name"], names)
    ]


def _matches_any(value: str, patterns: Sequence[str] | None) -> bool:
    return not patterns or any(matches_sql_like(value, pattern) for pattern in patterns)
