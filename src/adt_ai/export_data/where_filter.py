"""The `where` predicate an `export_data` table is read through.

Lifted out of `runner.py` when `#1099` pushed that module past the 24 KB
context guard (`tests/contracts/test_context_file_size.py`). `runner` re-exports
both names, so `diff -data` and every other importer is unchanged.

A config key names a table or a column the way a person types it, and the
dictionary holds the exact name (`#1111`). The exact spelling is tried first and
a case-insensitive match is only a fallback for the one name it can mean; when
it could mean two, such as `FOO` against `foo` and `"Foo"`, the run stops
instead of picking. The condition itself is the project's own SQL and is never
read or rewritten.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from adt_ai.shared.config import InvalidConfigValueError
from adt_ai.shared.sql_identifiers import is_identifier, sql_name


def _where_filter(config: dict[str, Any], table_name: str, columns: list[str]) -> str:
    sources: list[Any] = []
    tables_global = config.get("tables_global", {})
    if isinstance(tables_global, dict):
        sources.append(tables_global.get("where"))
    sources.append(_table_config(config, table_name).get("where"))

    # Resolved to a column before merging, so a table's condition replaces the
    # global one on the same column however either was spelled.
    resolved: dict[str, tuple[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for column, (key, condition) in _resolve_columns(source, columns, table_name).items():
            resolved.pop(column, None)
            resolved[column] = (key, condition)
    if not resolved:
        return ""
    lines = ["", "WHERE 1 = 1"]
    lines.extend(
        f"    AND {_rendered(column, key)} {condition}"
        for column, (key, condition) in resolved.items()
    )
    return "\n".join(lines)


def _resolve_columns(
    where: dict[Any, Any],
    columns: list[str],
    table_name: str,
) -> dict[str, tuple[str, Any]]:
    """`{column: (key as written, condition)}` for the keys that name an exported column."""
    found: dict[str, list[tuple[str, Any]]] = {}
    for raw_key, condition in where.items():
        key = str(raw_key)
        column = _the_one(key, columns, f"column of {table_name}")
        if column is not None:
            found.setdefault(column, []).append((key, condition))
    resolved: dict[str, tuple[str, Any]] = {}
    for column, entries in found.items():
        exact = [entry for entry in entries if entry[0] == column]
        if exact:
            resolved[column] = exact[0]
        elif len(entries) == 1:
            resolved[column] = entries[0]
        else:
            spellings = ", ".join(key for key, _ in entries)
            raise InvalidConfigValueError(
                f"AMBIGUOUS WHERE IN CONFIG\n\n{table_name}: {spellings} all name the "
                f"column {column}. Keep one."
            )
    return resolved


def _the_one(key: str, names: Iterable[str], what: str) -> str | None:
    """The name `key` means: itself when listed, else the only one it folds to."""
    names = list(names)
    if key in names:
        return key
    folded = [name for name in names if name.casefold() == key.casefold()]
    if len(folded) > 1:
        raise InvalidConfigValueError(
            f"AMBIGUOUS NAME IN CONFIG\n\n{key} names more than one {what}: "
            f"{', '.join(folded)}. Write the exact spelling."
        )
    return folded[0] if folded else None


def _rendered(column: str, key: str) -> str:
    """The column as SQL: quoted where Oracle needs it, else as the config wrote it.

    An ordinary name keeps its configured spelling, so the SELECT and the MERGE
    DELETE of a table that never needed quoting stay the bytes they were.
    """
    name = sql_name(column, role="where column")
    if not name.startswith('"') and is_identifier(key) and key.upper() == column:
        return key
    return name


def _table_config(config: dict[str, Any], table_name: str) -> dict[str, Any]:
    tables = config.get("tables", {})
    if not isinstance(tables, dict):
        return {}
    entries = {str(key): value for key, value in tables.items() if isinstance(value, dict)}
    key = _the_one(table_name, entries, "`tables` key")
    return entries[key] if key is not None else {}
