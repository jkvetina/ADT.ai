"""The `diff -data` result screen (ADT #883, #886).

Jan chose the shape with chips on 2026-09-18: without `-verbose`, one row per
table that differs, counting its `MISSING`, `EXTRA` and `CHANGED` rows; with
`-verbose`, every differing row. The statuses read as they do on the object
listing, one row at a time, and `LEGEND:` closes the screen the same way.

**`-verbose` prints one block per table, and a row names what differs**
(`#886`). Jan: *"I want a table per table + remove the table name column"*, the
row key becoming the key column itself, and *"You should list columns which
dont match, ideally even data from both env."* So each table gets its own
`CHANGED ROWS - <TABLE>:` block, one column per key column spelled as the
database spells it, and a `CHANGED` row takes one line per differing column with
both sides' values, its key printed once. A `MISSING` or `EXTRA` row is its key
alone: with sixty columns a table can hold, the screen cannot say which of them
a reader wanted. Every line fits the 80-column screen, the two value columns
sharing whatever the key, status and column name leave.

**`-out` writes the same report untrimmed, and only when asked** (`#886`, Jan:
*"I dont like the auto mode. It would create a pile of logs quickly."*). The
file carries every differing row whatever `-verbose` says, values untrimmed,
and a `MISSING` or `EXTRA` row there lists every column that holds a value.

The counts are plain numbers, right-aligned. `#883` marked a table `-limit`
stopped with `+` on each count and a legend line saying so; Jan removed both on
`#886`, since the reader who typed `-limit` knows the counts stop there.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from adt_ai.cli.constants import print_adt_header, print_adt_table
from adt_ai.cli.diff_reporters import (
    _LEGEND,
    COMPARING_HEADER,
    IN_SYNC_DATA,
    IN_SYNC_HEADER,
    LEGEND_HEADER,
    MAX_LINE,
    _print_fitted,
    _trim,
)
from adt_ai.diff.data import DataDiff, RowDiff, TableDiff, ValueDiff
from adt_ai.diff.inventory import CHANGED, MISSING
from adt_ai.shared import text_files
from adt_ai.shared.tables import adt_table_line_width, adt_table_lines

TABLES_HEADER = "CHANGED TABLES:"
ROWS_HEADER = "CHANGED ROWS - {table}:"

#: How a NULL value reads beside a value on the other side.
NULL_TEXT = "NULL"

#: A key column's row key, prefixed so a key column called `STATUS` cannot
#: collide with the status column; `labels` prints it without the prefix.
_KEY = "key:"

#: Below this a trimmed value or name stops being recognisable, so the value
#: columns are spent down to it before a key or column name gives anything.
_MIN_CELL = 12


def report_data_diff(
    result: DataDiff,
    *,
    verbose: bool = False,
    log: str | None = None,
    tail: Callable[[], object] | None = None,
) -> None:
    """Row counts per table, one block per table under `-verbose`, then the legend.

    `tail` is `-restore`'s section, run above the legend (`#893`).
    """
    if result.in_sync:
        print_adt_header(IN_SYNC_HEADER)
        print(f"  {IN_SYNC_DATA}")
        if log:
            print()
            print(f"  LOG: {log}")
        print()
        if tail is not None:
            tail()
        return
    print_adt_header(TABLES_HEADER)
    _print_fitted([_count_row(table) for table in result.changed_tables])
    if verbose:
        for table in result.changed_tables:
            rows = _table_rows(table, full=False)
            print_adt_header(ROWS_HEADER.format(table=table.name))
            labels = _labels(rows)
            fitted, labels = _fit(rows, labels)
            print_adt_table(fitted, labels=labels)
    # The file line sits under the last table, one blank below it, the way
    # `patch` prints its `LOG:` under the row it belongs to.
    if log:
        print(f"  LOG: {log}")
        print()
    elif not verbose:
        print()
    if tail is not None:
        tail()
    _report_legend(result)


def write_data_log(result: DataDiff, path: Path, comparing: str) -> None:
    """The report, untrimmed and with every differing row, written to `path`."""
    lines = [*_section(COMPARING_HEADER), f"  {comparing}", ""]
    if result.in_sync:
        lines += [*_section(IN_SYNC_HEADER), f"  {IN_SYNC_DATA}", ""]
    else:
        counts = [_count_row(table) for table in result.changed_tables]
        lines += [*_section(TABLES_HEADER), *adt_table_lines(counts), ""]
        for table in result.changed_tables:
            rows = _table_rows(table, full=True)
            lines += [
                *_section(ROWS_HEADER.format(table=table.name)),
                *adt_table_lines(rows, labels=_labels(rows)),
                "",
            ]
    path.parent.mkdir(parents=True, exist_ok=True)
    text_files.write_text(path, "\n".join(lines).rstrip() + "\n")


def _section(header: str) -> list[str]:
    return [header, "-" * len(header), ""]


def _count_row(table: TableDiff) -> dict[str, object]:
    return {
        "table_name" : table.name,
        "missing"    : table.missing,
        "extra"      : table.extra,
        "changed"    : table.changed,
    }


def _table_rows(table: TableDiff, *, full: bool) -> list[dict[str, object]]:
    """One table's block: a line per differing column, the key printed once.

    `full` is the file: a `MISSING` or `EXTRA` row there lists every column that
    holds a value. A table with no key names its rows by their cells, which are
    the whole row, so its rows carry nothing more.
    """
    shown = [(row, _shown_values(table, row, full=full)) for row in table.rows]
    with_values = any(values for _, values in shown)
    lines: list[dict[str, object]] = []
    for row, values in shown:
        head = _head(table, row)
        if not with_values:
            lines.append(head)
            continue
        for index, value in enumerate(values or (None,)):
            lead = head if index == 0 else dict.fromkeys(head, "")
            lines.append({**lead, **_value_cells(row, value)})
    return lines


def _head(table: TableDiff, row: RowDiff) -> dict[str, object]:
    key = {f"{_KEY}{name}": value for name, value in row.cells} if table.key_columns else {
        "row": row.key
    }
    return {**key, "status": row.status}


def _shown_values(table: TableDiff, row: RowDiff, *, full: bool) -> tuple[ValueDiff, ...]:
    if row.status == CHANGED:
        return row.values
    if not full or not table.key_columns:
        return ()
    side = "source" if row.status == MISSING else "target"
    return tuple(value for value in row.values if getattr(value, side) is not None)


def _value_cells(row: RowDiff, value: ValueDiff | None) -> dict[str, object]:
    if value is None:
        return {"column_name": "", "source": "", "target": ""}
    if row.status != CHANGED:
        return {
            "column_name" : value.column,
            "source"      : value.source or "",
            "target"      : value.target or "",
        }
    return {
        "column_name" : value.column,
        "source"      : NULL_TEXT if value.source is None else value.source,
        "target"      : NULL_TEXT if value.target is None else value.target,
    }


def _labels(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """A key column keeps the database's spelling, `DEPARTMENT_ID`."""
    return {column: column[len(_KEY):] for column in rows[0] if column.startswith(_KEY)}


