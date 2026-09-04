"""The declared column list on a VIEW or MATERIALIZED VIEW header.

DBMS_METADATA emits a `("C1", "C2")` projection after the object name for both
types, and the export drops it by default so the query below implies the
columns. That is old ADT's behaviour, kept because a declaration naming its
columns differently from the select list is a defect source, not documentation.
`keep_view_column_names` turns the drop off for a project that wants the
declaration preserved.

Both types find and render that list through this module rather than each
spelling it. They did not before: VIEW carried a regex on its definition line
and MATERIALIZED VIEW a paren walk over the whole payload, which is two
readings of one question and exactly the shape `tests/contracts/shared_readers.txt`
exists to stop growing a third of.
"""

from __future__ import annotations

import re

from adt_ai.export_db.normalizers import sql_spans

# The list as it sits on a view's definition line, up to but not including the
# keyword that follows it. The lookahead keeps that keyword in the remainder, so
# one pattern covers both spellings DBMS_METADATA emits, a plain `AS` and the
# `BEQUEATH DEFINER AS` a definer-rights view carries.
COLUMN_LIST_RE = re.compile(
    r"\s*\(([^)]+)\)\s*(?=(?:AS|BEQUEATH)\b)",
    flags=re.IGNORECASE,
)

_SIMPLE_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")


def column_block(inner: str) -> list[str]:
    """The kept list as an indented column per line, ready to sit under `(`.

    The four-space indent and the trailing comma are the select-list reflow's
    own shape (`_expand_simple_view_select`), so a view that keeps its
    declaration reads the same top and bottom rather than in two layouts.
    """
    columns = [normalize_column_name(item) for item in split_top_level_items(inner)]
    columns = [column for column in columns if column]
    return [
        f"    {column}{',' if index < len(columns) - 1 else ''}"
        for index, column in enumerate(columns)
    ]


def normalize_column_name(token: str) -> str:
    """A declared column lowercased and unquoted when that is safe, else verbatim.

    Only a name Oracle would resolve identically unquoted may lose its quotes:
    `"TOTAL"` is `TOTAL` either way, while `"Order Id"` unquoted is a different
    column and a syntax error besides. Same test the select-list reflow applies
    to its own identifiers.
    """
    name = token.strip()
    quoted = re.fullmatch(r'"([^"]*)"', name)
    if quoted and _SIMPLE_IDENTIFIER_RE.fullmatch(quoted.group(1)):
        return quoted.group(1).lower()
    if _SIMPLE_IDENTIFIER_RE.fullmatch(name):
        return name.lower()
    return name


def split_top_level_items(payload: str) -> list[str]:
    """`payload` split on its commas, ignoring any inside a quoted identifier.

    A comma inside `"A,B"` is part of a name rather than a separator, which is
    why the split reads `sql_spans` instead of `str.split`. There is no nesting
    to track: a declared column list is identifiers and commas, and Oracle
    admits no expression there, so a parenthesis inside one cannot occur.
    """
    items: list[str] = []
    start = 0
    code = code_positions(payload)
    for index, char in enumerate(payload):
        if char == "," and index in code:
            items.append(payload[start:index])
            start = index + 1
    items.append(payload[start:])
    return items


def code_positions(payload: str) -> set[int]:
    """Every index of `payload` that is SQL rather than string, comment or identifier.

    The one scan for every question this package asks of DDL text (ADT #474). A
    quoted identifier is opaque on purpose: a `(`, a top-level `,` and the `from`
    keyword are all SQL structure, and `"A(B"`, `"X,Y"` and `"FROM"` are names
    that merely look like it.
    """
    return {
        index
        for kind, start, end in sql_spans(payload, identifiers=True)
        if kind == "code"
        for index in range(start, end)
    }
