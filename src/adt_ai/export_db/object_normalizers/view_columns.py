"""The declared column list on a VIEW or MATERIALIZED VIEW header.

DBMS_METADATA emits a `("C1", "C2")` projection after the object name for both
types, and the export drops it by default so the query below implies the
columns. That is old ADT's behaviour, kept because a declaration naming its
columns differently from the select list is a defect source, not documentation.
`keep_view_column_names` turns the drop off for a project that wants the
declaration preserved.

**An annotated list is the exception, at either setting.** Since 23ai a column
may carry an `ANNOTATIONS (...)` clause, and the declared list is the only
place one can live: `ALTER VIEW` cannot add a column annotation afterwards, and
`USER_ANNOTATIONS_USAGE` is a read rather than a DDL surface. A list carrying
annotations IS documentation, so the reason for the drop stops applying to it
(ADT #761, approved before the implementation).

Both types find and render that list through this module rather than each
spelling it. They did not before: VIEW carried a regex on its definition line
and MATERIALIZED VIEW a paren walk over the whole payload, which is two
readings of one question and exactly the shape `tests/contracts/shared_readers.txt`
exists to stop growing a third of.
"""

from __future__ import annotations

import re

from adt_ai.export_db.normalizers import sql_spans
from adt_ai.export_db.object_normalizers.annotations import annotations_index

_SIMPLE_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")

# The keyword that follows the declared column list on a view's definition line.
# One pattern covers both spellings DBMS_METADATA emits, a plain `AS` and the
# `BEQUEATH DEFINER AS` a definer-rights view carries.
_TAIL_KEYWORD_RE = re.compile(r"\b(?:AS|BEQUEATH)\b", flags=re.IGNORECASE)


def find_column_list(line: str) -> tuple[int, int] | None:
    """`(open, close)` indices of the declared column list on `line`, or `None`.

    The list is the FIRST top-level parenthesised group before the trailing
    `AS`/`BEQUEATH`, found by matching parentheses rather than by scanning to
    the first `)`.

    It used to be `\\(([^)]+)\\)` with a lookahead for that keyword, which on an
    unannotated view is the same answer and on an annotated one is a different
    group entirely. `("ID" ANNOTATIONS(...)) ANNOTATIONS("TITLE" 'x') AS` ends
    on a `)` immediately before `AS`, so the pattern matched the OBJECT's
    annotation list, the caller dropped what it had matched, and the exported
    file carried a bare `ANNOTATIONS` keyword that Oracle answers `ORA-11546:
    missing left parenthesis when specifying ANNOTATIONS` to, so the export
    will not deploy (ADT #761).
    """
    code = code_positions(line)
    tail = _tail_keyword_index(line, code)
    if tail is None:
        return None

    depth = 0
    open_index: int | None = None
    for index in range(tail):
        if index not in code:
            continue
        char = line[index]
        if char == "(":
            if depth == 0:
                open_index = index
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
            if depth == 0 and open_index is not None:
                return open_index, index
    return None


def _tail_keyword_index(line: str, code: set[int]) -> int | None:
    """Index of the `AS`/`BEQUEATH` that ends the header, at paren depth zero.

    Depth matters because an annotation value is arbitrary user text: `AS` sits
    inside `ANNOTATIONS("NOTE" 'read AS a total')` as readily as it does in the
    header, and only the one outside every parenthesis ends the declaration.
    """
    depth = 0
    for index, char in enumerate(line):
        if index not in code:
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif depth == 0 and _TAIL_KEYWORD_RE.match(line, index):
            return index
    return None


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

    A column carrying an `ANNOTATIONS (...)` clause is normalized on its NAME
    and kept verbatim from the keyword on: the annotation names and values are
    user documentation, not identifiers this pass may respell.
    """
    name = token.strip()
    index = annotations_index(name)
    if index is None:
        return _plain_column_name(name)
    return f"{_plain_column_name(name[:index].strip())} {name[index:].strip()}".strip()


def _plain_column_name(name: str) -> str:
    quoted = re.fullmatch(r'"([^"]*)"', name)
    if quoted and _SIMPLE_IDENTIFIER_RE.fullmatch(quoted.group(1)):
        return quoted.group(1).lower()
    if _SIMPLE_IDENTIFIER_RE.fullmatch(name):
        return name.lower()
    return name


def split_top_level_items(payload: str) -> list[str]:
    """`payload` split on its top-level commas, ignoring quoted and nested text.

    A comma inside `"A,B"` is part of a name rather than a separator, which is
    why the split reads `sql_spans` instead of `str.split`. Since 23ai the list
    nests as well, so the walk tracks depth too: `ANNOTATIONS("RULE" 'HIGH when
    (P1, P2)')` puts a parenthesis and a separator-shaped comma inside one
    column's own text, and this docstring used to argue no nesting could occur
    because Oracle admitted no expression in the list (ADT #761).
    """
    items: list[str] = []
    start = 0
    depth = 0
    code = code_positions(payload)
    for index, char in enumerate(payload):
        if index not in code:
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            items.append(payload[start:index])
            start = index + 1
    items.append(payload[start:])
    return items


def collapse_spaces(payload: str) -> str:
    """`payload` with runs of spaces squeezed, outside strings and identifiers.

    The header carries user text now that the object-level `ANNOTATIONS (...)`
    clause rides on it, so a blanket `re.sub(r" {2,}", " ", …)` would rewrite an
    annotation value: `'two  spaces'` is documentation Oracle stored, not layout
    this pass may tidy (ADT #761).
    """
    return "".join(
        re.sub(r" {2,}", " ", payload[start:end]) if kind == "code" else payload[start:end]
        for kind, start, end in sql_spans(payload, identifiers=True)
    ).strip()


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
