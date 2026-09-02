"""The row shape the dependency mirror hands back.

The connection itself comes from the shared opener (`shared/sqlite_store.py`,
ADT #642); what stays here is the one thing this store does differently from
the others, rows as plain dicts, because every reader in the package spells a
column as ``row["OWNER"]`` and a query-mode result is serialised as is.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}