def _fit(
    rows: list[dict[str, object]], labels: dict[str, str]
) -> tuple[list[dict[str, object]], dict[str, str]]:
    """The rows trimmed to the screen: the values give way first, then the names.

    Jan, `#886`: *"make sure you fit whole row into 80 chars (trim the values)"*.
    The value columns share what the key, status and column name leave, the wider
    one giving first. A key or column name is trimmed only once both values are at
    `_MIN_CELL`, and past that every column but the status gives, so the cap holds
    whatever the table's names are.
    """
    columns = list(rows[0])
    widths = {
        column: max(
            len(labels.get(column, column)),
            *(len("" if row.get(column) is None else str(row.get(column))) for row in rows),
        )
        for column in columns
    }
    values = [column for column in ("source", "target") if column in widths]
    names = [column for column in columns if column not in (*values, "status")]
    while adt_table_line_width(list(widths.values())) > MAX_LINE:
        column = _next_to_trim(widths, values, names)
        if column is None:
            break
        widths[column] -= 1
    fitted = [
        {column: _trim(row.get(column, ""), widths[column]) for column in columns} for row in rows
    ]
    return fitted, {column: str(_trim(label, widths[column])) for column, label in labels.items()}


def _next_to_trim(
    widths: Mapping[str, int], values: Sequence[str], names: Sequence[str]
) -> str | None:
    for candidates, floor in ((values, _MIN_CELL), (names, _MIN_CELL), ([*values, *names], 4)):
        available = [column for column in candidates if widths[column] > floor]
        if available:
            return max(available, key=lambda column: widths[column])
    return None


def _report_legend(result: DataDiff) -> None:
    print_adt_header(LEGEND_HEADER)
    width = max(len(status) for status in result.statuses)
    for status in result.statuses:
        print(f"  {status.ljust(width)}   {_LEGEND[status]}")
    print()


__all__ = [name for name in globals() if not name.startswith("_")]
