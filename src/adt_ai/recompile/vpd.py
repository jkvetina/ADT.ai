"""The ``recompile -vpd`` report: which VPD policies exist and which tables lack one.

Report-only, like ``-jobs``: nothing is compiled or changed. Two reads, the
schema's enabled policies and one coverage row per table in scope, and the rest is
arithmetic over them, so the functions, the missing list and the counts can never
disagree with the assignments they summarize (ADT #839).

The column that makes a table a candidate is the flag's value, ``-vpd TENANT_ID``,
where CORE's ``core_daily_missing_vpd_policies_v`` hard-codes it. A bare ``-vpd``
therefore never lists every unprotected table, only counts them: on a schema with
1000 tables and 10 policies, the 990 are a number, not a screen.

Which column a policy filters on is recorded nowhere in the dictionary: the
function builds the predicate at run time, from whatever session it runs in. A
call answers for that session only, and for the schema owner a well-written
function returns `1=0` or nothing (ADT #882), so the report never calls it.
It reads the function's own source instead, and keeps the words inside the
literals it can return, where every predicate it builds is spelled, that are real
columns of the table it protects. Nothing is inferred: a word that is not a
column of that table is dropped, and wrapped or unreadable source names nothing.

The source is the file ``export_db`` already wrote, never a second read of the
dictionary (ADT #884). The report only names what it needs; the caller supplies
the text, exporting a file first when it is missing or older than the object.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from adt_ai.export_db.normalizers import sql_spans
from adt_ai.recompile import queries
from adt_ai.shared.db import QueryGateway


@dataclass(frozen=True)
class VpdAssignment:
    table_name: str
    policy_name: str
    # PACKAGE.FUNCTION, or the bare function for a standalone one.
    function_name: str
    select: bool
    # "Y" when INSERT, UPDATE and DELETE are all covered, "*" for some, "" for none.
    dml: str
    dynamic: bool
    # The table's own columns the function's source names in a literal, in order.
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class VpdSource:
    """One policy function's source object, and when the database last changed it."""

    owner: str
    # PACKAGE BODY for a packaged function, FUNCTION for a standalone one.
    object_type: str
    name: str
    # LAST_DDL_TIME as YYYY-MM-DD HH24:MI:SS on the database clock, "" when unknown.
    ddl_time: str = ""
    # The database clock's UTC offset, +02:00.
    db_offset: str = ""


# Given the sources the report needs, their text keyed by (owner, name).
SourceProvider = Callable[[list[VpdSource]], dict[tuple[str, str], str]]


def _no_sources(_wanted: list[VpdSource]) -> dict[tuple[str, str], str]:
    return {}


@dataclass(frozen=True)
class VpdFunction:
    function_name: str
    assignments: list[VpdAssignment]

    def dyn(self) -> str:
        """``Y`` when every policy is DYNAMIC, ``*`` for some, blank for none."""
        dynamic = sum(item.dynamic for item in self.assignments)
        if dynamic == len(self.assignments):
            return "Y"
        return "*" if dynamic else ""

    def columns(self) -> list[str]:
        return sorted({column for item in self.assignments for column in item.columns})


@dataclass(frozen=True)
class VpdTable:
    table_name: str
    has_policy: bool
    has_column: bool


@dataclass(frozen=True)
class VpdReport:
    # the -vpd value, upper-cased; "" for a bare flag, which lists nothing missing.
    column: str = ""
    assignments: list[VpdAssignment] = field(default_factory=list)
    tables: list[VpdTable] = field(default_factory=list)

    def functions(self) -> list[VpdFunction]:
        """One entry per policy function, its assignments by table name."""
        grouped: dict[str, list[VpdAssignment]] = {}
        for item in sorted(self.assignments, key=lambda item: (item.table_name, item.policy_name)):
            grouped.setdefault(item.function_name, []).append(item)
        return [VpdFunction(name, grouped[name]) for name in sorted(grouped)]

    def missing(self) -> list[str]:
        """Tables carrying the -vpd column with no enabled policy."""
        return [item.table_name for item in self.tables if item.has_column and not item.has_policy]

    def protected_count(self) -> int:
        return sum(1 for item in self.tables if item.has_policy)


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().upper() in {"YES", "Y"}


