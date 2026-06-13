from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class NormalizationContext:
    object_type : str
    object_name : str
    object_owner: str | None = None


@dataclass(frozen=True)
class _FoldedConstraint:
    constraint_name : str
    index_name      : str
    constraint_type : str
    columns         : tuple[str, ...]
    source_item     : str


Normalizer = Callable[[list[str], NormalizationContext], list[str]]

BODY_PRESERVING_OBJECT_TYPES = {
    "FUNCTION",
    "PACKAGE",
    "PACKAGE BODY",
    "PROCEDURE",
    "SYNONYM",
    "TRIGGER",
    "TYPE",
    "TYPE BODY",
    "VIEW",
}
RAW_NORMALIZER_OBJECT_TYPES = {
    "INDEX",
    "TABLE",
}


class NormalizerError(Exception):
    """Raised when a user normalizer plugin cannot be loaded."""


class NormalizerRegistry:
    def __init__(self, normalizers: Mapping[str, Normalizer] | None = None) -> None:
        self._normalizers = {
            object_type.upper(): normalizer
            for object_type, normalizer in (normalizers or {}).items()
        }

    @classmethod
    def builtin(cls) -> NormalizerRegistry:
        return cls(
            {
                "INDEX": _normalize_index,
                "JOB": _normalize_job,
                "TABLE": _normalize_table,
                "SEQUENCE": _normalize_sequence,
                "SYNONYM": _normalize_synonym,
                "TYPE": _normalize_type,
                "TYPE BODY": _normalize_type_body,
            }
        )

    def has(self, object_type: str) -> bool:
        return object_type.upper() in self._normalizers

    def get(self, object_type: str) -> Normalizer | None:
        return self._normalizers.get(object_type.upper())

    def with_plugins(self, plugin_paths: Iterable[Path]) -> NormalizerRegistry:
        normalizers = dict(self._normalizers)
        for plugin_path in plugin_paths:
            normalizers.update(_load_plugin(plugin_path))
        return NormalizerRegistry(normalizers)


def normalize_ddl(
    payload: str,
    object_type: str,
    object_name: str,
    registry: NormalizerRegistry | None = None,
) -> str:
    registry = registry or NormalizerRegistry.builtin()
    normalized_payload = payload.replace("\t", "    ").strip()
    lines = normalized_payload.splitlines()
    context = NormalizationContext(
        object_type  = object_type.upper(),
        object_name  = object_name,
        object_owner = _extract_definition_owner(normalized_payload, object_type),
    )
    normalizer = registry.get(object_type)
    if normalizer is not None and context.object_type in RAW_NORMALIZER_OBJECT_TYPES:
        lines = [line.rstrip() for line in lines]
        lines = normalizer(lines, context)
        return "\n".join(line.rstrip() for line in lines) + "\n"

    lines = _normalize_common(lines, context, terminate=False)
    if normalizer is not None:
        lines = normalizer(lines, context)
    else:
        lines = _normalize_common(
            lines,
            context,
            terminate=_uses_slash_terminator(context.object_type),
        )
    return "\n".join(line.rstrip() for line in lines) + "\n"


def _load_plugin(plugin_path: Path) -> dict[str, Normalizer]:
    module = _import_plugin(plugin_path)

    mapping = getattr(module, "NORMALIZERS", None)
    if isinstance(mapping, dict):
        return {
            str(object_type).upper(): normalizer
            for object_type, normalizer in mapping.items()
            if callable(normalizer)
        }

    object_type = getattr(module, "OBJECT_TYPE", None)
    normalizer = getattr(module, "normalize", None)
    if isinstance(object_type, str) and callable(normalizer):
        return {object_type.upper(): normalizer}

    raise NormalizerError(
        f"Plugin does not expose NORMALIZERS or OBJECT_TYPE + normalize: {plugin_path}"
    )


