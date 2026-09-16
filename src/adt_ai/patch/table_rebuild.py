"""A damaged `CREATE TABLE` file, rebuilt from the list it declares (ADT #859).

The table diff builds the version a target stands at as a shadow table, and that
version is history: when a file lost a comma, grew a second one, or carries a
partition clause Oracle refuses, the commit that repaired it is usually the one
being patched. `#855` put back the one comma a `CONSTRAINT` clause needs and
nothing else. Jan asked for the general answer the same day: *"reconstruct
statement from that list alone, bypassing the formatting in the source file"*.

So nothing here edits the file. It reads the list the statement declares, one
item per column or out-of-line constraint, and writes a new statement holding
only that list:

* Comments go before anything is read, because a comment is not SQL (`#299`).
* An item ends at a top-level comma, and also at a line that opens a new column
  or out-of-line constraint, which is what a missing comma looks like.
* An empty item is an extra comma, and is dropped.
* The list ends at its close paren or, when that is missing, at a statement
  terminator or a line opening a table clause. Everything past it goes:
  partitioning, storage and tablespace are suppressed on both sides of the
  comparison anyway (`queries/table_diff.py`), and a `COMMENT ON` is a statement
  of its own.

The removed `table_alter.py` read the same list the same way and then trusted
it, which is how a missing comma once reported a `MODIFY` nobody made. This one
is not trusted either: the runner reaches for it only after Oracle refuses the
file as committed, and compares it only once Oracle builds what it wrote.
"""

from __future__ import annotations

import re

from adt_ai.export_db.normalizers import sql_spans

_NAME = r'(?:"[^"\n]+"|[A-Za-z][A-Za-z0-9_$#]*)'

#: What a column's type starts with. `AS` and `GENERATED` open a virtual column,
#: and `DOMAIN` a 26ai column typed by its domain alone.
_DATATYPES = (
    "VARCHAR2|VARCHAR|NVARCHAR2|CHARACTER|CHAR|NCHAR|NUMBER|NUMERIC|DECIMAL|DEC|"
    "INTEGER|INT|SMALLINT|FLOAT|REAL|DOUBLE|BINARY_FLOAT|BINARY_DOUBLE|DATE|"
    "TIMESTAMP|INTERVAL|CLOB|NCLOB|BLOB|BFILE|RAW|LONG|ROWID|UROWID|XMLTYPE|"
    "JSON|BOOLEAN|BOOL|VECTOR|SDO_GEOMETRY|DOMAIN|AS|GENERATED"
)

#: A line opening with a name and a type is a new column...
_COLUMN_RE = re.compile(rf"\s*(?P<name>{_NAME})\s+(?:{_DATATYPES})\b", flags=re.IGNORECASE)

#: ...unless the "name" is a word that carries the column above it onto a second
#: line: `DEFAULT DATE '2020-01-01'` reads as a name and a type, and is neither.
_CONTINUATIONS = frozenset(
    {
        "AND", "AS", "BETWEEN", "CASE", "CHECK", "COLLATE", "CONSTRAINT", "DEFAULT",
        "DISABLE", "DOMAIN", "ELSE", "ENABLE", "ENCRYPT", "END", "GENERATED", "IN",
        "INVISIBLE", "IS", "LIKE", "NOT", "NULL", "ON", "OR", "REFERENCES", "THEN",
        "USING", "VISIBLE", "WHEN", "WITH",
    }
)

#: An out-of-line constraint always carries its column list or condition in
#: parentheses. An inline `CONSTRAINT x PRIMARY KEY` on its own line never does,
#: so it stays with its column. An inline `CHECK (...)` on its own line reads as
#: out-of-line, which Oracle stores as the same constraint.
_CONSTRAINT_RE = re.compile(
    rf"\s*(?:CONSTRAINT\s+{_NAME}\s+)?(?:PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\s*\(",
    flags=re.IGNORECASE,
)

#: A constraint's name on a line of its own, which is how the exporter writes
#: every named one. The `PRIMARY KEY (...)` line under it is that constraint's
#: body, not an unnamed constraint of its own.
_NAME_ONLY_RE = re.compile(rf"CONSTRAINT\s+{_NAME}", flags=re.IGNORECASE)

#: A line opening one of these ends a list whose close paren is missing.
_TABLE_CLAUSE_RE = re.compile(
    r"\s*(?:(?:PARTITION\s+BY|SUBPARTITION\s+BY|TABLESPACE|ORGANIZATION|SEGMENT\s+CREATION"
    r"|PCTFREE|STORAGE|NOCOMPRESS|COMPRESS|NOLOGGING|LOGGING|ON\s+COMMIT"
    r"|(?:ENABLE|DISABLE)\s+ROW\s+MOVEMENT)\b|LOB\s*\()",
    flags=re.IGNORECASE,
)

_HEADER_RE = re.compile(r"\bCREATE\b[^;(]*?\bTABLE\b[^;(]*\(", flags=re.IGNORECASE)


def rebuild_table(sql: str) -> str | None:
    """``sql``'s `CREATE TABLE`, rewritten from its columns and constraints alone.

    `None` when there is nothing to rebuild from: no `CREATE TABLE`, or a list
    holding no item at all.
    """
    code = _without_comments(sql)
    header = _HEADER_RE.search(code)
    if header is None:
        return None
    items = [item for item in map(_flattened, _items(code, header.end())) if item]
    if not items:
        return None
    body = ",\n".join(f"    {item}" for item in items)
    return f"{_flattened(code[header.start():header.end() - 1])} (\n{body}\n);\n"


def _without_comments(sql: str) -> str:
    """``sql`` with every comment blanked and its line breaks kept.

    The line breaks are what a missing comma is found by, so a block comment
    spanning three lines leaves three behind rather than one space.
    """
    return "".join(
        ("\n" * sql.count("\n", start, end) or " ") if kind == "comment" else sql[start:end]
        for kind, start, end in sql_spans(sql, identifiers=True)
    )


def _items(code: str, start: int) -> list[str]:
    """The raw items of the list opening at ``start``, empty ones included."""
    items: list[str] = []
    depth = 0
    begin = start
    for kind, span_start, span_end in sql_spans(code, identifiers=True):
        if kind != "code":
            continue
        for index in range(max(span_start, start), span_end):
            char = code[index]
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            elif char in ");":
                return [*items, code[begin:index]]
            elif depth == 0 and char == ",":
                items.append(code[begin:index])
                begin = index + 1
            elif depth == 0 and char == "\n":
                if _opens_item(code, index + 1):
                    current = code[begin:index].strip()
                    if current and not _NAME_ONLY_RE.fullmatch(current):
                        items.append(code[begin:index])
                        begin = index + 1
                elif _TABLE_CLAUSE_RE.match(code, index + 1):
                    return [*items, code[begin:index]]
    return [*items, code[begin:]]


def _opens_item(code: str, position: int) -> bool:
    """Whether the line at ``position`` starts a column or out-of-line constraint."""
    if _CONSTRAINT_RE.match(code, position):
        return True
    column = _COLUMN_RE.match(code, position)
    return column is not None and column.group("name").upper() not in _CONTINUATIONS


def _flattened(text: str) -> str:
    """``text`` on one line, with string literals and quoted names left byte for byte."""
    return "".join(
        re.sub(r"\s+", " ", text[start:end]) if kind == "code" else text[start:end]
        for kind, start, end in sql_spans(text, identifiers=True)
    ).strip()


__all__ = ["rebuild_table"]
