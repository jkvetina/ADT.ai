from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _ensure_statement_semicolon,
    _matching_parenthesis_index,
    _normalize_definition_line_only,
    _normalize_sql_identifier,
    _replace_outside_sql_strings,
    _split_top_level_commas,
    _trim_trailing_blank_lines,
    qualified,
)


def normalize_index(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    if not lines:
        return lines

    index_pattern = (
        r"\s*CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+"
        r"(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?P<name>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)"
        r"|\"[^\"]+\"|[A-Za-z0-9_$#]+)\s+ON\s+"
        r"(?P<table>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)"
        r"|\"[^\"]+\"|[A-Za-z0-9_$#]+)\s*"
        r"(?P<body>.*)$"
    )
    match_line = lines[0]
    option_start = 1
    match = re.match(index_pattern, match_line, flags=re.IGNORECASE)
    if not match and len(lines) > 1 and re.match(r"\s*ON\b", lines[1], flags=re.IGNORECASE):
        match_line = f"{lines[0].rstrip()} {lines[1].strip()}"
        option_start = 2
        match = re.match(index_pattern, match_line, flags=re.IGNORECASE)
    if not match:
        lines = _normalize_definition_line_only(lines, context)
        lines = _trim_trailing_blank_lines(lines)
        return _ensure_statement_semicolon(lines) + [""]

    body = match.group("body").strip()
    if not body.startswith("("):
        lines = _normalize_definition_line_only(lines, context)
        lines = _trim_trailing_blank_lines(lines)
        return _ensure_statement_semicolon(lines) + [""]

    close_index = _matching_parenthesis_index(body, 0)
    if close_index is None:
        lines = _normalize_definition_line_only(lines, context)
        lines = _trim_trailing_blank_lines(lines)
        return _ensure_statement_semicolon(lines) + [""]

    expression = body[1:close_index]
    suffix = body[close_index + 1:].strip().rstrip(";")
    option_lines = [line.rstrip() for line in lines[option_start:]]
    kind = "CREATE UNIQUE INDEX" if match.group("unique") else "CREATE INDEX"
    if_not_exists = " IF NOT EXISTS" if context.add_if_not_exists else ""
    # Both names are re-derived here rather than edited in place, so both need
    # the owner put back under `keep_owner`; the index and its table share one.
    result = [
        f"{kind}{if_not_exists} "
        f"{qualified(_normalize_sql_identifier(match.group('name')), context)}",
    ]
    table_name = qualified(_normalize_sql_identifier(match.group("table")), context)
    columns = _simple_index_columns(expression)
    expression_items = _index_expression_items(expression)
    has_options = bool(option_lines)
    if columns and len(columns) == 1:
        line = f"    ON {table_name} ({columns[0]})"
        if suffix:
            line += f" {suffix}"
        if not has_options:
            line += ";"
        result.append(line)
    elif columns:
        result.append(f"    ON {table_name} (")
        result.extend(
            f"        {column}{',' if index < len(columns) - 1 else ''}"
            for index, column in enumerate(columns)
        )
        result.append(f"    ){f' {suffix}' if suffix else ''}{'' if has_options else ';'}")
    elif len(expression_items) > 1:
        result.append(f"    ON {table_name} (")
        result.extend(
            f"        {item}{',' if index < len(expression_items) - 1 else ''}"
            for index, item in enumerate(expression_items)
        )
        result.append(f"    ){f' {suffix}' if suffix else ''}{'' if has_options else ';'}")
    else:
        line = f"    ON {table_name} ({_normalize_index_expression(expression.strip())})"
        if suffix:
            line += f" {suffix}"
        if not has_options:
            line += ";"
        result.append(line)

    if option_lines:
        result.extend(_ensure_statement_semicolon(option_lines))
    return result + [""]


def _simple_index_columns(expression: str) -> list[str] | None:
    columns = _split_top_level_commas(expression)
    if not columns:
        return None
    normalized: list[str] = []
    for column in columns:
        if not re.fullmatch(r"(?:\"[A-Z][A-Z0-9_$#]*\"|[A-Z][A-Z0-9_$#]*)", column.strip()):
            return None
        normalized.append(_normalize_sql_identifier(column))
    return normalized

def _index_expression_items(expression: str) -> list[str]:
    return [
        _normalize_index_expression(item.strip())
        for item in _split_top_level_commas(expression)
    ]

def _normalize_index_expression(expression: str) -> str:
    return _replace_outside_sql_strings(
        expression,
        lambda chunk: re.sub(
            r'"([A-Z][A-Z0-9_$#]*)"',
            lambda match: match.group(1).lower(),
            chunk,
        ),
    )
