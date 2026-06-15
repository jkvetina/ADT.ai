from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _ensure_sql_terminator,
    _ensure_statement_semicolon,
    _trim_trailing_blank_lines,
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
        lines[0] = _normalize_view_definition_line(lines[0])
        lines[1] = lines[1].lstrip()
        lines = _expand_simple_view_select(lines)
    return lines

def _normalize_view_definition_line(line: str) -> str:
    line = re.sub(
        r"\s+DEFAULT\s+COLLATION\s+\S+",
        "",
        line,
        flags=re.IGNORECASE,
    )
    line = re.sub(r"\s*\([^)]+\)\s*AS\b", " AS", line, count=1, flags=re.IGNORECASE)
    line = re.sub(r"\s*\([^)]+\)\s*BEQUEATH\b", " BEQUEATH", line, count=1, flags=re.IGNORECASE)
    return re.sub(r" {2,}", " ", line).rstrip()

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

def _top_level_view_select_index(lines: list[str]) -> int | None:
    depth = 0
    for index, line in enumerate(lines):
        if depth == 0 and re.match(r"\s*select\s+", line, flags=re.IGNORECASE):
            return index
        depth = _sql_parenthesis_depth_after_line(line, depth)
    return None

def _sql_parenthesis_depth_after_line(line: str, depth: int) -> int:
    in_quoted_identifier = False
    in_string = False
    index = 0
    while index < len(line):
        char = line[index]
        if in_string:
            index += 1
            if char == "'":
                if index < len(line) and line[index] == "'":
                    index += 1
                    continue
                in_string = False
            continue

        if char == "'":
            in_string = True
            index += 1
            continue

        if char == '"':
            in_quoted_identifier = not in_quoted_identifier
            index += 1
            continue

        if not in_quoted_identifier and line[index : index + 2] == "--":
            break

        if not in_quoted_identifier and char == "(":
            depth += 1
        elif not in_quoted_identifier and char == ")" and depth > 0:
            depth -= 1
        index += 1
    return depth

def _find_from_keyword(payload: str) -> int | None:
    in_quoted_identifier = False
    in_string = False
    depth = 0
    index = 0
    while index < len(payload):
        char = payload[index]
        if in_string:
            index += 1
            if char == "'":
                if index < len(payload) and payload[index] == "'":
                    index += 1
                    continue
                in_string = False
            continue

        if char == "'":
            in_string = True
            index += 1
            continue

        if char == '"':
            in_quoted_identifier = not in_quoted_identifier
            index += 1
            continue

        if not in_quoted_identifier and char == "(":
            depth += 1
            index += 1
            continue

        if not in_quoted_identifier and char == ")" and depth > 0:
            depth -= 1
            index += 1
            continue

        if (
            not in_quoted_identifier
            and depth == 0
            and payload[index : index + 4].lower() == "from"
            and (index == 0 or not _is_identifier_char(payload[index - 1]))
            and (index + 4 == len(payload) or not _is_identifier_char(payload[index + 4]))
        ):
            return index
        index += 1
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
    current: list[str] = []
    in_quoted_identifier = False
    in_string = False
    depth = 0
    index = 0
    while index < len(projection):
        char = projection[index]
        if in_string:
            current.append(char)
            index += 1
            if char == "'":
                if index < len(projection) and projection[index] == "'":
                    current.append(projection[index])
                    index += 1
                    continue
                in_string = False
            continue

        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue

        if char == '"':
            in_quoted_identifier = not in_quoted_identifier
            current.append(char)
            index += 1
            continue

        if not in_quoted_identifier and char == "(":
            depth += 1
        elif not in_quoted_identifier and char == ")" and depth > 0:
            depth -= 1
        elif not in_quoted_identifier and depth == 0 and char == ",":
            items.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    items.append("".join(current))
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
        return None

    alias = match.group("alias")
    if not alias:
        return column

    normalized_alias = _normalize_simple_view_identifier(alias)
    if not re.fullmatch(r"[a-z][a-z0-9_$#]*", normalized_alias):
        return None
    return f"{normalized_alias}.{column}"

def _normalize_simple_view_identifier(name: str) -> str:
    name = name.strip()
    quoted_match = re.fullmatch(r'"([A-Za-z][A-Za-z0-9_$#]*)"', name)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", name):
        return name.lower()
    return name.strip('"')
