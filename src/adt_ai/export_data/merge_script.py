"""The MERGE half of an `export_data` run: the CSV read back as replayable SQL.

Split out of `export_data/runner.py` by card `#695`, which is the card that took
that file past the 24 000 byte context guard: rendering a spatial column meant
teaching both halves a new type, and the project SOP's remedy for the guard is a
bounded module rather than a debt entry.

The seam is the one the module already had. Everything in `runner.py` is about
reaching the database and writing what came back; everything here starts from the
CSV that landed and asks a different question -- how does a person put these rows
into another environment. Nothing here touches a gateway, and the only reason the
column types travel this far is that a CSV cell is text and the destination
column's Oracle type is the only thing that can say what the text meant.

`from adt_ai.export_data.runner import _merge_sql_from_csv` still works and is the
spelling the tests use; `runner.py` re-exports it.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from adt_ai.export_data import queries
from adt_ai.export_data.csv_formulas import restored
from adt_ai.export_data.merge_config import merge_config
from adt_ai.shared.config import is_enabled


def merge_sql_from_csv(
    path: Path,
    table_name: str,
    primary_columns: list[str],
    config: dict[str, Any],
    where_filter: str,
    sql_table_name: str | None = None,
    null_safe_key: bool = False,
    column_types: dict[str, str] | None = None,
    identity_columns: set[str] | None = None,
) -> str:
    """`column_types` types the CSV cells; `identity_columns` are the unwritable ones.

    Both arrive from the table's own inventory (`#670`). Without them every cell
    is rendered as a text literal and no column is excluded, which is the shape
    a caller with no inventory in hand gets.
    """
    columns, batches = _csv_select_batches(path, config, column_types)
    if not columns:
        return ""
    # The MERGE/DELETE target carries the owner under `keep_owner`; every config
    # lookup below still keys off the unqualified `table_name`.
    table = (sql_table_name or table_name).lower()
    lower_columns = [column.lower() for column in columns]
    lower_primary = [column.lower() for column in primary_columns]
    # An ALWAYS identity column is exported and may be the key, but Oracle
    # refuses an INSERT that names it (ORA-32795) and an UPDATE that sets it.
    identity = identity_columns or set()
    update_columns = [
        column
        for column in lower_columns
        if column not in lower_primary and column not in identity
    ]
    insert_columns = [column for column in lower_columns if column not in identity]
    table_merge_config = merge_config(config, table_name)
    skip_delete = "" if is_enabled(table_merge_config.get("delete"), default=False) else "--"
    skip_insert = (
        ""
        if is_enabled(table_merge_config.get("insert"), default=True) and insert_columns
        else "--"
    )
    skip_update = (
        ""
        if is_enabled(table_merge_config.get("update"), default=True) and update_columns
        else "--"
    )
    primary_join = "\n    " + "\n    AND ".join(
        _join_predicate(column, null_safe_key)
        for column in lower_primary
    ) + "\n"
    updates = queries.update_assignments(update_columns, skip_update)
    statements = []
    for batch in batches:
        statements.append(
            queries.merge_statement(
                table          = table,
                columns        = lower_columns,
                csv_selects    = batch,
                primary_join   = primary_join,
                updates        = updates,
                skip_delete    = skip_delete,
                skip_insert    = skip_insert,
                skip_update    = skip_update,
                where_filter   = commented_where_filter(where_filter, skip_delete),
                insert_columns = insert_columns,
            )
        )
    return "".join(statements)


def _join_predicate(column: str, null_safe: bool) -> str:
    """One `ON` comparison, NULL-safe when the key came from the UNIQUE fallback.

    A UNIQUE constraint permits NULLs, and `t.c = s.c` is never TRUE for one, so
    every NULL-keyed row failed to match and was inserted again on each replay
    (`#670`). A primary key cannot be NULL, so it keeps the plain equality and
    the index path that comes with it.
    """
    if not null_safe:
        return f"t.{column} = s.{column}"
    return f"(t.{column} = s.{column} OR (t.{column} IS NULL AND s.{column} IS NULL))"


def _csv_select_batches(
    path: Path,
    config: dict[str, Any],
    column_types: dict[str, str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Read the CSV back as text and let the destination column type shape each literal.

    This read used to pass `csv.QUOTE_NONNUMERIC`, which casts every unquoted
    field through `float()` (`#670`): 9007199254740993 came back as
    9007199254740992.0 and an exported `1` was replayed as `1.0`. The column's
    Oracle type is already known here, so it decides numeric-versus-text rather
    than the parser guessing from the quoting.

    The formula guard is undone here rather than in `queries.row_select`, which
    the LOB update scripts also call with the driver's own rows: only a value
    that came out of a CSV can be carrying a prefix `#707` put there.
    """
    columns: list[str] = []
    batches: list[list[str]] = []
    delimiter = str(config.get("csv_delimiter") or ";")
    batch_size = int(config.get("merge_batch_size") or 10000)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(
            handle,
            delimiter      = delimiter,
            lineterminator = "\n",
        )
        for index, row in enumerate(reader):
            if not columns:
                columns = list(row)
            batch_index = index // batch_size
            if batch_index == len(batches):
                batches.append([])
            batches[batch_index].append(
                queries.row_select(_restored_row(row), columns, column_types)
            )
    return columns, batches


def _restored_row(row: dict[str, Any]) -> dict[str, Any]:
    """One CSV row with the `#707` formula prefix taken back off every text cell.

    `DictReader` hands back `None` for a column a short row never reached and a
    list under the rest key for a long one, so the type is checked rather than
    assumed; neither shape can carry a prefix this export wrote.
    """
    return {
        column: restored(value) if isinstance(value, str) else value
        for column, value in row.items()
    }


def commented_where_filter(where_filter: str, skip_delete: str) -> str:
    if not where_filter or not skip_delete:
        return where_filter
    return ("\n" + skip_delete).join(where_filter.splitlines())
