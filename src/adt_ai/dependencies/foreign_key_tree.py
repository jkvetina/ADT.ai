"""Foreign-key cascade traversal for the dependency SQLite mirror."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any


def foreign_key_tree(
    connection: sqlite3.Connection,
    constraint_name: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return FK cascade rows around ``constraint_name`` sorted by traversal path."""
    target = _constraint_by_name(connection, constraint_name)
    if target is None:
        return {"references": [], "dependencies": []}

    references: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    dependencies: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    if target["CONSTRAINT_TYPE"] == "R":
        _collect_references(
            connection,
            target,
            (target["CONSTRAINT_NAME"],),
            set(),
            references,
        )
    else:
        for row in _table_foreign_keys(connection, target["OWNER"], target["TABLE_NAME"]):
            _collect_references(connection, row, (row["CONSTRAINT_NAME"],), set(), references)

    _collect_dependencies(
        connection,
        target["OWNER"],
        target["TABLE_NAME"],
        (),
        set(),
        dependencies,
    )

    return {
        "references": [row for _, row in sorted(references, key=lambda item: item[0])],
        "dependencies": [row for _, row in sorted(dependencies, key=lambda item: item[0])],
    }


def _constraint_by_name(
    connection: sqlite3.Connection,
    constraint_name: str,
) -> dict[str, Any] | None:
    return connection.execute(
        """
        SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME,
               R_OWNER, R_CONSTRAINT_NAME
        FROM USER_CONSTRAINTS
        WHERE UPPER(CONSTRAINT_NAME) = UPPER(?)
        ORDER BY OWNER, TABLE_NAME, CONSTRAINT_NAME
        LIMIT 1
        """,
        (constraint_name,),
    ).fetchone()


def _constraint_by_key(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> dict[str, Any] | None:
    return connection.execute(
        """
        SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME,
               R_OWNER, R_CONSTRAINT_NAME
        FROM USER_CONSTRAINTS
        WHERE OWNER = ?
          AND CONSTRAINT_NAME = ?
        """,
        (owner, constraint_name),
    ).fetchone()


def _table_foreign_keys(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME,
               R_OWNER, R_CONSTRAINT_NAME
        FROM USER_CONSTRAINTS
        WHERE OWNER = ?
          AND TABLE_NAME = ?
          AND CONSTRAINT_TYPE = 'R'
        ORDER BY CONSTRAINT_NAME
        """,
        (owner, table_name),
    ).fetchall()


def _table_key_constraints(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME,
               R_OWNER, R_CONSTRAINT_NAME
        FROM USER_CONSTRAINTS
        WHERE OWNER = ?
          AND TABLE_NAME = ?
          AND CONSTRAINT_TYPE IN ('P', 'U')
        ORDER BY CONSTRAINT_TYPE, CONSTRAINT_NAME
        """,
        (owner, table_name),
    ).fetchall()


def _referencing_foreign_keys(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME,
               R_OWNER, R_CONSTRAINT_NAME
        FROM USER_CONSTRAINTS
        WHERE R_OWNER = ?
          AND R_CONSTRAINT_NAME = ?
          AND CONSTRAINT_TYPE = 'R'
        ORDER BY TABLE_NAME, CONSTRAINT_NAME
        """,
        (owner, constraint_name),
    ).fetchall()


def _constraint_column_names(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> str:
    rows = connection.execute(
        """
        SELECT COLUMN_NAME
        FROM USER_CONS_COLUMNS
        WHERE OWNER = ?
          AND CONSTRAINT_NAME = ?
        ORDER BY POSITION
        """,
        (owner, constraint_name),
    ).fetchall()
    return ", ".join(row["COLUMN_NAME"] for row in rows)


def _constraint_tree_row(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "table_name": row["TABLE_NAME"],
        "column_name": _constraint_column_names(
            connection, row["OWNER"], row["CONSTRAINT_NAME"]
        ),
        "constraint_name": row["CONSTRAINT_NAME"],
        "type": row["CONSTRAINT_TYPE"],
    }


def _collect_references(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    path: tuple[str, ...],
    seen: set[tuple[Any, Any]],
    result: list[tuple[tuple[str, ...], dict[str, Any]]],
) -> None:
    key = (row["OWNER"], row["CONSTRAINT_NAME"])
    if key in seen:
        return
    seen.add(key)
    result.append((path, _constraint_tree_row(connection, row)))
    if row["CONSTRAINT_TYPE"] != "R" or not row["R_OWNER"] or not row["R_CONSTRAINT_NAME"]:
        return
    referenced = _constraint_by_key(connection, row["R_OWNER"], row["R_CONSTRAINT_NAME"])
    if referenced is None:
        return
    referenced_key = (referenced["OWNER"], referenced["CONSTRAINT_NAME"])
    if referenced_key in seen:
        return
    seen.add(referenced_key)
    referenced_path = path + (referenced["CONSTRAINT_NAME"],)
    result.append((referenced_path, _constraint_tree_row(connection, referenced)))
    for parent_fk in _table_foreign_keys(
        connection, referenced["OWNER"], referenced["TABLE_NAME"]
    ):
        _collect_references(
            connection,
            parent_fk,
            referenced_path + (parent_fk["CONSTRAINT_NAME"],),
            seen,
            result,
        )


def _collect_dependencies(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
    path: tuple[str, ...],
    seen: set[tuple[Any, Any]],
    result: list[tuple[tuple[str, ...], dict[str, Any]]],
) -> None:
    for key_constraint in _table_key_constraints(connection, owner, table_name):
        child_fks = _referencing_foreign_keys(
            connection, key_constraint["OWNER"], key_constraint["CONSTRAINT_NAME"]
        )
        if not child_fks:
            continue
        key = (key_constraint["OWNER"], key_constraint["CONSTRAINT_NAME"])
        if key in seen:
            continue
        seen.add(key)
        key_path = path + (key_constraint["CONSTRAINT_NAME"],)
        result.append((key_path, _constraint_tree_row(connection, key_constraint)))
        for child_fk in child_fks:
            key = (child_fk["OWNER"], child_fk["CONSTRAINT_NAME"])
            if key in seen:
                continue
            seen.add(key)
            child_path = key_path + (child_fk["CONSTRAINT_NAME"],)
            result.append((child_path, _constraint_tree_row(connection, child_fk)))
            _collect_dependencies(
                connection,
                child_fk["OWNER"],
                child_fk["TABLE_NAME"],
                child_path,
                seen,
                result,
            )