def dml_flag(insert: Any, update: Any, delete: Any) -> str:
    covered = sum(_is_yes(value) for value in (insert, update, delete))
    if covered == 3:
        return "Y"
    return "*" if covered else ""


def _function_name(package: Any, function: Any) -> str:
    name = str(function or "")
    return f"{package}.{name}" if package else name


_IDENTIFIER = re.compile(r'"([^"]+)"|([A-Za-z][A-Za-z0-9_$#]*)')
_ROUTINE = re.compile(r'\b(FUNCTION|PROCEDURE)\s+"?([A-Za-z0-9_$#]+)"?', re.IGNORECASE)


def function_body(source: str, function: str) -> str:
    """The text of ``function`` inside ``source``, a package body or the function itself.

    The last ``FUNCTION <name>`` in code is the definition, a forward declaration
    coming first, and the next routine header in code ends it. Headers are read
    only from code spans, so a comment naming a routine cannot move either end.
    """
    headers = [
        (start + match.start(), match.group(1).upper(), match.group(2).upper())
        for kind, start, end in sql_spans(source, identifiers=True)
        if kind == "code"
        for match in _ROUTINE.finditer(source[start:end])
    ]
    found = [index for index, header in enumerate(headers)
             if header[1] == "FUNCTION" and header[2] == function.upper()]
    if not found:
        return ""
    index = found[-1]
    end = headers[index + 1][0] if index + 1 < len(headers) else len(source)
    return source[headers[index][0]:end]


_RETURN = re.compile(r"\bRETURN\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")
_ASSIGN = re.compile(r"\b([A-Za-z][A-Za-z0-9_$#]*)\s*:=")


def _statements(body: str) -> list[tuple[int, int, list[tuple[str, int, int]]]]:
    """``body`` cut at every ``;`` in code, each piece with its spans, offsets absolute."""
    pieces: list[tuple[int, int, list[tuple[str, int, int]]]] = []
    start, spans = 0, []
    for kind, span_start, span_end in sql_spans(body, identifiers=True):
        if kind == "code" and ";" in body[span_start:span_end]:
            cut = span_start
            for part in body[span_start:span_end].split(";")[:-1]:
                spans.append(("code", cut, cut + len(part)))
                pieces.append((start, cut + len(part), spans))
                cut += len(part) + 1
                start, spans = cut, []
            spans.append(("code", cut, span_end))
            continue
        spans.append((kind, span_start, span_end))
    pieces.append((start, len(body), spans))
    return pieces


def _code_match(
    pattern: re.Pattern[str], body: str, spans: list[tuple[str, int, int]]
) -> re.Match[str] | None:
    for kind, start, end in spans:
        if kind == "code":
            match = pattern.search(body, start, end)
            if match:
                return match
    return None


def _return_match(body: str, spans: list[tuple[str, int, int]]) -> re.Match[str] | None:
    """The first ``RETURN`` that hands a value back, never the header's ``RETURN VARCHAR2``."""
    code = ""
    for kind, start, end in spans:
        if kind != "code":
            continue
        for match in _RETURN.finditer(body, start, end):
            prefix = code + body[start:match.start()]
            header = list(re.finditer(r"\bFUNCTION\b", prefix, re.IGNORECASE))
            if header and not re.search(r"\bBEGIN\b", prefix[header[-1].end():], re.IGNORECASE):
                continue
            return match
        code += body[start:end]
    return None


