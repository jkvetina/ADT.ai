"""SQLite connection helpers for the dependency mirror."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def connect(db_path: str | Path) -> sqlite3.Connection:
    if isinstance(db_path, str) and db_path == ":memory:":
        connection = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path))
    connection.row_factory = dict_factory
    return connection
