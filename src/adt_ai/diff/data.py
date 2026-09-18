"""`diff -data`: the rows two schemas hold, matched on their keys (ADT #877, #883).

The object comparison answers whether two tables are DEFINED alike; nothing on
`diff` answered whether they HOLD the same rows, which is what reference data,
settings and lookup lists drift in between environments. Jan split the data
surface out of `#778` and chose the spelling himself, with chips, on
2026-09-18: `diff -data` rather than a mode on `export_data`, and database
against database. Comparing a database against the repository's CSV files is
a separate card.

**Rows match on a key, and only the data is compared, never the metadata**
(`#883`). Jan, on the first cut, which called a whole table `CHANGED` when a
column differed: *"We can have a table with same data, but different metadata
and then marking every row as changed is totally pointless."* So a table is
compared on the columns BOTH sides have, minus:

- identity columns, which each environment numbers for itself;
- `ignored_columns` from `config.yaml`, the setting `export_data` reads;
- the `-ignore` patterns of this run, for audit columns and the like.

The key is `export_data`'s own (`_key_columns`): the primary key, or a unique
key when the primary key is an identity, so the same row is found on both sides
however it was numbered. A table with neither is compared as a bag of rows.

**Each value is rendered the way `export_data` writes it**: `_csv_cell` for a
cell, and a digest of the sidecar file for a BLOB, CLOB, XMLTYPE or JSON value.
Two values compare equal exactly when the export would write the same bytes.

**`-limit N` stops a table after N differing rows** (`#883`, Jan's
*"shortloop"*). Both sides are then read in pages, in key order, and matched as
they arrive, so a large table that differs early costs a few pages rather than
two full reads. Character keys are ordered by `NLSSORT(..., BINARY)`, which is
code-point order for UTF-8 and so the order Python compares strings in; that is
what lets a row missing on one side be called missing before the other side is
read to its end.

**A row carries what differs, not just that it does** (`#886`). Jan: *"You
should list columns which dont match, ideally even data from both env."* A
`CHANGED` row holds each column whose values differ, with both sides' text; a
`MISSING` or `EXTRA` row holds every compared column of the side that has it.
A LOB is named by its type and the first characters of its digest, since the
digest is all the comparison ever read of it.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING, STATUS_ORDER
from adt_ai.export_data.inventory import DataColumn, DataDiscovery, DataTable
from adt_ai.export_data.runner import (
    _csv_cell,
    _ignored_columns,
    _key_columns,
    _where_filter,
)
from adt_ai.export_data.sidecars import _is_sidecar_column, _sidecar_payload
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value
from adt_ai.shared.sql_like import matches_sql_like, split_patterns

#: The object type a `-data` run is keyed under in the countdown history.
TABLE_DATA_TYPE = "TABLE DATA"

#: Rows per page when `-limit` reads a table in pieces. The driver's own
#: `FETCH_ARRAYSIZE`, so one page is one round trip.
PAGE_SIZE = 5000

#: The character types whose ORDER BY has to be pinned to binary order.
_CHARACTER_TYPES = frozenset({"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2"})


@dataclass(frozen=True)
class DataSide:
    """One side of the comparison: its gateway and its connection's `export:` block."""

    gateway : QueryGateway
    export  : Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValueDiff:
    """One compared column of a differing row; `None` is NULL, or the side that lacks the row."""

    column : str
    source : str | None
    target : str | None


@dataclass(frozen=True)
class RowDiff:
    key    : str
    status : str
    #: The key as `(column, value)` pairs, empty for a table with no key.
    cells  : tuple[tuple[str, str], ...] = ()
    #: `CHANGED`: the columns that differ. `MISSING`/`EXTRA`: every compared column.
    values : tuple[ValueDiff, ...] = ()


@dataclass(frozen=True)
class TableDiff:
    name        : str
    missing     : int = 0
    extra       : int = 0
    changed     : int = 0
    #: `-limit` stopped this table before it was read to the end, so the three
    #: counts are lower bounds.
    stopped     : bool = False
    rows        : tuple[RowDiff, ...] = ()
    #: The columns that find a row, empty when the table has no key.
    key_columns : tuple[str, ...] = ()

    @property
    def differs(self) -> bool:
        return bool(self.missing or self.extra or self.changed)


@dataclass(frozen=True)
class DataDiff:
    tables : tuple[TableDiff, ...] = ()

    @property
    def changed_tables(self) -> tuple[TableDiff, ...]:
        return tuple(table for table in self.tables if table.differs)

    @property
    def in_sync(self) -> bool:
        return not self.changed_tables

    @property
    def stopped(self) -> bool:
        return any(table.stopped for table in self.tables)

    @property
    def statuses(self) -> tuple[str, ...]:
        found = {row.status for table in self.tables for row in table.rows}
        return tuple(sorted(found, key=lambda status: STATUS_ORDER.get(status, 99)))


