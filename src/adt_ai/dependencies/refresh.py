"""Incremental refresh helpers for the raw dependency mirrors."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from adt_ai.dependencies import queries
from adt_ai.dependencies.schema import APEX_TABLES, USER_TABLES
from adt_ai.dependencies.write import delete_where, insert_rows, row_key, stamped_row

_USER_OBJECT_KEY = ("OBJECT_TYPE", "OBJECT_NAME")
_OBJECT_TABLE_KEY = {
    "USER_DEPENDENCIES": ("TYPE", "NAME"),
    "USER_CONSTRAINTS": ("TABLE", "TABLE_NAME"),
    "USER_CONS_COLUMNS": ("TABLE", "TABLE_NAME"),
    "USER_IDENTIFIERS": ("OBJECT_TYPE", "OBJECT_NAME"),
    "USER_STATEMENTS": ("OBJECT_TYPE", "OBJECT_NAME"),
}
_APEX_PRIMARY_KEYS = {
    "APEX_USED_DB_OBJECTS": ("APPLICATION_ID", "USED_DB_OBJECT_ID"),
    "APEX_USED_DB_OBJECT_COMP_PROPS": (
        "APPLICATION_ID",
        "USED_DB_OBJECT_ID",
        "COMPONENT_ID",
        "PROPERTY_ID",
    ),
    "APEX_USED_DB_OBJ_DEPENDENCIES": (
        "APPLICATION_ID",
        "USED_DB_OBJECT_ID",
        "REFERENCED_OBJECT_OWNER",
        "REFERENCED_OBJECT_TYPE",
        "REFERENCED_OBJECT_NAME",
    ),
}


def refresh_schema_full(
    connection: sqlite3.Connection,
    owner: str,
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, int]:
    """Replace one owner's ``USER_*`` rows in a single transaction."""
    return _replace_scope(
        connection,
        USER_TABLES,
        tables,
        delete_query = queries.delete_owner_rows_query,
        scope_value  = owner,
        stamp        = {"OWNER": owner},
    )


def refresh_app_full(
    connection: sqlite3.Connection,
    app_id: int,
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, int]:
    """Replace one app's ``APEX_*`` rows in a single transaction."""
    return _replace_scope(
        connection,
        APEX_TABLES,
        tables,
        delete_query = queries.delete_app_rows_query,
        scope_value  = app_id,
        stamp        = {"APPLICATION_ID": app_id},
    )


def _replace_scope(
    connection: sqlite3.Connection,
    mirror_tables: tuple[str, ...],
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None,
    *,
    delete_query: Any,
    scope_value: Any,
    stamp: Mapping[str, Any],
) -> dict[str, int]:
    """Wipe one scope's rows and write the supplied ones back, all or nothing.

    The schema and app writers differ only in which mirror tables they cover and
    which column names the scope, which is what the two callers above pass. They
    lived in `store.py` until ADT #413 and moved here so all four writers, full
    and incremental, are in one module rather than two.
    """
    provided = {str(key).upper(): value for key, value in (tables or {}).items()}
    counts: dict[str, int] = {}
    with connection:
        for table in mirror_tables:
            connection.execute(delete_query(table), (scope_value,))
            counts[table] = insert_rows(
                connection, table, provided.get(table, ()), stamp=dict(stamp)
            )
    return counts


