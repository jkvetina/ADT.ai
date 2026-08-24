from __future__ import annotations

import re
from dataclasses import dataclass

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _constraint_column_names,
    _identifier_key,
    _matching_parenthesis_index,
    _normalize_sql_identifier,
    sql_spans,
)


@dataclass(frozen=True)
class _FoldedConstraint:
    constraint_name : str
    index_name      : str
    constraint_type : str
    columns         : tuple[str, ...]
    source_item     : str


def _collect_index_backed_constraints(
    suffix: str,
    context: NormalizationContext,
) -> tuple[list[_FoldedConstraint], str]:
    trailing_match = re.search(r"\b(?:CREATE|ALTER)\b", suffix, flags=re.IGNORECASE)
    if not trailing_match:
        return [], suffix

    head = suffix[: trailing_match.start()]
    statements = _split_ddl_statements(suffix[trailing_match.start():])

    create_index_keys: dict[str, int] = {}
    for index, statement in enumerate(statements):
        key = _parse_create_index(statement)
        if key is not None:
            create_index_keys.setdefault(key, index)

    folds: list[_FoldedConstraint] = []
    consumed: set[int] = set()
    for index, statement in enumerate(statements):
        parsed = _parse_alter_add_constraint(statement)
        if parsed is None:
            continue
        create_index = create_index_keys.get(parsed["index_key"])
        if create_index is None:
            continue
        folds.append(_make_fold(parsed, context))
        consumed.add(index)
        consumed.add(create_index)

    if not folds:
        return [], suffix

    remaining = [
        statement for index, statement in enumerate(statements) if index not in consumed
    ]
    if not remaining:
        return folds, head
    return folds, head + "\n".join(f"{statement};" for statement in remaining)


_DDL_STATEMENT_START_RE = re.compile(r"(?:CREATE|ALTER)\b", flags=re.IGNORECASE)


def _split_ddl_statements(text: str) -> list[str]:
    """The trailing DDL statements, split on a top-level `;` or a new CREATE/ALTER.

    Scanned through `sql_spans()` since ADT #474 rather than through a private
    `in_string` walk, which is the rule `#299` wrote and this function had never
    followed. It knew strings and nothing about comments, so a semicolon inside
    `-- and then; more` split a `CREATE TABLE` in two and handed the halves to a
    regex parser as prose; `discovery`'s walk, which did know, got the same input
    right. Quoted identifiers stay code here, as they were, since a `"` never
    changes what this function is looking for.
    """
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    at_line_start = True

    def flush() -> None:
        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        current.clear()

    for kind, span_start, span_end in sql_spans(text):
        if kind != "code":
            current.append(text[span_start:span_end])
            at_line_start = False
            continue

        for index in range(span_start, span_end):
            char = text[index]

            if (
                at_line_start
                and depth == 0
                and "".join(current).strip()
                and _DDL_STATEMENT_START_RE.match(text, index)
            ):
                flush()

            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == ";" and depth == 0:
                flush()
                at_line_start = True
                continue

            current.append(char)
            if char == "\n":
                at_line_start = True
            elif not char.isspace():
                at_line_start = False

    flush()
    return statements

def _parse_create_index(statement: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", statement).strip()
    match = re.match(
        r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+'
        r'(?P<name>"[^"]+"(?:\s*\.\s*"[^"]+")?|[A-Za-z0-9_$#.]+)\s+ON\b',
        collapsed,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _index_identity_key(match.group("name"))

def _parse_alter_add_constraint(statement: str) -> dict[str, str] | None:
    collapsed = re.sub(r"\s+", " ", statement).strip()
    match = re.match(
        r'ALTER\s+TABLE\s+.+?\s+ADD\s+CONSTRAINT\s+'
        r'(?P<name>"[^"]+"|[A-Za-z0-9_$#]+)\s+'
        r'(?P<ctype>PRIMARY\s+KEY|UNIQUE)\s*\(',
        collapsed,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    open_index = match.end() - 1
    close_index = _matching_parenthesis_index(collapsed, open_index)
    if close_index is None:
        return None

    using_match = re.search(
        r'USING\s+INDEX\s+'
        r'(?P<index>"[^"]+"(?:\s*\.\s*"[^"]+")?|[A-Za-z0-9_$#.]+)',
        collapsed[close_index + 1:],
        flags=re.IGNORECASE,
    )
    if not using_match:
        return None

    index_raw = using_match.group("index")
    return {
        "name_raw"    : match.group("name"),
        "ctype"       : re.sub(r"\s+", " ", match.group("ctype").upper()),
        "columns_raw" : collapsed[open_index + 1: close_index],
        "index_raw"   : index_raw,
        "index_key"   : _index_identity_key(index_raw),
    }

def _make_fold(parsed: dict[str, str], context: NormalizationContext) -> _FoldedConstraint:
    source_item = (
        f"CONSTRAINT {parsed['name_raw']} {parsed['ctype']} "
        f"({parsed['columns_raw']}) USING INDEX {parsed['index_raw']} ENABLE"
    )
    return _FoldedConstraint(
        constraint_name = _normalize_sql_identifier(parsed["name_raw"], context),
        index_name      = _normalize_sql_identifier(parsed["index_raw"], context),
        constraint_type = parsed["ctype"],
        columns         = tuple(_constraint_column_names(parsed["columns_raw"])),
        source_item     = source_item,
    )

def _index_identity_key(name: str) -> str:
    return _identifier_key(name.split(".")[-1])