@dataclass(frozen=True)
class DataOptions:
    #: `-ignore`: column patterns, SQL LIKE, left out on both sides.
    ignore : tuple[str, ...] = ()
    #: `-limit`: stop a table after this many differing rows. `None` or 0 reads all.
    limit  : int | None = None


@dataclass(frozen=True)
class ColumnPlan:
    """Which columns find a row, and which are compared once it is found."""

    key    : tuple[DataColumn, ...]
    values : tuple[DataColumn, ...]

    @property
    def names(self) -> list[str]:
        return [column.name for column in (*self.key, *self.values)]


def plan_columns(
    source: Sequence[DataColumn] | None,
    target: Sequence[DataColumn] | None,
    config: Mapping[str, Any],
    ignore: Sequence[str] = (),
) -> ColumnPlan:
    """The columns both sides share, minus identity, config and `-ignore` columns.

    A table only one side has is planned from that side alone. The key is
    chosen before identity columns are dropped, so an identity primary key still
    finds rows when the table has nothing better, which is `export_data`'s rule.
    """
    base = list(source if source is not None else target or ())
    other = {column.name for column in (target if source is not None else source) or base}
    ignored = _ignored_columns(dict(config))
    patterns = [str(pattern).replace("*", "%").upper() for pattern in ignore]
    kept = [
        column for column in base
        if column.name in other
        and column.name not in ignored
        and not any(matches_sql_like(column.name.upper(), pattern) for pattern in patterns)
    ]
    identity = {
        column.name
        for column in (*(source or ()), *(target or ()))
        if column.identity.strip()
    }
    key_names = _key_columns(DataTable(schema="", name="", columns=list(base)),
                             [column.name for column in kept])
    by_name = {column.name: column for column in kept}
    key = tuple(by_name[name] for name in key_names)
    values = tuple(
        column for column in kept
        if column.name not in key_names and column.name not in identity
    )
    return ColumnPlan(key=key, values=values)


class DataDiffRunner:
    """Read both sides' tables and compare them row by row."""

    def __init__(self, page_size: int = PAGE_SIZE) -> None:
        self.page_size = page_size

    def run(
        self,
        source: DataSide,
        target: DataSide,
        config: Mapping[str, Any],
        *,
        names: Sequence[str],
        options: DataOptions | None = None,
    ) -> DataDiff:
        """Every selected table, both sides read at once, the way `-rest` reads its two.

        A value `export_data` has no text form for fails the whole run, as it
        fails the export: comparing without it would call a row equal on the
        strength of the columns that could be read.
        """
        options = options or DataOptions()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="adt-diff-data") as pool:
            try:
                source_tables, target_tables = _both(pool, _catalog, source, target, names)
                tables = tuple(
                    self._table(
                        pool, name, source, target, config, options,
                        source_tables.get(name), target_tables.get(name),
                    )
                    for name in sorted(set(source_tables) | set(target_tables))
                )
            except ValueError as error:
                raise RuntimeError(str(error)) from error
        return DataDiff(tables=tables)

    def _table(
        self,
        pool: ThreadPoolExecutor,
        name: str,
        source: DataSide,
        target: DataSide,
        config: Mapping[str, Any],
        options: DataOptions,
        source_columns: list[DataColumn] | None,
        target_columns: list[DataColumn] | None,
    ) -> TableDiff:
        plan = plan_columns(source_columns, target_columns, config, options.ignore)
        limit = options.limit or None
        sides = [
            _Reader(side, config, name, plan, self.page_size if limit and plan.key else None)
            if columns is not None else None
            for side, columns in ((source, source_columns), (target, target_columns))
        ]
        if limit and plan.key:
            return _merge(name, plan, sides[0], sides[1], limit)
        rows = _both(pool, lambda reader: list(reader.rows()) if reader else [], *sides)
        return _compare_all(name, plan, rows[0], rows[1], limit)


def _both(
    pool: ThreadPoolExecutor,
    work: Callable[..., Any],
    first: Any,
    second: Any,
    *args: Any,
) -> tuple[Any, Any]:
    """Run `work` for both sides at once and wait for both before reading either."""
    futures = [pool.submit(work, side, *args) for side in (first, second)]
    return futures[0].result(), futures[1].result()