def refresh_schema_incremental(
    connection: sqlite3.Connection,
    owner: str,
    object_rows: Iterable[Mapping[str, Any]],
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Update one owner's ``USER_*`` rows without wiping unchanged objects."""
    provided = {str(key).upper(): value for key, value in (tables or {}).items()}
    fresh_objects = [stamped_row("USER_OBJECTS", row, {"OWNER": owner}) for row in object_rows]
    fresh_by_key = {row_key(row, _USER_OBJECT_KEY): row for row in fresh_objects}
    if force:
        counts = {table: 0 for table in USER_TABLES}
        with connection:
            for table in USER_TABLES:
                connection.execute(queries.delete_owner_rows_query(table), (owner,))
            counts["USER_OBJECTS"] = insert_rows(connection, "USER_OBJECTS", fresh_objects)
            for table in USER_TABLES:
                if table == "USER_OBJECTS":
                    continue
                counts[table] = insert_rows(
                    connection, table, provided.get(table, ()), stamp={"OWNER": owner}
                )
            connection.execute(queries.DELETE_STALE_EXTERNAL_DEPS_QUERY, (owner,))
        return counts

    existing = connection.execute(queries.USER_OBJECTS_BY_OWNER_QUERY, (owner,)).fetchall()
    existing_by_key = {row_key(row, _USER_OBJECT_KEY): dict(row) for row in existing}

    changed = {
        key
        for key, row in fresh_by_key.items()
        if key not in existing_by_key
        or _last_ddl_time(row) != _last_ddl_time(existing_by_key[key])
    }
    stale = set(existing_by_key) - set(fresh_by_key)
    touched = changed | stale
    counts = {table: 0 for table in USER_TABLES}

    with connection:
        for object_type, object_name in touched:
            delete_where(
                connection,
                "USER_OBJECTS",
                ("OWNER", "OBJECT_TYPE", "OBJECT_NAME"),
                (owner, object_type, object_name),
            )
            delete_where(
                connection,
                "USER_DEPENDENCIES",
                ("OWNER", "TYPE", "NAME"),
                (owner, object_type, object_name),
            )
            if (object_type, object_name) in stale:
                delete_where(
                    connection,
                    "USER_DEPENDENCIES",
                    ("REFERENCED_OWNER", "REFERENCED_TYPE", "REFERENCED_NAME"),
                    (owner, object_type, object_name),
                )
            delete_where(
                connection,
                "USER_IDENTIFIERS",
                ("OWNER", "OBJECT_TYPE", "OBJECT_NAME"),
                (owner, object_type, object_name),
            )
            delete_where(
                connection,
                "USER_STATEMENTS",
                ("OWNER", "OBJECT_TYPE", "OBJECT_NAME"),
                (owner, object_type, object_name),
            )
            if object_type == "TABLE":
                _delete_table_constraint_scope(connection, owner, object_name)
        changed_objects = [row for key, row in fresh_by_key.items() if key in changed]
        counts["USER_OBJECTS"] = insert_rows(connection, "USER_OBJECTS", changed_objects)
        for table in USER_TABLES:
            if table == "USER_OBJECTS":
                continue
            changed_rows = _rows_for_changed_objects(
                table,
                provided.get(table, ()),
                changed,
            )
            counts[table] = insert_rows(
                connection, table, changed_rows, stamp={"OWNER": owner}
            )
        connection.execute(queries.DELETE_STALE_EXTERNAL_DEPS_QUERY, (owner,))
    return counts


def refresh_schema_deep(
    connection: sqlite3.Connection,
    owner: str,
    object_rows: Iterable[Mapping[str, Any]],
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    object_names: Iterable[str],
) -> dict[str, int]:
    """Replace rows for named objects plus rows pointing to those objects."""
    provided = {str(key).upper(): value for key, value in (tables or {}).items()}
    patterns = [name.strip().upper() for name in object_names if name and name.strip()]
    counts = {table: 0 for table in USER_TABLES}
    if not patterns:
        return counts

    fresh_objects = [stamped_row("USER_OBJECTS", row, {"OWNER": owner}) for row in object_rows]
    with connection:
        _delete_named_schema_scope(connection, owner, patterns)
        counts["USER_OBJECTS"] = insert_rows(connection, "USER_OBJECTS", fresh_objects)
        for table in USER_TABLES:
            if table == "USER_OBJECTS":
                continue
            counts[table] = insert_rows(
                connection, table, provided.get(table, ()), stamp={"OWNER": owner}
            )
    return counts


def schema_changed_objects(
    connection: sqlite3.Connection,
    owner: str,
    object_rows: Iterable[Mapping[str, Any]],
    *,
    force: bool = False,
) -> list[tuple[str, str]]:
    """Return added or modified ``USER_OBJECTS`` keys for ``owner``."""
    fresh_objects = [stamped_row("USER_OBJECTS", row, {"OWNER": owner}) for row in object_rows]
    fresh_by_key = {row_key(row, _USER_OBJECT_KEY): row for row in fresh_objects}
    if force:
        return [(object_type, object_name) for object_type, object_name in fresh_by_key]

    existing = connection.execute(queries.USER_OBJECTS_BY_OWNER_QUERY, (owner,)).fetchall()
    existing_by_key = {row_key(row, _USER_OBJECT_KEY): dict(row) for row in existing}
    return [
        (object_type, object_name)
        for object_type, object_name in fresh_by_key
        if (object_type, object_name) not in existing_by_key
        or _last_ddl_time(fresh_by_key[(object_type, object_name)])
        != _last_ddl_time(existing_by_key[(object_type, object_name)])
    ]


def schema_detail_rows_for_changed_objects(
    table: str,
    rows: Iterable[Mapping[str, Any]],
    changed: set[tuple[str, str]],
) -> list[Mapping[str, Any]]:
    """Return detail rows that will be reinserted for changed schema objects."""
    return _rows_for_changed_objects(table, rows, changed)


def _last_ddl_time(row: Mapping[str, Any]) -> Any:
    value = row.get("LAST_DDL_TIME")
    return None if value is None else str(value)


def _delete_named_schema_scope(
    connection: sqlite3.Connection,
    owner: str,
    patterns: list[str],
) -> None:
    _delete_like(connection, "USER_OBJECTS", "OBJECT_NAME", owner, patterns)
    connection.execute(
        queries.delete_named_dependency_scope_query(len(patterns)),
        (owner, *patterns, owner, *patterns),
    )
    _delete_like(connection, "USER_IDENTIFIERS", "OBJECT_NAME", owner, patterns)
    _delete_like(connection, "USER_STATEMENTS", "OBJECT_NAME", owner, patterns)
    _delete_matching_table_constraint_scope(connection, owner, patterns)


def _delete_like(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    owner: str,
    patterns: list[str],
) -> None:
    connection.execute(
        queries.delete_like_query(table, column, len(patterns)),
        (owner, *patterns),
    )


def _delete_matching_table_constraint_scope(
    connection: sqlite3.Connection,
    owner: str,
    patterns: list[str],
) -> None:
    rows = connection.execute(
        queries.matching_table_constraints_query(len(patterns)),
        (owner, *patterns),
    ).fetchall()
    _delete_constraint_scope(
        connection,
        owner,
        {row["CONSTRAINT_NAME"] for row in rows},
    )


def _delete_table_constraint_scope(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
) -> None:
    rows = connection.execute(
        queries.TABLE_CONSTRAINTS_QUERY,
        (owner, table_name),
    ).fetchall()
    _delete_constraint_scope(
        connection,
        owner,
        {row["CONSTRAINT_NAME"] for row in rows},
    )


def _delete_constraint_scope(
    connection: sqlite3.Connection,
    owner: str,
    constraint_names: set[str],
) -> None:
    if not constraint_names:
        return
    referenced = connection.execute(
        queries.referenced_constraints_query(len(constraint_names)),
        (owner, owner, *constraint_names),
    ).fetchall()
    all_names = constraint_names | {row["CONSTRAINT_NAME"] for row in referenced}
    for constraint_name in all_names:
        delete_where(
            connection,
            "USER_CONS_COLUMNS",
            ("OWNER", "CONSTRAINT_NAME"),
            (owner, constraint_name),
        )
        delete_where(
            connection,
            "USER_CONSTRAINTS",
            ("OWNER", "CONSTRAINT_NAME"),
            (owner, constraint_name),
        )


def _rows_for_changed_objects(
    table: str,
    rows: Iterable[Mapping[str, Any]],
    changed: set[tuple[str, str]],
) -> list[Mapping[str, Any]]:
    if not changed:
        return []
    key = _OBJECT_TABLE_KEY.get(table)
    if key is None:
        return list(rows)
    object_type, object_name = key
    return [
        row
        for row in rows
        if (_row_value_or_literal(row, object_type), str(row.get(object_name) or "")) in changed
    ]


def _row_value_or_literal(row: Mapping[str, Any], key: str) -> str:
    if key in row:
        return str(row.get(key) or "")
    return key


def refresh_app_incremental(
    connection: sqlite3.Connection,
    app_id: int,
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Update one app's ``APEX_*`` rows without wiping unchanged rows."""
    provided = {str(key).upper(): value for key, value in (tables or {}).items()}
    counts: dict[str, int] = {}
    with connection:
        for table in APEX_TABLES:
            pk = _APEX_PRIMARY_KEYS[table]
            fresh_rows = [
                stamped_row(table, row, {"APPLICATION_ID": app_id})
                for row in provided.get(table, ())
            ]
            if force:
                connection.execute(queries.delete_app_rows_query(table), (app_id,))
                counts[table] = insert_rows(connection, table, fresh_rows)
                continue

            fresh_by_key = {row_key(row, pk): row for row in fresh_rows}
            existing = connection.execute(
                queries.select_app_rows_query(table), (app_id,)
            ).fetchall()
            existing_by_key = {row_key(row, pk): dict(row) for row in existing}
            changed = {
                key
                for key, row in fresh_by_key.items()
                if key not in existing_by_key or row != existing_by_key[key]
            }
            stale = set(existing_by_key) - set(fresh_by_key)
            for key in changed | stale:
                delete_where(connection, table, pk, key)
            counts[table] = insert_rows(
                connection,
                table,
                (row for key, row in fresh_by_key.items() if key in changed),
            )
    return counts
