from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from adt_ai.dependencies.schema import TABLE_COLUMNS


def normalize(row: Mapping[str, Any]) -> dict[str, Any]:
    """Upper-case a raw dictionary row's keys so query aliasing cannot matter."""
    return {str(key).upper(): value for key, value in row.items()}


def insert_rows(
    connection: sqlite3.Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    stamp: Mapping[str, Any] | None = None,
) -> int:
    """Insert ``rows`` into ``table``; ``stamp`` overrides per-scope columns."""
    columns = TABLE_COLUMNS[table]
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{column}"' for column in columns)
    statement = f"INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({placeholders})"
    count = 0
    for row in rows:
        values = normalize(row)
        if stamp:
            values.update(stamp)
        connection.execute(statement, tuple(values.get(column) for column in columns))
        count += 1
    return count


def stamped_row(table: str, row: Mapping[str, Any], stamp: Mapping[str, Any]) -> dict[str, Any]:
    values = normalize(row)
    values.update(stamp)
    return {column: values.get(column) for column in TABLE_COLUMNS[table]}


def row_key(row: Mapping[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in columns)


def delete_where(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    key: tuple[Any, ...],
) -> None:
    predicate = " AND ".join(f'"{column}" = ?' for column in columns)
    connection.execute(f"DELETE FROM {table} WHERE {predicate}", key)