def _catalog(side: DataSide, names: Sequence[str]) -> dict[str, list[DataColumn]]:
    discovery = DataDiscovery(side.gateway)
    return {
        table_name: discovery.columns(table_name)
        for table_name in discovery.table_names(
            names  = list(names),
            prefix = side.export.get("prefix"),
            ignore = split_patterns(side.export.get("ignore")),
        )
    }


#: One row as the comparison sees it: its order in key order, its key as
#: rendered cells, and its compared values as rendered cells.
_Row = tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]


class _Reader:
    """One side of one table, read whole or in key-ordered pages."""

    def __init__(
        self,
        side: DataSide,
        config: Mapping[str, Any],
        table: str,
        plan: ColumnPlan,
        page_size: int | None,
    ) -> None:
        self.discovery = DataDiscovery(side.gateway)
        self.plan = plan
        self.page_size = page_size
        columns = plan.names
        csv_names = [c.name for c in (*plan.key, *plan.values) if not _is_sidecar_column(c)]
        order_by = ", ".join(column.name for column in plan.key) or "ROWID"
        self.sql = self.discovery.data_query(
            table,
            columns,
            _where_filter(dict(config), table, csv_names),
            order_by,
            {column.name.upper(): column.data_type for column in (*plan.key, *plan.values)},
        )
        self.order_by = order_by

    def rows(self) -> Iterator[_Row]:
        if not self.page_size:
            yield from self._render(self.discovery.gateway.fetch_all(self.sql, exact_numbers=True))
            return
        offset = 0
        while True:
            page = self.discovery.gateway.fetch_all(self.page_sql(offset), exact_numbers=True)
            yield from self._render(page)
            if len(page) < self.page_size:
                return
            offset += self.page_size

    def page_sql(self, offset: int) -> str:
        """The same SELECT, in binary key order, one page of it."""
        order = ", ".join(
            (f"NLSSORT({column.name}, 'NLS_SORT=BINARY')"
             if column.data_type.upper() in _CHARACTER_TYPES else column.name)
            + " NULLS LAST"
            for column in self.plan.key
        )
        base = self.sql[: -len(self.order_by)]
        return f"{base}{order}\nOFFSET {offset} ROWS FETCH NEXT {self.page_size} ROWS ONLY"

    def _render(self, rows: Sequence[Mapping[str, Any]]) -> Iterator[_Row]:
        for row in rows:
            key = tuple(_cell(row, column) for column in self.plan.key)
            values = tuple(_cell(row, column) for column in self.plan.values)
            order = tuple(_order(row_value(dict(row), column.name)) for column in self.plan.key)
            yield order, key, values


def _cell(row: Mapping[str, Any], column: DataColumn) -> Any:
    value = row_value(dict(row), column.name)
    if _is_sidecar_column(column):
        payload = _sidecar_payload(value, column)
        if payload is None:
            return ""
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        return hashlib.sha256(data).hexdigest()
    return _csv_cell(value, column.name)


def _order(value: Any) -> tuple[bool, Any]:
    """A key value in the order `NULLS LAST` sorts it in."""
    return (value is None, 0 if value is None else value)


def _before(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    """`left` sorts strictly before `right`, or `False` when the two cannot be ordered."""
    try:
        return bool(left < right)
    except TypeError:
        return False


def _label(plan: ColumnPlan, key: tuple[Any, ...], values: tuple[Any, ...]) -> str:
    """How a row is named on screen: `COL=value`, or its cells when it has no key."""
    if plan.key:
        return ", ".join(
            f"{column.name}={_text(cell)}" for column, cell in zip(plan.key, key, strict=True)
        )
    return _line([_text(cell) for cell in values])


def _text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, Decimal):
        return format(cell, "f")
    return str(cell)


def _line(cells: Sequence[str]) -> str:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter=";", lineterminator="").writerow(cells)
    return buffer.getvalue()


def _compare_all(
    name: str,
    plan: ColumnPlan,
    source: list[_Row],
    target: list[_Row],
    limit: int | None,
) -> TableDiff:
    """Both sides read whole: every key matched, then listed in key order."""
    if plan.key:
        left = {key: (order, values) for order, key, values in source}
        right = {key: (order, values) for order, key, values in target}
    else:
        left = _bag(source)
        right = _bag(target)
    found: list[tuple[tuple[Any, ...], RowDiff]] = []
    for key in set(left) | set(right):
        mine, theirs = left.get(key), right.get(key)
        order = (mine or theirs or ((), ()))[0]
        if theirs is None:
            status = MISSING
        elif mine is None:
            status = EXTRA
        elif mine[1] != theirs[1]:
            status = CHANGED
        else:
            continue
        found.append((order, _row_diff(
            plan, key, status,
            mine[1] if mine else None,
            theirs[1] if theirs else None,
        )))
    found = _sorted(found)
    stopped = bool(limit) and len(found) > (limit or 0)
    return _table_diff(name, plan, [row for _, row in found][: limit or None], stopped)


