"""`search TERM -data`: the rows holding a text or a number, live (ADT #879, #920).

Jan, 2026-09-22, choosing the database over the `export_data` CSVs: *"when I
have to search, I have no idea where it is; csv data are usually known enums"*.
So every table, view, materialized view and synonym of the schema is asked,
one query per object, and only the rows that hold TERM come back.

**What a hit is.** A text column holds TERM as a case-insensitive substring,
the fold done by Oracle on both sides so it agrees with the database's own
`UPPER`. A TERM that reads as a number also matches a NUMBER column by
equality, so `4711` finds the order keyed 4711 as well as the note that
mentions it. Every other column type is not searched.

**Which row.** A hit names its row by the primary key, the key columns joined
in key order, or by its ROWID when there is none. A view has no ROWID, so it
names the row by its first column that sorts. Each query stops
after `limit` rows, the matching ones in key order, so one wide table cannot
bury the rest of the answer.

Nothing here prints except through the `reporter`, which opens a table's row
before its queries run and closes it with the hit count after, or as failed
when the database refuses it, so the screen always names the table being read.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from adt_ai.export_data.inventory import DataColumn
from adt_ai.search.queries.data import COLUMNS_QUERY, OBJECTS_QUERY
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value

#: Every object whose rows a `SELECT` reads, in the order the screen lists them.
#: Jan, 2026-09-22, after `SEARCHING 0 TABLES:` on a schema whose data sat
#: behind a synonym: *"TABLES + VIEWS + MVIEWS + SYNONYMS TO OTHER TABLES +
#: VIEWS + MVIEWS !"*, one list per object type.
KINDS = ("TABLE", "VIEW", "MATERIALIZED VIEW", "SYNONYM")
#: The heading word of each kind's list.
KIND_LABELS = {
    "TABLE"             : "TABLES",
    "VIEW"              : "VIEWS",
    "MATERIALIZED VIEW" : "MVIEWS",
    "SYNONYM"           : "SYNONYMS",
}

@dataclass(frozen=True)
class DataObject:
    """One object a search reads: its name in the schema and whose rows it reads."""

    kind        : str
    name        : str
    owner       : str
    target      : str
    target_kind : str

    @property
    def title(self) -> str:
        """The name its hits print under; a synonym names what it points at."""
        if self.kind == "SYNONYM":
            return f"{self.name} -> {self.owner}.{self.target}"
        return self.name

    @property
    def has_rowid(self) -> bool:
        """A view has no ROWID of its own to name a row by (`ORA-01445`)."""
        return self.target_kind != "VIEW"


class SearchDiscovery:
    """The objects of the login schema a search reads, and each one's columns."""

    OBJECTS_QUERY = OBJECTS_QUERY
    COLUMNS_QUERY = COLUMNS_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

    def objects(
        self, names: list[str] | None = None, ignore: list[str] | None = None
    ) -> list[DataObject]:
        rows = self.gateway.fetch_all(
            self.OBJECTS_QUERY,
            {
                "object_name"    : ",".join(names or ["%"]).upper(),
                "objects_ignore" : ",".join(ignore or []),
            },
        )
        return [
            DataObject(
                kind        = str(row_value(row, "OBJECT_TYPE")),
                name        = str(row_value(row, "OBJECT_NAME")),
                owner       = str(row_value(row, "TARGET_OWNER")),
                target      = str(row_value(row, "TARGET_NAME")),
                target_kind = str(row_value(row, "TARGET_TYPE")),
            )
            for row in rows
        ]

    def columns(self, item: DataObject) -> list[DataColumn]:
        rows = self.gateway.fetch_all(
            self.COLUMNS_QUERY, {"owner": item.owner, "table_name": item.target}
        )
        return [
            DataColumn(
                name      = str(row_value(row, "COLUMN_NAME")),
                data_type = str(row_value(row, "DATA_TYPE")),
                pk        = None if row_value(row, "PK") is None else int(row_value(row, "PK")),
            )
            for row in rows
        ]