def literal_columns(body: str, columns: set[str]) -> tuple[str, ...]:
    """Columns of the table named in a literal the function can return, first use first.

    Only a literal in a ``RETURN`` statement, or in an assignment to a variable
    some ``RETURN`` hands back, is part of a predicate: a context name such as
    ``SYS_CONTEXT('APEX$SESSION', 'APP_USER')`` or a value compared in an ``IF``
    is not, and would otherwise match a table that has a column of that name.
    """
    statements = _statements(body)
    returned: set[str] = set()
    for _, _, spans in statements:
        match = _return_match(body, spans)
        if match:
            returned.update(
                word.upper()
                for kind, start, end in spans
                if kind == "code" and end > match.end()
                for word in _WORD.findall(body, max(start, match.end()), end)
            )
    found: list[str] = []
    for _, _, spans in statements:
        match = _return_match(body, spans)
        if match is None:
            match = _code_match(_ASSIGN, body, spans)
            if match is None or match.group(1).upper() not in returned:
                continue
        for kind, start, end in spans:
            if kind != "string" or start < match.start():
                continue
            # an argument, as in SYS_CONTEXT('APP', 'STATUS'), is never predicate text
            if body[:start].rstrip()[-1:] in ("(", ","):
                continue
            literal = body[start:end]
            if literal[:1] in "qQ":
                literal = literal[1:]
            for quoted, bare in _IDENTIFIER.findall(literal):
                name = quoted or bare.upper()
                if name in columns and name not in found:
                    found.append(name)
    return tuple(found)


def vpd_sources(policy_rows: list[dict[str, Any]]) -> list[VpdSource]:
    """Each distinct source object the policies' functions live in, in first-use order."""
    found: dict[tuple[str, str], VpdSource] = {}
    for row in policy_rows:
        package = str(row.get("PACKAGE") or "")
        name = package or str(row.get("FUNCTION") or "")
        owner = str(row.get("FUNCTION_OWNER") or "")
        found.setdefault((owner, name), VpdSource(
            owner       = owner,
            object_type = "PACKAGE BODY" if package else "FUNCTION",
            name        = name,
            ddl_time    = str(row.get("FUNCTION_DDL_TIME") or ""),
            db_offset   = str(row.get("DB_UTC_OFFSET") or ""),
        ))
    return list(found.values())


def _source_columns(
    row: dict[str, Any],
    sources: dict[tuple[str, str], str],
    table_columns: dict[str, set[str]],
) -> tuple[str, ...]:
    function = str(row.get("FUNCTION") or "")
    owner = str(row.get("FUNCTION_OWNER") or "")
    source = sources.get((owner, str(row.get("PACKAGE") or function)), "")
    return literal_columns(
        function_body(source, function), table_columns.get(str(row["TABLE_NAME"]), set())
    )


def read_vpd(
    gateway: QueryGateway,
    *,
    object_name: str = "%",
    object_type: str = "%",
    prefix: str = "",
    ignore: str = "",
    column: str = "",
    sources: SourceProvider = _no_sources,
) -> VpdReport:
    binds: dict[str, Any] = {
        "object_name"    : object_name,
        "object_type"    : object_type,
        "objects_prefix" : prefix,
        "objects_ignore" : ignore,
    }
    policy_rows = gateway.fetch_all(queries.VPD_ASSIGNMENTS_QUERY, binds)
    table_columns: dict[str, set[str]] = {}
    texts: dict[tuple[str, str], str] = {}
    if policy_rows:
        for row in gateway.fetch_all(queries.VPD_POLICY_COLUMNS_QUERY):
            table_columns.setdefault(str(row["TABLE_NAME"]), set()).add(str(row["COLUMN_NAME"]))
        texts = sources(vpd_sources(policy_rows))
    table_rows = gateway.fetch_all(
        queries.VPD_TABLES_QUERY, {**binds, "vpd_column": column or None}
    )
    return VpdReport(
        column      = column,
        assignments = [
            VpdAssignment(
                str(row["TABLE_NAME"]),
                str(row["POLICY_NAME"]),
                _function_name(row.get("PACKAGE"), row.get("FUNCTION")),
                _is_yes(row.get("SEL")),
                dml_flag(row.get("INS"), row.get("UPD"), row.get("DEL")),
                str(row.get("POLICY_TYPE") or "").upper() == "DYNAMIC",
                _source_columns(row, texts, table_columns),
            )
            for row in policy_rows
        ],
        tables      = [
            VpdTable(
                str(row["TABLE_NAME"]),
                _is_yes(row.get("HAS_POLICY")),
                _is_yes(row.get("HAS_COLUMN")),
            )
            for row in table_rows
        ],
    )
