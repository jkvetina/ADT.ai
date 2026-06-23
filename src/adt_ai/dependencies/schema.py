"""Raw-mirror table definitions and DDL for the ``dependencies`` database.

The single source of truth for ``config/dependencies.db``: each Oracle
dictionary mirror as ``((column, sqlite_type)…, primary-key-columns)`` plus the
secondary lookup indexes used by the query layer. The ``CREATE TABLE`` DDL, the
per-table insert column lists, and the ``CREATE INDEX`` DDL are generated from
module constants so they cannot drift. Columns keep only dictionary fields
consumed by query modes or generated artifacts, plus a leading ``OWNER`` on the
``USER_*`` tables (the dictionary views have none — they scope implicitly to the
connected schema).
"""

from __future__ import annotations

# Each table as ((column, sqlite_type)…, primary-key-columns).
_TABLE_DEFS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    "USER_OBJECTS": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("OBJECT_NAME", "TEXT NOT NULL"),
            ("OBJECT_TYPE", "TEXT NOT NULL"),
            ("LAST_DDL_TIME", "TEXT"),
        ),
        ("OWNER", "OBJECT_TYPE", "OBJECT_NAME"),
    ),
    "USER_DEPENDENCIES": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("NAME", "TEXT NOT NULL"),
            ("TYPE", "TEXT NOT NULL"),
            ("REFERENCED_OWNER", "TEXT"),
            ("REFERENCED_NAME", "TEXT"),
            ("REFERENCED_TYPE", "TEXT"),
        ),
        ("OWNER", "TYPE", "NAME", "REFERENCED_OWNER", "REFERENCED_TYPE", "REFERENCED_NAME"),
    ),
    "USER_CONSTRAINTS": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("CONSTRAINT_NAME", "TEXT NOT NULL"),
            ("CONSTRAINT_TYPE", "TEXT"),
            ("TABLE_NAME", "TEXT NOT NULL"),
            ("R_OWNER", "TEXT"),
            ("R_CONSTRAINT_NAME", "TEXT"),
        ),
        ("OWNER", "CONSTRAINT_NAME"),
    ),
    "USER_CONS_COLUMNS": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("CONSTRAINT_NAME", "TEXT NOT NULL"),
            ("TABLE_NAME", "TEXT NOT NULL"),
            ("COLUMN_NAME", "TEXT NOT NULL"),
            ("POSITION", "INTEGER"),
        ),
        ("OWNER", "CONSTRAINT_NAME", "COLUMN_NAME"),
    ),
    "USER_IDENTIFIERS": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("OBJECT_NAME", "TEXT NOT NULL"),
            ("OBJECT_TYPE", "TEXT NOT NULL"),
            ("NAME", "TEXT"),
            ("TYPE", "TEXT"),
            ("USAGE", "TEXT"),
            ("USAGE_ID", "INTEGER"),
            ("USAGE_CONTEXT_ID", "INTEGER"),
        ),
        ("OWNER", "OBJECT_TYPE", "OBJECT_NAME", "USAGE_ID"),
    ),
    "USER_STATEMENTS": (
        (
            ("OWNER", "TEXT NOT NULL"),
            ("OBJECT_NAME", "TEXT NOT NULL"),
            ("OBJECT_TYPE", "TEXT NOT NULL"),
            ("TYPE", "TEXT"),
            ("USAGE_ID", "INTEGER"),
            ("USAGE_CONTEXT_ID", "INTEGER"),
        ),
        ("OWNER", "OBJECT_TYPE", "OBJECT_NAME", "USAGE_ID"),
    ),
    "APEX_USED_DB_OBJECTS": (
        (
            ("WORKSPACE", "TEXT"),
            ("APPLICATION_ID", "INTEGER NOT NULL"),
            ("APPLICATION_NAME", "TEXT"),
            ("USED_DB_OBJECT_ID", "INTEGER NOT NULL"),
            ("USED_DB_OBJECT_OWNER", "TEXT"),
            ("USED_DB_OBJECT_NAME", "TEXT"),
            ("USED_DB_OBJECT_TYPE", "TEXT"),
        ),
        ("APPLICATION_ID", "USED_DB_OBJECT_ID"),
    ),
    "APEX_USED_DB_OBJECT_COMP_PROPS": (
        (
            ("APPLICATION_ID", "INTEGER NOT NULL"),
            ("USED_DB_OBJECT_ID", "INTEGER NOT NULL"),
            ("USED_DB_OBJECT_NAME", "TEXT"),
            ("PAGE_ID", "INTEGER"),
            ("COMPONENT_ID", "INTEGER"),
            ("COMPONENT_NAME", "TEXT"),
            ("COMPONENT_TYPE", "TEXT"),
            ("PROPERTY_ID", "INTEGER"),
            ("PROPERTY_NAME", "TEXT"),
            ("PROPERTY_VALUE", "TEXT"),
        ),
        ("APPLICATION_ID", "USED_DB_OBJECT_ID", "COMPONENT_ID", "PROPERTY_ID"),
    ),
    "APEX_USED_DB_OBJ_DEPENDENCIES": (
        (
            ("APPLICATION_ID", "INTEGER NOT NULL"),
            ("USED_DB_OBJECT_ID", "INTEGER NOT NULL"),
            ("USED_DB_OBJECT_OWNER", "TEXT"),
            ("USED_DB_OBJECT_NAME", "TEXT"),
            ("USED_DB_OBJECT_TYPE", "TEXT"),
            ("REFERENCED_OBJECT_OWNER", "TEXT"),
            ("REFERENCED_OBJECT_NAME", "TEXT"),
            ("REFERENCED_OBJECT_TYPE", "TEXT"),
        ),
        (
            "APPLICATION_ID",
            "USED_DB_OBJECT_ID",
            "REFERENCED_OBJECT_OWNER",
            "REFERENCED_OBJECT_TYPE",
            "REFERENCED_OBJECT_NAME",
        ),
    ),
}