def _bag(rows: list[_Row]) -> dict[tuple[Any, ...], tuple[tuple[Any, ...], tuple[Any, ...]]]:
    """Keyless rows, keyed by their own values; a repeated row keeps a counter."""
    bag: dict[tuple[Any, ...], tuple[tuple[Any, ...], tuple[Any, ...]]] = {}
    seen: dict[tuple[Any, ...], int] = {}
    for _, _, values in rows:
        count = seen.get(values, 0)
        seen[values] = count + 1
        bag[(*values, count)] = ((str(values), count), values)
    return bag


def _sorted(
    found: list[tuple[tuple[Any, ...], RowDiff]],
) -> list[tuple[tuple[Any, ...], RowDiff]]:
    try:
        return sorted(found, key=lambda item: item[0])
    except TypeError:
        return sorted(found, key=lambda item: item[1].key)


def _merge(
    name: str,
    plan: ColumnPlan,
    source: _Reader | None,
    target: _Reader | None,
    limit: int,
) -> TableDiff:
    """Both sides in key order, matched as they arrive, stopped at `limit` differences.

    A row still unmatched is called `MISSING` or `EXTRA` once the other side
    has read past its key, or has nothing left to read.
    """
    streams = [source.rows() if source else iter(()), target.rows() if target else iter(())]
    pending: list[dict[tuple[Any, ...], tuple[tuple[Any, ...], tuple[Any, ...]]]] = [{}, {}]
    last: list[tuple[Any, ...] | None] = [None, None]
    done = [False, False]
    found: list[RowDiff] = []
    while len(found) < limit and not all(done):
        for index in (0, 1):
            if done[index]:
                continue
            row = next(streams[index], None)
            if row is None:
                done[index] = True
                continue
            order, key, values = row
            last[index] = order
            other = pending[1 - index]
            if key in other:
                _, other_values = other.pop(key)
                if other_values != values:
                    pair = (values, other_values) if index == 0 else (other_values, values)
                    found.append(_row_diff(plan, key, CHANGED, *pair))
            else:
                pending[index][key] = (order, values)
        for index, status in ((0, MISSING), (1, EXTRA)):
            reached = last[1 - index]
            for key, (order, values) in list(pending[index].items()):
                if done[1 - index] or (reached is not None and _before(order, reached)):
                    del pending[index][key]
                    alone = (values, None) if index == 0 else (None, values)
                    found.append(_row_diff(plan, key, status, *alone))
    stopped = len(found) >= limit and not (all(done) and not any(pending))
    return _table_diff(name, plan, found[:limit], stopped)


def _row_diff(
    plan: ColumnPlan,
    key: tuple[Any, ...],
    status: str,
    source: tuple[Any, ...] | None,
    target: tuple[Any, ...] | None,
) -> RowDiff:
    """A differing row: its label, its key cells, and the values a reader needs."""
    present = source if source is not None else target or ()
    cells = tuple(
        (column.name, _text(cell)) for column, cell in zip(plan.key, key, strict=True)
    ) if plan.key else ()
    values = tuple(
        ValueDiff(
            column.name,
            None if source is None else _shown(column, source[index]),
            None if target is None else _shown(column, target[index]),
        )
        for index, column in enumerate(plan.values)
        if source is None or target is None or source[index] != target[index]
    )
    return RowDiff(_label(plan, key, present), status, cells, values)


def _shown(column: DataColumn, cell: Any) -> str | None:
    """A compared cell as text: `None` for NULL, and a LOB by its type and digest."""
    if cell is None or cell == "":
        return None
    if _is_sidecar_column(column):
        return f"({column.data_type.upper()} {str(cell)[:8]})"
    return _text(cell)


def _table_diff(name: str, plan: ColumnPlan, rows: list[RowDiff], stopped: bool) -> TableDiff:
    return TableDiff(
        name        = name,
        missing     = sum(row.status == MISSING for row in rows),
        extra       = sum(row.status == EXTRA for row in rows),
        changed     = sum(row.status == CHANGED for row in rows),
        stopped     = stopped,
        rows        = tuple(rows),
        key_columns = tuple(column.name for column in plan.key),
    )


__all__ = [name for name in globals() if not name.startswith("_")]
