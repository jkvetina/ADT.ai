from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _ensure_sql_terminator,
    _ensure_statement_semicolon,
    _trim_trailing_blank_lines,
    sql_spans,
)
from adt_ai.export_db.object_normalizers.view_columns import (
    COLUMN_LIST_RE,
    column_block,
)


def normalize_view(lines: list[str], context: NormalizationContext) -> list[str]:
    lines = _normalize_view_lines(lines, context)
    lines = _trim_trailing_blank_lines(lines)
    lines = _ensure_statement_semicolon(lines)
    return _ensure_sql_terminator(lines)

def _normalize_view_lines(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    if context.object_type == "VIEW" and len(lines) > 1:
        header = _normalize_view_definition_line(lines[0], context)
        # The select-list reflow reads and rewrites `lines[0]`, so it runs
        # against the line carrying the trailing keyword and the kept block is
        # spliced in front of the result rather than around it.
        lines[0] = header[-1]
        lines[1] = lines[1].lstrip()
        lines = _expand_simple_view_select(lines)
        lines = [*header[:-1], *lines]
    return lines

def _normalize_view_definition_line(
    line: str,
    context: NormalizationContext,
) -> list[str]:
    """The definition line, as one line or as a kept column list's block.

    Returns a list whose LAST entry is always the line carrying the trailing
    `AS` / `BEQUEATH`; everything before it is the `(` opener and the columns.
    """
    line = re.sub(
        r"\s+DEFAULT\s+COLLATION\s+\S+",
        "",
        line,
        flags=re.IGNORECASE,
    )
    match = COLUMN_LIST_RE.search(line)
    if match is None:
        return [re.sub(r" {2,}", " ", line).rstrip()]

    head = _collapse(line[: match.start()])
    tail = _collapse(line[match.end() :])
    if not context.keep_view_column_names:
        return [f"{head} {tail}".rstrip()]
    return [f"{head} (", *column_block(match.group(1)), f") {tail}".rstrip()]

def _collapse(payload: str) -> str:
    return re.sub(r" {2,}", " ", payload).strip()

def _expand_simple_view_select(lines: list[str]) -> list[str]:
    if len(lines) < 2:
        return lines
    body_lines = [line for line in lines[1:] if line.strip() != "/"]
    if not body_lines:
        return lines

    select_index = _top_level_view_select_index(body_lines)
    if select_index is None:
        return lines

    select_match = re.match(
        r"(?P<select>select)\s+(?P<projection>.*)$",
        body_lines[select_index],
        flags=re.IGNORECASE,
    )
    if not select_match:
        return lines

    select_line = select_match.group("select")
    projection_source = select_match.group("projection")
    distinct_match = re.match(
        r"(?P<distinct>distinct)\s+(?P<projection>.*)$",
        projection_source,
        flags=re.IGNORECASE,
    )
    if distinct_match:
        select_line = f"{select_line} {distinct_match.group('distinct')}"
        projection_source = distinct_match.group("projection")

    projection_lines: list[str] = [projection_source]
    tail_lines: list[str] | None = None
    for index, line in enumerate(body_lines[select_index:]):
        absolute_index = select_index + index
        if index == 0:
            from_index = _find_from_keyword(projection_source)
            if from_index is None:
                continue
            projection_lines = [projection_source[:from_index]]
            tail_lines = [
                projection_source[from_index:].lstrip(),
                *body_lines[absolute_index + 1 :],
            ]
            break

        from_index = _find_from_keyword(line)
        if from_index is None:
            projection_lines.append(line)
            continue
        projection_lines.append(line[:from_index])
        tail_lines = [line[from_index:].lstrip(), *body_lines[absolute_index + 1 :]]
        break

    if tail_lines is None:
        return lines
    if any(_contains_sql_comment(line) for line in projection_lines):
        # The reflow joins the select list onto one line, which would park every
        # later column (and `from`) behind the comment (ADT #299). A commented
        # select list is the body-preserving "stays literal" case.
        return lines

    projection = " ".join(line.strip() for line in projection_lines)
    columns = _view_projection_columns(projection)
    projection_output_lines = _view_projection_output_lines(
        projection,
        columns,
        allow_unquoted_wrap=len(projection_lines) == 1,
    )
    if projection_output_lines is None:
        return lines
    return [
        lines[0],
        *body_lines[:select_index],
        select_line,
        *projection_output_lines,
        *tail_lines,
    ]

def _contains_sql_comment(payload: str) -> bool:
    return any(kind == "comment" for kind, _, _ in sql_spans(payload))

def _top_level_view_select_index(lines: list[str]) -> int | None:
    depth = 0
    for index, line in enumerate(lines):
        if depth == 0 and re.match(r"\s*select\s+", line, flags=re.IGNORECASE):
            return index
        depth = _sql_parenthesis_depth_after_line(line, depth)
    return None

def _sql_parenthesis_depth_after_line(line: str, depth: int) -> int:
    for index in _code_positions(line):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
    return depth

def _code_positions(payload: str) -> list[int]:
    """Every index of `payload` that is SQL rather than string, comment or identifier.

    The one scan for all three questions this module asks of DDL text, where each
    used to carry its own `in_string` walk against the rule `#299` wrote (ADT
    #474). A quoted identifier is opaque here on purpose: a `(`, a top-level `,`
    and the `from` keyword are all SQL structure, and `"A(B"`, `"X,Y"` and
    `"FROM"` are names that merely look like it.
    """
    return [
        index
        for kind, start, end in sql_spans(payload, identifiers=True)
        if kind == "code"
        for index in range(start, end)
    ]

def _find_from_keyword(payload: str) -> int | None:
    depth = 0
    for index in _code_positions(payload):
        char = payload[index]
        if char == "(":
            depth += 1
            continue
        if char == ")" and depth > 0:
            depth -= 1
            continue
        if (
            depth == 0
            and payload[index : index + 4].lower() == "from"
            and (index == 0 or not _is_identifier_char(payload[index - 1]))
            and (index + 4 == len(payload) or not _is_identifier_char(payload[index + 4]))
        ):
            return index
    return None

def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char in "_$#"

def _view_projection_columns(projection: str) -> list[str] | None:
    if '"' not in projection:
        return None

    columns: list[str] = []
    changed = False
    for token in _split_top_level_projection_items(projection):
        column = _simple_view_projection_column(token)
        if column is None:
            column = token.strip()
        elif '"' in token:
            changed = True
        columns.append(column)
    if not changed:
        return None
    return columns or None

def _view_projection_output_lines(
    projection: str,
    columns: list[str] | None,
    *,
    allow_unquoted_wrap: bool,
) -> list[str] | None:
    if columns is not None:
        return [
            f"    {column}{',' if index < len(columns) - 1 else ''}"
            for index, column in enumerate(columns)
        ]

    if not allow_unquoted_wrap:
        return None

    unquoted_columns = _compact_unquoted_view_columns(projection)
    if unquoted_columns is None:
        return None
    return [
        f"    {column}{',' if index < len(unquoted_columns) - 1 else ''}"
        for index, column in enumerate(unquoted_columns)
    ]

def _compact_unquoted_view_columns(projection: str) -> list[str] | None:
    if '"' in projection:
        return None
    items = _split_top_level_projection_items(projection)
    if len(items) < 2:
        return None
    for item in items:
        if not re.fullmatch(
            r"\s*[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)?\s*",
            item,
        ):
            return None
    return [item.strip() for item in items]

def _split_top_level_projection_items(projection: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = 0
    code = set(_code_positions(projection))
    for index, char in enumerate(projection):
        if index not in code:
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            items.append(projection[start:index])
            start = index + 1
    items.append(projection[start:])
    return items

def _simple_view_projection_column(token: str) -> str | None:
    identifier = r'(?:[A-Za-z][A-Za-z0-9_$#]*|"[A-Za-z][A-Za-z0-9_$#]*")'
    match = re.fullmatch(
        rf"\s*(?:(?P<alias>{identifier})\s*\.\s*)?(?P<column>{identifier})\s*",
        token,
    )
    if not match:
        return None

    column = _normalize_simple_view_identifier(match.group("column"))
    if not re.fullmatch(r"[a-z][a-z0-9_$#]*", column):
        # pragma: no cover reason: unreachable, `column` already matched `identifier`'s charset
        return None  # pragma: no cover

    alias = match.group("alias")
    if not alias:
        return column

    normalized_alias = _normalize_simple_view_identifier(alias)
    if not re.fullmatch(r"[a-z][a-z0-9_$#]*", normalized_alias):
        # pragma: no cover reason: unreachable, `alias` already matched `identifier`'s charset
        return None  # pragma: no cover
    return f"{normalized_alias}.{column}"

def _normalize_simple_view_identifier(name: str) -> str:
    name = name.strip()
    quoted_match = re.fullmatch(r'"([A-Za-z][A-Za-z0-9_$#]*)"', name)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", name):
        return name.lower()
    # pragma: no cover reason: both callers only pass a name matching one of the two patterns above
    return name.strip('"')  # pragma: no cover