def _import_plugin(plugin_path: Path) -> ModuleType:
    path = Path(plugin_path)
    spec = importlib.util.spec_from_file_location(f"adt_ai_normalizer_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise NormalizerError(f"Cannot load normalizer plugin: {plugin_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_common(
    lines: list[str],
    context: NormalizationContext,
    terminate: bool,
) -> list[str]:
    if context.object_type in BODY_PRESERVING_OBJECT_TYPES:
        lines = _normalize_definition_line_only(lines, context)
        lines = _normalize_view_lines(lines, context)
        lines = _split_spec_from_body(lines, context)
        lines = _strip_generated_trigger_enable(lines, context)
        lines = _trim_trailing_blank_lines(lines)
        lines = _ensure_statement_semicolon(lines)
        if terminate:
            return _ensure_sql_terminator(lines)
        return lines

    payload = "\n".join(lines)
    payload = _replace_outside_sql_strings(
        payload,
        lambda chunk: re.sub(
            r'"([A-Z][A-Z0-9_$#]*)"',
            lambda match: match.group(1).lower(),
            chunk,
        ),
    )
    payload = _replace_outside_sql_strings(
        payload,
        lambda chunk: re.sub(
            rf"\b[A-Za-z0-9_$#]+\.(?={re.escape(context.object_name.lower())}\b)",
            "",
            chunk,
        ),
    )
    payload = re.sub(r"\s+(NON)?EDITIONABLE\b", "", payload, flags=re.IGNORECASE)
    payload = re.sub(
        r"\s+DEFAULT\s+COLLATION\s+\S+",
        "",
        payload,
        flags=re.IGNORECASE,
    )
    payload = re.sub(
        r"(CREATE\s+OR\s+REPLACE(?:\s+FORCE)?\s+VIEW\s+\S+)\s+\([^)]+\)\s+AS",
        r"\1 AS",
        payload,
        count=1,
        flags=re.IGNORECASE,
    )
    lines = [line.rstrip() for line in payload.rstrip().splitlines()]
    lines = _split_spec_from_body(lines, context)
    lines = _normalize_view_lines(lines, context)
    lines = _trim_trailing_blank_lines(lines)
    lines = _ensure_statement_semicolon(lines)
    if terminate:
        return _ensure_sql_terminator(lines)
    return lines


def _normalize_definition_line_only(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    if not lines:
        return lines

    normalized = [line.rstrip() for line in lines]
    normalized[0] = _normalize_definition_line(normalized[0], context)
    return normalized


def _normalize_definition_line(line: str, context: NormalizationContext) -> str:
    line = re.sub(r"\s+(NON)?EDITIONABLE\b", "", line, flags=re.IGNORECASE)
    line = re.sub(
        r"\s+DEFAULT\s+COLLATION\s+\S+",
        "",
        line,
        flags=re.IGNORECASE,
    )

    object_type_pattern = r"\s+".join(re.escape(part) for part in context.object_type.split())
    match = re.search(
        rf"\b{object_type_pattern}\s+"
        r"(?P<name>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)"
        r"|\"[^\"]+\"|[A-Za-z0-9_$#]+)",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return line

    name = _normalize_definition_name(match.group("name"))
    return f"{line[:match.start('name')]}{name}{line[match.end('name'):]}"


def _extract_definition_owner(payload: str, object_type: str) -> str | None:
    first_line = payload.splitlines()[0] if payload else ""
    object_type_pattern = r"\s+".join(re.escape(part) for part in object_type.upper().split())
    match = re.search(
        rf"\b{object_type_pattern}\s+"
        r"(?P<name>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+))",
        first_line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _identifier_key(match.group("name").split(".")[0])


def _normalize_definition_name(name: str) -> str:
    object_name = name.split(".")[-1]
    quoted_match = re.fullmatch(r'"([A-Z][A-Z0-9_$#]*)"', object_name)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", object_name):
        return object_name.lower()
    return object_name


def _replace_outside_sql_strings(
    payload: str,
    replace_chunk: Callable[[str], str],
) -> str:
    result: list[str] = []
    outside: list[str] = []
    index = 0

    while index < len(payload):
        char = payload[index]
        if char != "'":
            outside.append(char)
            index += 1
            continue

        if outside:
            result.append(replace_chunk("".join(outside)))
            outside = []

        quoted = [char]
        index += 1
        while index < len(payload):
            char = payload[index]
            quoted.append(char)
            index += 1
            if char == "'":
                if index < len(payload) and payload[index] == "'":
                    quoted.append(payload[index])
                    index += 1
                    continue
                break
        result.append("".join(quoted))

    if outside:
        result.append(replace_chunk("".join(outside)))
    return "".join(result)


def _ensure_sql_terminator(lines: list[str]) -> list[str]:
    if not lines:
        return lines
    if lines[-1].strip() == "/":
        return lines if len(lines) > 1 and lines[-2].strip() == "" else lines + [""]
    lines = _ensure_statement_semicolon(lines)
    return lines + ["/", ""]


def _ensure_statement_semicolon(lines: list[str]) -> list[str]:
    if not lines or lines[-1].strip() == "/":
        return lines
    if not lines[-1].rstrip().endswith(";"):
        lines[-1] = lines[-1].rstrip() + ";"
    return lines


def _uses_slash_terminator(object_type: str) -> bool:
    return object_type not in {"TABLE", "INDEX"}


def _trim_trailing_blank_lines(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


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
    distinct_match = re.match(r"(?P<distinct>distinct)\s+(?P<projection>.*)$", projection_source, flags=re.IGNORECASE)
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
            tail_lines = [projection_source[from_index:].lstrip(), *body_lines[absolute_index + 1 :]]
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


def _normalize_index(
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
    result = [
        f"{kind} IF NOT EXISTS {_normalize_sql_identifier(match.group('name'))}",
    ]
    table_name = _normalize_sql_identifier(match.group("table"))
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


def _strip_schema_prefix(name: str) -> str:
    return name.split(".")[-1].strip('"')


def _split_spec_from_body(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    if context.object_type not in {"PACKAGE", "TYPE"}:
        return lines
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("CREATE OR REPLACE") and " BODY" in line:
            return lines[:index]
    return lines


def _strip_generated_trigger_enable(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    if context.object_type != "TRIGGER":
        return lines
    return [
        line
        for line in lines
        if not re.match(r"\s*ALTER\s+TRIGGER\b.*\bENABLE\s*;\s*$", line, flags=re.IGNORECASE)
    ]


def _normalize_sequence(lines: list[str], context: NormalizationContext) -> list[str]:
    line = " ".join(lines)
    line = re.sub(r" START WITH \d+", "", line)
    for token in (
        " INCREMENT BY 1",
        " CACHE 20",
        " NOORDER",
        " NOCYCLE",
        " NOKEEP",
        " NOSCALE",
        " NOPARTITION",
        " GLOBAL",
    ):
        line = line.replace(token, "")
    line = re.sub(r"\s+MAXVALUE\s+9{28}(?!\d)", "", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line).replace(" ;", ";").strip()
    line = line.replace(" MINVALUE", "\n    MINVALUE")
    line = re.sub(r"\s+;", ";", line)
    return [
        f"-- DROP SEQUENCE {context.object_name.lower()};",
        line,
        "/",
        "",
    ]


def _normalize_table(lines: list[str], context: NormalizationContext) -> list[str]:
    payload = "\n".join(_normalize_definition_line_only(lines, context))
    table_name = context.object_name.lower()
    is_global_temporary = bool(
        re.search(r"CREATE\s+GLOBAL\s+TEMPORARY\s+TABLE\b", payload, flags=re.IGNORECASE)
    )
    open_index = payload.find("(")
    if open_index < 0:
        return lines

    close_index = _matching_parenthesis_index(payload, open_index)
    if close_index is None:
        return lines

    body = payload[open_index + 1:close_index]
    suffix = payload[close_index + 1:]
    items = _split_top_level_commas(body)
    folds, suffix = _collect_index_backed_constraints(suffix, context)
    formatted_items = _formatted_table_items_reordered(items, folds, context)

    create_header = (
        "CREATE GLOBAL TEMPORARY TABLE"
        if is_global_temporary
        else "CREATE TABLE IF NOT EXISTS"
    )
    result = [f"{create_header} {table_name} ("]
    for index, item_lines in enumerate(formatted_items):
        is_last = index == len(formatted_items) - 1
        lines_to_add = list(item_lines)
        if not is_last:
            lines_to_add[-1] += ","
        result.extend(lines_to_add)
    result.append(")")
    partition_lines = _format_table_suffix(suffix, context)
    if partition_lines and partition_lines[0] == ";":
        result[-1] += ";"
        result.extend(partition_lines[1:])
    elif partition_lines and partition_lines[0].startswith(") "):
        result[-1] = partition_lines[0]
        result.extend(partition_lines[1:])
    else:
        result.extend(partition_lines)
    return result


def build_table_fix_sql(payload: str, object_name: str) -> str | None:
    normalized_payload = payload.replace("\t", "    ").strip()
    context = NormalizationContext(
        object_type  = "TABLE",
        object_name  = object_name,
        object_owner = _extract_definition_owner(normalized_payload, "TABLE"),
    )
    open_index = normalized_payload.find("(")
    if open_index < 0:
        return None
    close_index = _matching_parenthesis_index(normalized_payload, open_index)
    if close_index is None:
        return None

    suffix = normalized_payload[close_index + 1:]
    folds, _ = _collect_index_backed_constraints(suffix, context)
    if not folds:
        return None

    ordered = sorted(
        folds,
        key=lambda fold: (
            _constraint_item_rank(fold.source_item),
            fold.constraint_name.casefold(),
        ),
    )
    table = object_name.lower()
    blocks = [
        "\n".join(
            [
                f"ALTER TABLE {table} DROP CONSTRAINT {fold.constraint_name};",
                "--",
                f"DROP INDEX {fold.index_name};",
                "--",
                f"ALTER TABLE {table}",
                f"    ADD CONSTRAINT {fold.constraint_name} "
                f"{fold.constraint_type} ({', '.join(fold.columns)});",
            ]
        )
        for fold in ordered
    ]
    return "\n--\n".join(blocks) + "\n"


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


# DBMS_METADATA runs with SQLTERMINATOR=FALSE, so the trailing CREATE INDEX /
# ALTER ... ADD CONSTRAINT statements arrive with NO semicolons between them.
# A new top-level statement is therefore recognized by its leading keyword at
# paren-depth 0 outside string literals, in addition to the historical `;`.
_DDL_STATEMENT_START_RE = re.compile(r"(?:CREATE|ALTER)\b", flags=re.IGNORECASE)


def _split_ddl_statements(text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    at_line_start = True

    def flush() -> None:
        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        current.clear()

    index = 0
    length = len(text)
    while index < length:
        char = text[index]

        if (
            at_line_start
            and depth == 0
            and not in_string
            and "".join(current).strip()
            and _DDL_STATEMENT_START_RE.match(text, index)
        ):
            flush()

        if char == "'":
            in_string = not in_string
            current.append(char)
            at_line_start = False
            index += 1
            continue

        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == ";" and depth == 0:
                flush()
                at_line_start = True
                index += 1
                continue

        current.append(char)
        if char == "\n":
            at_line_start = True
        elif not char.isspace():
            at_line_start = False
        index += 1

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


def _is_constraint_item(item_type: str) -> bool:
    return bool(
        item_type.startswith("CONSTRAINT ")
        or re.match(
            r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK)\b",
            item_type,
            flags=re.IGNORECASE,
        )
    )


def _constraint_item_rank(item_type: str) -> int:
    body = re.sub(
        r"^CONSTRAINT\s+\S+\s+", "", item_type.strip(), flags=re.IGNORECASE
    ).upper()
    if body.startswith("PRIMARY KEY"):
        return 0
    if body.startswith("UNIQUE"):
        return 1
    if body.startswith("FOREIGN KEY"):
        return 2
    return 3


def _constraint_item_name(item_type: str) -> str:
    match = re.match(
        r'^CONSTRAINT\s+("(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z0-9_$#]+))',
        item_type.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return (match.group("quoted") or match.group("bare")).casefold()


def _formatted_table_items_reordered(
    items: list[str],
    folds: list[_FoldedConstraint],
    context: NormalizationContext,
) -> list[list[str]]:
    columns: list[list[str]] = []
    constraints: list[tuple[int, str, int, list[str]]] = []
    for order_index, item in enumerate(items):
        item_type = re.sub(r"\s+", " ", item.strip())
        formatted = _format_table_item(item, context)
        if formatted is None:
            continue
        if _is_constraint_item(item_type):
            constraints.append(
                (
                    _constraint_item_rank(item_type),
                    _constraint_item_name(item_type),
                    order_index,
                    formatted,
                )
            )
        else:
            columns.append(formatted)

    base = len(items)
    for offset, fold in enumerate(folds):
        constraints.append(
            (
                _constraint_item_rank(fold.source_item),
                _constraint_item_name(fold.source_item),
                base + offset,
                _format_table_constraint(fold.source_item, context),
            )
        )

    constraints.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return columns + [entry[3] for entry in constraints]


def _matching_parenthesis_index(payload: str, open_index: int) -> int | None:
    depth = 0
    in_string = False
    index = open_index
    while index < len(payload):
        char = payload[index]
        if char == "'":
            in_string = not in_string
            if in_string and index + 1 < len(payload) and payload[index + 1] == "'":
                index += 1
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return None


def _split_top_level_commas(body: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    in_string = False
    for index, char in enumerate(body):
        if char == "'":
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(body[start:index].strip())
                start = index + 1
    items.append(body[start:].strip())
    return [item for item in items if item]


def _format_table_item(item: str, context: NormalizationContext) -> list[str] | None:
    if not item.strip():
        return None
    item_type = re.sub(r"\s+", " ", item.strip())
    if item_type.startswith("CONSTRAINT ") or re.match(
        r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK)\b",
        item_type,
        flags=re.IGNORECASE,
    ):
        return _format_table_constraint(item_type, context)

    item = _cleanup_table_item(item)
    if not item:
        return None
    return _format_table_column(item)


def _cleanup_table_item(item: str) -> str:
    item = re.sub(r"\s+", " ", item.replace("\n", " ")).strip()
    item = re.sub(
        r"\s+COLLATE\s+\"?USING_NLS_COMP\"?",
        "",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(r"\s+ENABLE\b", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+USING\s+INDEX\b.*", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+TABLESPACE\s+\S+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+MAXVALUE\s+9{10,}", "", item, flags=re.IGNORECASE)
    for token in (
        " MINVALUE 1",
        " INCREMENT BY 1",
        " CACHE 20",
        " NOORDER",
        " NOCYCLE",
        " NOKEEP",
        " NOSCALE",
    ):
        item = item.replace(token, "")
    item = item.replace("NUMBER(*,0)", "INTEGER")
    item = re.sub(r"\bNUMBER\(\*,0\)", "INTEGER", item, flags=re.IGNORECASE)
    item = re.sub(r"TIMESTAMP\s+\((\d+)\)", r"TIMESTAMP(\1)", item)
    item = re.sub(
        r"INTERVAL\s+DAY\s+\((\d+)\)\s+TO\s+SECOND\s+\((\d+)\)",
        r"INTERVAL DAY(\1) TO SECOND(\2)",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(
        r"INTERVAL\s+YEAR\s+\((\d+)\)\s+TO\s+MONTH",
        r"INTERVAL YEAR(\1) TO MONTH",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(r'(?<![A-Za-z0-9_$#])(?:"?SYS"?\.)?"?XMLTYPE"?(?![A-Za-z0-9_$#])', "XMLTYPE", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+START WITH 1\b", "", item)
    item = re.sub(
        r"\b[a-z][a-z0-9_$#]*\.([a-z][a-z0-9_$#]*\.nextval)",
        r"\1",
        item,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", item).strip()


def _format_table_column(item: str) -> list[str]:
    match = re.match(r"(?P<name>\S+)\s+(?P<body>.*)", item, flags=re.IGNORECASE)
    if not match:
        return [f"    {item}"]

    name = _normalize_sql_identifier(match.group("name"))
    data_type, extras = _split_column_data_type_and_extras(match.group("body"))
    data_type = data_type.upper()
    interval_qualifier = _interval_qualifier(data_type)
    if interval_qualifier is not None:
        data_type = "INTERVAL"
        extras = f"{interval_qualifier} {extras}".strip()
    if extras:
        return [f"    {name:<30}  {data_type:<21} {extras},".rstrip().rstrip(",")]
    return [f"    {name:<30}  {data_type}".rstrip()]


def _split_column_data_type_and_extras(body: str) -> tuple[str, str]:
    match = re.search(
        r"\s+(?=(?:DEFAULT|GENERATED|CONSTRAINT|NOT\s+NULL|NULL\b|PRIMARY\s+KEY|UNIQUE\b|REFERENCES\b|CHECK\b))",
        body,
        flags=re.IGNORECASE,
    )
    if not match:
        return body.strip(), ""
    return body[: match.start()].strip(), body[match.end():].strip()


def _interval_qualifier(data_type: str) -> str | None:
    match = re.fullmatch(r"INTERVAL (DAY\(\d+\) TO SECOND\(\d+\)|YEAR\(\d+\) TO MONTH)", data_type)
    if match:
        return match.group(1)
    return None


def _format_table_constraint(item: str, context: NormalizationContext) -> list[str]:
    item = _cleanup_constraint_item(item)
    named = re.match(r"CONSTRAINT\s+(?P<name>\S+)\s+(?P<body>.*)", item, flags=re.IGNORECASE)
    if named:
        name = _normalize_sql_identifier(named.group("name"), context)
        return _format_constraint_body(named.group("body"), name=name, context=context)
    return _format_constraint_body(item, name=None, context=context)


def _cleanup_constraint_item(item: str) -> str:
    item = re.sub(r"\s+", " ", item.replace("\n", " ")).strip()
    item = re.sub(r"\s+ENABLE\b", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+USING\s+INDEX\b.*", "", item, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", item).strip()


def _format_constraint_body(
    body: str,
    name: str | None,
    context: NormalizationContext,
) -> list[str]:
    prefix = ["    --"]
    if name:
        prefix.append(f"    CONSTRAINT {name}")

    check = _extract_parenthesized_clause(body, "CHECK")
    if check is not None:
        expression, suffix = check
        lines = prefix + _format_check_constraint(expression, named=bool(name))
        lines.extend(_format_constraint_suffix(suffix, context))
        return lines

    for constraint_type in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE"):
        parsed = _extract_parenthesized_clause(body, constraint_type)
        if parsed is None:
            continue
        columns, suffix = parsed
        lines = prefix
        lines.extend(_format_constraint_columns(constraint_type, columns, named=bool(name)))
        lines.extend(_format_constraint_suffix(suffix, context))
        return lines

    return prefix + [f"    {body}"]


def _extract_parenthesized_clause(body: str, keyword: str) -> tuple[str, str] | None:
    keyword_pattern = r"\s+".join(keyword.split())
    match = re.match(rf"{keyword_pattern}\s*\(", body, flags=re.IGNORECASE)
    if not match:
        return None
    open_index = body.find("(", match.start())
    close_index = _matching_parenthesis_index(body, open_index)
    if close_index is None:
        return None
    return body[open_index + 1 : close_index], body[close_index + 1 :].strip()


def _format_constraint_suffix(suffix: str, context: NormalizationContext) -> list[str]:
    if not suffix:
        return []
    reference = _format_references_clause(suffix, context)
    if reference is not None:
        return reference
    return [f"        {suffix}"]


def _format_references_clause(
    suffix: str,
    context: NormalizationContext,
) -> list[str] | None:
    match = re.match(
        r"REFERENCES\s+(?P<table>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)|\"[^\"]+\"|[A-Za-z0-9_$#]+)\s*\(",
        suffix,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    open_index = suffix.find("(", match.end("table"))
    close_index = _matching_parenthesis_index(suffix, open_index)
    if close_index is None:
        return None

    table_name = _normalize_sql_identifier(match.group("table"), context)
    columns = _constraint_column_names(suffix[open_index + 1 : close_index])
    tail = suffix[close_index + 1 :].strip()
    if len(columns) == 1:
        return [f"        REFERENCES {table_name} ({columns[0]}){f' {tail}' if tail else ''}"]
    return [
        f"        REFERENCES {table_name} (",
        *[
            f"            {column}{',' if index < len(columns) - 1 else ''}"
            for index, column in enumerate(columns)
        ],
        f"        ){f' {tail}' if tail else ''}",
    ]


def _format_check_constraint(expression: str, named: bool) -> list[str]:
    expression_line = _normalize_constraint_expression(expression)
    return [
        "        CHECK (" if named else "    CHECK (",
        f"            {expression_line}" if named else f"        {expression_line}",
        "        )" if named else "    )",
    ]


def _normalize_constraint_expression(expression: str) -> str:
    return _replace_outside_sql_strings(
        expression.strip(),
        lambda chunk: re.sub(r'"([A-Z][A-Z0-9_$#]*)"', r"\1", chunk),
    )


def _format_constraint_columns(
    constraint_type: str,
    columns: str,
    named: bool,
) -> list[str]:
    column_names = _constraint_column_names(columns)
    indent = "        " if named else "    "
    if len(column_names) == 1:
        return [f"{indent}{constraint_type} ({column_names[0]})"]
    return [
        f"{indent}{constraint_type} (",
        *[
            f"{indent}    {column}{',' if index < len(column_names) - 1 else ''}"
            for index, column in enumerate(column_names)
        ],
        f"{indent})",
    ]


def _constraint_column_names(columns: str) -> list[str]:
    return [_normalize_sql_identifier(column) for column in _split_top_level_commas(columns)]


def _format_table_suffix(suffix: str, context: NormalizationContext) -> list[str]:
    trailing_lines = _trailing_table_statements(suffix)
    cluster_match = re.search(
        r"\bCLUSTER\s+(?P<cluster>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if cluster_match:
        cluster = _format_cluster_clause(cluster_match.group("cluster"), context)
        return [f") CLUSTER {cluster};", *trailing_lines]

    if re.search(r"\bON\s+COMMIT\s+DELETE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT DELETE ROWS;", *trailing_lines]
    if re.search(r"\bON\s+COMMIT\s+PRESERVE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT PRESERVE ROWS;", *trailing_lines]
    if re.search(r"\bUSAGE\s+QUEUE\b", suffix, flags=re.IGNORECASE):
        return [") USAGE QUEUE;", *trailing_lines]

    inmemory_lines = _format_inmemory_suffix(suffix)
    if inmemory_lines:
        return [*inmemory_lines, *trailing_lines]

    match = re.search(
        r"PARTITION BY RANGE \(([^)]+)\)\s+INTERVAL\s+\((NUMTODSINTERVAL\([^)]+\))\)",
        suffix,
        flags=re.IGNORECASE,
    )
    if not match:
        return [";", *trailing_lines]

    column_name, interval = match.groups()
    partition_name = _extract_partition_name(suffix)
    return [
        f"PARTITION BY RANGE ({column_name}) INTERVAL({interval}) (",
        f"    PARTITION {partition_name} VALUES()",
        ");",
        *trailing_lines,
    ]


def _format_inmemory_suffix(suffix: str) -> list[str]:
    match = re.search(
        r"\bINMEMORY\b(?P<body>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    raw = suffix[match.start() : match.end()].strip()
    lines = [re.sub(r"\s+", " ", line.strip()) for line in raw.splitlines() if line.strip()]
    if lines:
        lines[-1] = lines[-1].rstrip(";") + ";"
    return lines


def _format_cluster_clause(cluster: str, context: NormalizationContext) -> str:
    cluster = re.sub(r"\s+", " ", cluster).strip()
    match = re.fullmatch(
        r"(?P<name>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)|"
        r"\"[^\"]+\"|[A-Za-z0-9_$#]+)\s*(?:\((?P<columns>.*)\))?",
        cluster,
        flags=re.IGNORECASE,
    )
    if not match:
        return cluster

    name = _normalize_sql_identifier(match.group("name"), context)
    columns = match.group("columns")
    if columns is None:
        return name
    return f"{name}({', '.join(_constraint_column_names(columns))})"


def _normalize_sql_identifier(
    name: str,
    context: NormalizationContext | None = None,
) -> str:
    name = name.strip()
    parts = name.split(".")
    if len(parts) > 1:
        owner = parts[0]
        object_name = ".".join(parts[1:])
        if context is not None and context.object_owner:
            normalized_name = _normalize_identifier_part(object_name)
            if _identifier_key(owner) == context.object_owner:
                return normalized_name
            return f"{_normalize_identifier_part(owner)}.{normalized_name}"
        name = object_name
    return _normalize_identifier_part(name)


def _normalize_identifier_part(identifier: str) -> str:
    identifier = identifier.strip()
    quoted_match = re.fullmatch(r'"([A-Z][A-Z0-9_$#]*)"', identifier)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", identifier):
        return identifier.lower()
    return identifier.strip('"')


def _trailing_table_statements(suffix: str) -> list[str]:
    match = re.search(r"\b(?:CREATE|ALTER)\b", suffix, flags=re.IGNORECASE)
    if not match:
        return []
    return [line.rstrip() for line in suffix[match.start():].strip().splitlines()]


def _extract_partition_name(suffix: str) -> str:
    match = re.search(r"\(\s*PARTITION\s+(\S+)", suffix, flags=re.IGNORECASE)
    if not match:
        return "p00"
    return match.group(1).strip('"').lower()


def _normalize_type(lines: list[str], context: NormalizationContext) -> list[str]:
    return _normalize_type_with_drop(lines, context, drop_clause="TYPE")


def _normalize_type_body(lines: list[str], context: NormalizationContext) -> list[str]:
    return _normalize_type_with_drop(lines, context, drop_clause="TYPE BODY")


def _normalize_type_with_drop(
    lines: list[str],
    context: NormalizationContext,
    *,
    drop_clause: str,
) -> list[str]:
    object_name = context.object_name.upper()
    return [
        "BEGIN",
        f"    DBMS_UTILITY.EXEC_DDL_STATEMENT('DROP {drop_clause} {object_name}');",
        "    DBMS_OUTPUT.PUT_LINE('--');",
        f"    DBMS_OUTPUT.PUT_LINE('-- DROP {drop_clause} {object_name}, DONE');",
        "    DBMS_OUTPUT.PUT_LINE('--');",
        "EXCEPTION",
        "WHEN OTHERS THEN",
        "    NULL;",
        "END;",
        "/",
        "--",
        *_ensure_sql_terminator(lines),
    ]


def _normalize_job(lines: list[str], context: NormalizationContext) -> list[str]:
    job_payload = _extract_scheduler_job_payload(lines)
    if not job_payload:
        return lines

    action_match = re.search(
        r"job_action\s*=>\s*'((?:''|[^'])*)'",
        job_payload,
        flags=re.IGNORECASE | re.DOTALL,
    )
    job_action = action_match.group(1).strip() if action_match else ""
    if action_match:
        job_payload = job_payload.replace(job_action, "{JOB_ACTION}")

    job_payload = re.sub(
        r"start_date=>TO_TIMESTAMP_TZ[^)]*[)]",
        "start_date=>SYSDATE",
        job_payload,
        flags=re.IGNORECASE,
    )
    job_payload = job_payload.replace("end_date=>NULL,", "")
    job_payload = job_payload.replace("job_class=>\'\"DEFAULT_JOB_CLASS\"\',", "")
    payload_lines = ["job_name=>in_job_name,"]
    payload_lines.extend(
        re.sub(
            r"\s*,\s*([a-z_]+)\s*=>\s*",
            r",\n\1=>",
            job_payload,
            flags=re.IGNORECASE,
        ).splitlines()
    )
    formatted_payload = "\n".join(_format_job_attribute(line) for line in payload_lines if line)
    formatted_payload = formatted_payload.replace("{JOB_ACTION}", job_action)

    return _job_template(
        job_name    = context.object_name.upper(),
        job_payload = formatted_payload,
    ).splitlines() + [""]


def _extract_scheduler_job_payload(lines: list[str]) -> str:
    cleaned = [
        line
        for line in lines
        if not (line.lstrip().startswith("sys.dbms_scheduler.set_attribute(") and "NLS_ENV" in line)
    ]
    for index, line in enumerate(cleaned):
        if line.startswith(");"):
            return "\n".join(cleaned[2:index])
    return ""


def _format_job_attribute(line: str) -> str:
    parts = line.split("=>")
    return f"        {parts[0]:<20}=> {'=>'.join(parts[1:])}"


def _job_template(job_name: str, job_payload: str) -> str:
    return f"""DECLARE
    in_job_name             CONSTANT VARCHAR2(128)  := '{job_name}';
    in_run_immediatelly     CONSTANT BOOLEAN        := FALSE;
BEGIN
    DBMS_OUTPUT.PUT_LINE('--');
    DBMS_OUTPUT.PUT_LINE('-- REPLACE JOB ' || UPPER(in_job_name));
    DBMS_OUTPUT.PUT_LINE('--');
    --
    BEGIN
        DBMS_SCHEDULER.DROP_JOB(in_job_name, TRUE);
    EXCEPTION
    WHEN OTHERS THEN
        NULL;
    END;
    --
    DBMS_SCHEDULER.CREATE_JOB (
{job_payload}
    );
    --
    DBMS_SCHEDULER.SET_ATTRIBUTE(in_job_name, 'JOB_PRIORITY', 3);
    DBMS_SCHEDULER.ENABLE(in_job_name);
    COMMIT;
    --
    IF in_run_immediatelly THEN
        DBMS_SCHEDULER.RUN_JOB(in_job_name);
        COMMIT;
    END IF;
END;
/"""


def _normalize_synonym(lines: list[str], context: NormalizationContext) -> list[str]:
    line = " ".join(lines)
    line = re.sub(
        r"\s+FOR\s+(?P<target>\S+)",
        lambda match: f"\n    FOR {_normalize_synonym_target(match.group('target'), context)}",
        line,
        count=1,
        flags=re.IGNORECASE,
    )
    return _ensure_sql_terminator(line.splitlines())


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
    return [_normalize_index_expression(item.strip()) for item in _split_top_level_commas(expression)]


def _normalize_index_expression(expression: str) -> str:
    return _replace_outside_sql_strings(
        expression,
        lambda chunk: re.sub(
            r'"([A-Z][A-Z0-9_$#]*)"',
            lambda match: match.group(1).lower(),
            chunk,
        ),
    )


def _normalize_synonym_target(target: str, context: NormalizationContext) -> str:
    target = target.rstrip(";")
    match = re.fullmatch(
        r"(?P<owner>\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?P<name>\"[^\"]+\"|[A-Za-z0-9_$#]+)"
        r"(?P<suffix>@[A-Za-z0-9_$#]+)?",
        target,
    )
    if not match:
        return _normalize_sql_identifier(target) + ";"

    owner = match.group("owner")
    name = match.group("name")
    suffix = match.group("suffix") or ""
    normalized_name = _normalize_sql_identifier(name)
    if context.object_owner and _identifier_key(owner) == context.object_owner:
        return normalized_name + suffix + ";"
    return f"{_normalize_sql_identifier(owner)}.{normalized_name}{suffix};"


def _identifier_key(identifier: str) -> str:
    return identifier.strip().strip('"').upper()
