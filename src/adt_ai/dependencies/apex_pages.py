from __future__ import annotations

import sqlite3
from typing import Any

from adt_ai.dependencies import queries


def apex_page_components(
    connection: sqlite3.Connection,
    app_id: int,
    explicit_ids: tuple[int, ...],
    ranges: tuple[tuple[int, int | None], ...],
) -> list[dict[str, Any]]:
    page_clauses: list[str] = []
    params: list[Any] = [app_id]
    if explicit_ids:
        page_clauses.append(f"PAGE_ID IN ({','.join('?' for _ in explicit_ids)})")
        params.extend(explicit_ids)
    for low, high in ranges:
        if high is None:
            page_clauses.append("PAGE_ID >= ?")
            params.append(low)
        else:
            page_clauses.append("(PAGE_ID >= ? AND PAGE_ID <= ?)")
            params.extend((low, high))
    if not page_clauses:
        return []
    rows = connection.execute(
        queries.apex_page_components_query(" OR ".join(page_clauses)),
        params,
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
    page_clauses: list[str] = []
    params: list[Any] = [app_id]
    if explicit_ids:
        page_clauses.append(f"PAGE_ID IN ({','.join('?' for _ in explicit_ids)})")
        params.extend(explicit_ids)
    for low, high in ranges:
        if high is None:
            page_clauses.append("PAGE_ID >= ?")
            params.append(low)
        else:
            page_clauses.append("(PAGE_ID >= ? AND PAGE_ID <= ?)")
            params.extend((low, high))
    if not page_clauses:
        return []
    rows = connection.execute(
        queries.apex_page_db_objects_query(" OR ".join(page_clauses)),
        params,
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
