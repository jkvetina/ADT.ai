"""Foreign-key cascade traversal for the dependency SQLite mirror."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from adt_ai.dependencies import queries


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
        queries.FK_CONSTRAINT_BY_NAME_QUERY,
        (constraint_name,),
    ).fetchone()


def _constraint_by_key(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> dict[str, Any] | None:
    return connection.execute(
        queries.FK_CONSTRAINT_BY_KEY_QUERY,
        (owner, constraint_name),
    ).fetchone()


def _table_foreign_keys(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        queries.FK_TABLE_FOREIGN_KEYS_QUERY,
        (owner, table_name),
    ).fetchall()


def _table_key_constraints(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        queries.FK_TABLE_KEY_CONSTRAINTS_QUERY,
        (owner, table_name),
    ).fetchall()


def _referencing_foreign_keys(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> list[dict[str, Any]]:
    return connection.execute(
        queries.FK_REFERENCING_FOREIGN_KEYS_QUERY,
        (owner, constraint_name),
    ).fetchall()


def _constraint_column_names(
    connection: sqlite3.Connection,
    owner: str,
    constraint_name: str,
) -> str:
    rows = connection.execute(
        queries.FK_CONSTRAINT_COLUMN_NAMES_QUERY,
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


def _iterate_frames(frame, *first_call) -> None:
    """Drive ``frame`` generators with an explicit stack.

    Depth-first with exact recursion semantics, each ``yield`` is a deferred
    recursive call, but immune to Python's recursion limit, which a deep
    (non-cyclic) FK chain in a large schema can exceed.
    """
    stack = [frame(*first_call)]
    while stack:
        try:
            call = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        stack.append(frame(*call))


def _collect_references(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    path: tuple[str, ...],
    seen: set[tuple[Any, Any]],
    result: list[tuple[tuple[str, ...], dict[str, Any]]],
) -> None:
    def frame(row: Mapping[str, Any], path: tuple[str, ...]):
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
            yield parent_fk, referenced_path + (parent_fk["CONSTRAINT_NAME"],)

    _iterate_frames(frame, row, path)


def _collect_dependencies(
    connection: sqlite3.Connection,
    owner: str,
    table_name: str,
    path: tuple[str, ...],
    seen: set[tuple[Any, Any]],
    result: list[tuple[tuple[str, ...], dict[str, Any]]],
) -> None:
    def frame(owner: str, table_name: str, path: tuple[str, ...]):
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
                yield child_fk["OWNER"], child_fk["TABLE_NAME"], child_path

    _iterate_frames(frame, owner, table_name, path)