#: Columns a text TERM is looked for in. `LONG` is left out: Oracle refuses it
#: inside `INSTR` and `UPPER`, and nothing written since 8i uses one.
TEXT_TYPES = frozenset({"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"})
LOB_TYPES = frozenset({"CLOB", "NCLOB"})
#: What `user_tab_cols` reports for a numeric column; `INTEGER` and `DECIMAL`
#: are stored as `NUMBER` and never appear under their own names.
NUMBER_TYPES = frozenset({"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"})

#: How much of a LOB comes back around the match: enough for the value line to
#: show TERM in context, never the whole document.
LOB_WINDOW = 400
LOB_LEAD = 100

_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")


@dataclass(frozen=True)
class DataHit:
    """One `ROW | COLUMN | VALUE` line under its table."""

    table: str
    row: str
    column: str
    value: str


@dataclass(frozen=True)
class DataFailure:
    """An object the database refused to read, and what it said."""

    table: str
    error: Exception


class DataReporter(Protocol):
    def begin(self, table: str) -> None: ...

    def finish(self, table: str, hits: int) -> None: ...

    def fail(self, table: str) -> None: ...


def numeric_term(term: str) -> Decimal | None:
    """TERM as a number when it reads as one, `None` otherwise."""
    text = term.strip()
    if not _NUMBER.fullmatch(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - the pattern admits only digits
        return None


def _quoted(name: str) -> str:
    """A dictionary name as a quoted identifier, so a mixed-case name still reads."""
    if '"' in name or not name:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


@dataclass(frozen=True)
class DataQuery:
    """One table's query, the columns it searches in select order, and its binds."""

    sql: str
    keys: tuple[str, ...]
    columns: tuple[str, ...]
    params: dict[str, Any]


def data_query(
    table: str,
    columns: Sequence[DataColumn],
    term: str,
    limit: int | None,
    owner: str = "",
    rowid: bool = True,
) -> DataQuery | None:
    """The query finding TERM in `table`, `None` when no column can hold it.

    Each searched column comes back as its value when that column matched and
    as NULL when it did not, so which column of a row matched is the database's
    answer rather than a second guess in Python with a different case fold.

    `owner` qualifies the table, for a synonym's target in another schema.
    Without `rowid`, a view, a row with no key is named by the view's first
    column that sorts, and by its position when no column does.
    """
    number = numeric_term(term)
    keys = [column.name for column in sorted(
        (column for column in columns if column.pk), key=lambda column: column.pk or 0
    )]
    if not keys and not rowid:
        keys = [column.name for column in columns if _sortable(column)][:1]
    tests: list[tuple[str, str, str]] = []
    for column in columns:
        name = _quoted(column.name)
        kind = column.data_type.strip().upper()
        if kind in TEXT_TYPES:
            tests.append((column.name, f"INSTR(UPPER({name}), UPPER(:term)) > 0", name))
        elif kind in LOB_TYPES:
            at = f"DBMS_LOB.INSTR(UPPER({name}), UPPER(:term))"
            tests.append((
                column.name,
                f"{at} > 0",
                f"DBMS_LOB.SUBSTR({name}, {LOB_WINDOW}, GREATEST(1, {at} - {LOB_LEAD}))",
            ))
        elif kind in NUMBER_TYPES and number is not None:
            tests.append((column.name, f"{name} = :num", f"TO_CHAR({name})"))
    if not tests:
        return None
    fallback = "ROWID" if rowid else "ROWNUM"
    key_items = (
        [f"{_quoted(key)} AS ADT_K{index}" for index, key in enumerate(keys, start=1)]
        if keys
        else ["ROWIDTOCHAR(ROWID) AS ADT_K1" if rowid else "ROWNUM AS ADT_K1"]
    )
    value_items = [
        f"CASE WHEN {test} THEN {value} END AS ADT_V{index}"
        for index, (_column, test, value) in enumerate(tests, start=1)
    ]
    order = ", ".join(_quoted(key) for key in keys) if keys else ("ROWID" if rowid else "")
    fetch = f"\nFETCH FIRST {int(limit)} ROWS ONLY" if limit else ""
    source = f"{_quoted(owner)}.{_quoted(table)}" if owner else _quoted(table)
    sql = (
        f"SELECT {', '.join([*key_items, *value_items])}\n"
        f"FROM {source}\n"
        f"WHERE {' OR '.join(test for _column, test, _value in tests)}"
        + (f"\nORDER BY {order}" if order else "")
        + fetch
    )
    params: dict[str, Any] = {}
    if any(":term" in test for _column, test, _value in tests):
        params["term"] = term
    if number is not None and any(":num" in test for _column, test, _value in tests):
        params["num"] = number
    return DataQuery(
        sql     = sql,
        keys    = tuple(keys) or (fallback,),
        columns = tuple(column for column, _test, _value in tests),
        params  = params,
    )


def row_hits(table: str, query: DataQuery, rows: Sequence[dict[str, Any]]) -> list[DataHit]:
    """The hits of one table's rows: one per matched column, in column order."""
    hits: list[DataHit] = []
    for row in rows:
        key = ", ".join(
            _text(row_value(row, f"ADT_K{index}"))
            for index in range(1, len(query.keys) + 1)
        )
        for index, column in enumerate(query.columns, start=1):
            value = row_value(row, f"ADT_V{index}")
            if value is None:
                continue
            hits.append(DataHit(table=table, row=key, column=column, value=_text(value)))
    return hits


def _text(value: object) -> str:
    """A cell as one line: a LOB read, whitespace runs collapsed to one space.

    Never handed a NULL: a key column holds none, and a column that did not
    match comes back NULL and is skipped before it gets here.
    """
    read = getattr(value, "read", None)
    text = str(read() if callable(read) else value)
    return " ".join(text.split())


def _sortable(column: DataColumn) -> bool:
    """A column an ORDER BY takes: text, a number or a date, never a LOB."""
    kind = column.data_type.strip().upper()
    return kind in TEXT_TYPES or kind in NUMBER_TYPES or kind == "DATE" or kind.startswith(
        "TIMESTAMP"
    )


def search_objects(
    gateway: QueryGateway,
    objects: Sequence[DataObject],
    term: str,
    limit: int | None,
    reporter: DataReporter,
    label: str = "",
    *,
    failures: list[DataFailure] | None = None,
    fatal_error: Callable[[Exception], bool] = lambda error: False,
) -> list[DataHit]:
    """Every hit in `objects`, each object's row opened before its queries run.

    `label` prefixes an object's name in its hits, `SCHEMA.` when a run reads
    more than one schema, so two schemas' objects of one name stay apart.

    An object the database refuses, a view left invalid (`ORA-04063`) say,
    closes its row failed and lands in `failures`, and the next object is read:
    one broken view used to end the run and take every hit found with it.
    Only an error `fatal_error` names, a lost session, still ends it.
    """
    discovery = SearchDiscovery(gateway)
    hits: list[DataHit] = []
    for item in objects:
        reporter.begin(item.name)
        try:
            query = data_query(
                item.target,
                discovery.columns(item),
                term,
                limit,
                owner = item.owner,
                rowid = item.has_rowid,
            )
            found = (
                row_hits(
                    f"{label}{item.title}",
                    query,
                    gateway.read_only_fetch_all(query.sql, query.params),
                )
                if query is not None
                else []
            )
        except Exception as error:
            reporter.fail(item.name)
            if fatal_error(error) or failures is None:
                raise
            failures.append(DataFailure(f"{label}{item.title}", error))
            continue
        reporter.finish(item.name, len(found))
        hits.extend(found)
    return hits


__all__ = [name for name in globals() if not name.startswith("_")]
