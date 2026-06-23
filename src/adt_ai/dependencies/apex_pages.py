from __future__ import annotations

import sqlite3
from typing import Any


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
        f"""
        SELECT MIN(PAGE_ID) AS page_id,
               COMPONENT_TYPE AS component_type,
               COMPONENT_NAME AS component_name
        FROM APEX_USED_DB_OBJECT_COMP_PROPS
        WHERE APPLICATION_ID = ?
          AND PAGE_ID IS NOT NULL
          AND COMPONENT_TYPE IS NOT NULL
          AND TRIM(COMPONENT_TYPE) <> ''
          AND COMPONENT_NAME IS NOT NULL
          AND TRIM(COMPONENT_NAME) <> ''
          AND ({" OR ".join(page_clauses)})
        GROUP BY COMPONENT_TYPE, COMPONENT_NAME
        ORDER BY MIN(PAGE_ID), UPPER(COMPONENT_TYPE), UPPER(COMPONENT_NAME)
        """,
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