_INDEX_DEFS: dict[str, tuple[str, tuple[str, ...]]] = {
    "idx_user_dependencies_referenced": (
        "USER_DEPENDENCIES",
        ("REFERENCED_OWNER", "REFERENCED_TYPE", "REFERENCED_NAME"),
    ),
    "idx_user_dependencies_referenced_node": (
        "USER_DEPENDENCIES",
        ("REFERENCED_TYPE", "REFERENCED_NAME", "REFERENCED_OWNER"),
    ),
    "idx_user_dependencies_source": ("USER_DEPENDENCIES", ("TYPE", "NAME")),
    "idx_user_constraints_table": ("USER_CONSTRAINTS", ("TABLE_NAME", "OWNER")),
    "idx_user_constraints_referenced": (
        "USER_CONSTRAINTS",
        ("R_OWNER", "R_CONSTRAINT_NAME"),
    ),
    "idx_user_cons_columns_table": ("USER_CONS_COLUMNS", ("OWNER", "TABLE_NAME")),
    "idx_user_objects_type_owner": ("USER_OBJECTS", ("OBJECT_TYPE", "OWNER")),
    "idx_apex_used_db_objects_lookup": (
        "APEX_USED_DB_OBJECTS",
        ("USED_DB_OBJECT_OWNER", "USED_DB_OBJECT_NAME"),
    ),
    "idx_apex_used_db_object_comp_props_name": (
        "APEX_USED_DB_OBJECT_COMP_PROPS",
        ("USED_DB_OBJECT_NAME", "APPLICATION_ID"),
    ),
    "idx_apex_used_db_obj_dependencies_lookup": (
        "APEX_USED_DB_OBJ_DEPENDENCIES",
        ("USED_DB_OBJECT_OWNER", "USED_DB_OBJECT_NAME"),
    ),
}

# USER_* tables are refreshed per schema; APEX_* per app.
USER_TABLES: tuple[str, ...] = (
    "USER_OBJECTS",
    "USER_DEPENDENCIES",
    "USER_CONSTRAINTS",
    "USER_CONS_COLUMNS",
    "USER_IDENTIFIERS",
    "USER_STATEMENTS",
)
APEX_TABLES: tuple[str, ...] = (
    "APEX_USED_DB_OBJECTS",
    "APEX_USED_DB_OBJECT_COMP_PROPS",
    "APEX_USED_DB_OBJ_DEPENDENCIES",
)

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    table: tuple(name for name, _ in defs) for table, (defs, _pk) in _TABLE_DEFS.items()
}


def _ddl(table: str, defs: tuple[tuple[str, str], ...], pk: tuple[str, ...]) -> str:
    # Quote every identifier — some dictionary columns (DEFERRABLE, DEFERRED)
    # collide with SQLite reserved keywords.
    columns = ",\n    ".join(f'"{name}" {type_}' for name, type_ in defs)
    pk_cols = ", ".join(f'"{name}"' for name in pk)
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        f"    {columns},\n"
        f"    PRIMARY KEY ({pk_cols})\n"
        f");"
    )


def _index_ddl(name: str, table: str, columns: tuple[str, ...]) -> str:
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    return f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({quoted_columns});"


SCHEMA: str = "\n".join(
    [
        *(_ddl(table, defs, pk) for table, (defs, pk) in _TABLE_DEFS.items()),
        *(_index_ddl(name, table, columns) for name, (table, columns) in _INDEX_DEFS.items()),
    ]
)
