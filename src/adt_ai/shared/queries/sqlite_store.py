"""SQL every local SQLite store shares (`shared/sqlite_store.py`, ADT #642).

Three statements and one table. `_meta` is the same table in every file, so a
reader who has learned one store's version row has learned all five, and the
opener that stamps it is the one place the row is written.
"""

from __future__ import annotations

#: The version table, identical in every store. `value` is NOT NULL because a
#: version row with nothing in it answers no question; a store that carried a
#: nullable one (`apex.db`) rebuilt it on the way to this shape.
META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

FOREIGN_KEYS_ON = "PRAGMA foreign_keys = ON"

FOREIGN_KEYS_QUERY = "PRAGMA foreign_keys"

TABLE_EXISTS_QUERY = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?"

#: Every table the file holds, the automatic ones aside. Zero means a file the
#: opener is seeing for the first time, which has nothing to lift.
TABLE_COUNT_QUERY = (
    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
)

META_VERSION_QUERY = "SELECT value FROM _meta WHERE key = 'schema_version'"

META_VERSION_UPSERT = (
    "INSERT INTO _meta (key, value) VALUES ('schema_version', ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)

__all__ = [name for name in globals() if not name.startswith("_")]
