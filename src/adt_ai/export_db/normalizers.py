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
    lines = payload.replace("\t", "    ").strip().splitlines()
    context = NormalizationContext(object_type=object_type.upper(), object_name=object_name)
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
        lines[1] = lines[1].lstrip()
        lines = _expand_simple_view_select(lines)
    return lines


def _expand_simple_view_select(lines: list[str]) -> list[str]:
    if len(lines) < 2:
        return lines
    body_lines = [line for line in lines[1:] if line.strip() != "/"]
    body = " ".join(line.strip() for line in body_lines)
    match = re.fullmatch(
        r"select\s+(?P<columns>[A-Za-z0-9_$#,\s]+)\s+from\s+(?P<table>\S+);?",
        body,
        flags=re.IGNORECASE,
    )
    if not match:
        return lines

    columns = [column.strip().lower() for column in match.group("columns").split(",")]
    if not columns or any(not re.fullmatch(r"[a-z][a-z0-9_$#]*", column) for column in columns):
        return lines
    return [
        lines[0],
        "select",
        *[
            f"    {column}{',' if index < len(columns) - 1 else ''}"
            for index, column in enumerate(columns)
        ],
        f"from {match.group('table').rstrip(';')};",
    ]


def _normalize_index(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    lines = _normalize_definition_line_only(lines, context)
    lines = _trim_trailing_blank_lines(lines)
    return _ensure_statement_semicolon(lines) + [""]


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
    line = re.sub(r" MAXVALUE 9{10,}", "", line)
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
    formatted_items = [
        formatted
        for item in items
        if (formatted := _format_table_item(item))
    ]

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
    partition_lines = _format_table_suffix(suffix)
    if partition_lines and partition_lines[0] == ";":
        result[-1] += ";"
        result.extend(partition_lines[1:])
    elif partition_lines and partition_lines[0].startswith(") "):
        result[-1] = partition_lines[0]
        result.extend(partition_lines[1:])
    else:
        result.extend(partition_lines)
    return result


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


def _format_table_item(item: str) -> list[str] | None:
    if not item.strip():
        return None
    item_type = re.sub(r"\s+", " ", item.strip())
    if item_type.startswith("CONSTRAINT ") or re.match(
        r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK)\b",
        item_type,
        flags=re.IGNORECASE,
    ):
        return [f"    {line.strip()}".rstrip() for line in item.strip().splitlines()]

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
    item = re.sub(r"\b(?:\"?SYS\"?\.)?\"?XMLTYPE\"?\b", "XMLTYPE", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+START WITH 1\b", "", item)
    item = re.sub(
        r"\b[a-z][a-z0-9_$#]*\.([a-z][a-z0-9_$#]*\.nextval)",
        r"\1",
        item,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", item).strip()


def _format_table_column(item: str) -> list[str]:
    match = re.match(
        r"(?P<name>\S+)\s+(?P<data_type>"
        r"VARCHAR2\([^)]+\)|CHAR\([^)]+\)|RAW\([^)]+\)|NUMBER\([^)]+\)|"
        r"NUMBER|INTEGER|TIMESTAMP\(\d+\)|INTERVAL DAY\(\d+\) TO SECOND\(\d+\)|"
        r"DATE|BLOB|CLOB|LONG|XMLTYPE"
        r")(?P<extras>.*)",
        item,
        flags=re.IGNORECASE,
    )
    if not match:
        return [f"    {item}"]

    name = _normalize_sql_identifier(match.group("name"))
    data_type = match.group("data_type").upper()
    extras = match.group("extras").strip()
    if extras:
        return [f"    {name:<30}  {data_type:<21} {extras},".rstrip().rstrip(",")]
    return [f"    {name:<30}  {data_type}".rstrip()]


def _format_table_constraint(item: str) -> list[str]:
    item = _cleanup_constraint_references(item)
    unnamed = re.match(
        r"(PRIMARY KEY|FOREIGN KEY|UNIQUE)\s*\(([^)]+)\)(.*)",
        item,
        flags=re.IGNORECASE,
    )
    if unnamed:
        constraint_type, columns, suffix = unnamed.groups()
        lines = ["    --"]
        lines.extend(_format_constraint_columns(constraint_type.upper(), columns, named=False))
        suffix = suffix.strip()
        if suffix:
            lines.append(f"        {suffix}")
        return lines

    unnamed_check = re.match(r"CHECK\s*\((.*)\)(.*)", item, flags=re.IGNORECASE)
    if unnamed_check:
        expression, suffix = unnamed_check.groups()
        lines = ["    --", *_format_check_constraint(expression, named=False)]
        suffix = suffix.strip()
        if suffix:
            lines.append(f"        {suffix}")
        return lines

    named_check = re.match(r"CONSTRAINT\s+(\S+)\s+CHECK\s*\((.*)\)(.*)", item, flags=re.IGNORECASE)
    if named_check:
        name, expression, suffix = named_check.groups()
        lines = ["    --", f"    CONSTRAINT {name.lower()}"]
        lines.extend(_format_check_constraint(expression, named=True))
        suffix = suffix.strip()
        if suffix:
            lines.append(f"        {suffix}")
        return lines

    match = re.match(
        r"CONSTRAINT\s+(\S+)\s+(PRIMARY KEY|FOREIGN KEY|UNIQUE)\s*\(([^)]+)\)(.*)",
        item,
        flags=re.IGNORECASE,
    )
    if not match:
        return ["    --", f"    {item}"]

    name, constraint_type, columns, suffix = match.groups()
    lines = ["    --", f"    CONSTRAINT {name.lower()}"]
    lines.extend(_format_constraint_columns(constraint_type.upper(), columns, named=True))
    suffix = suffix.strip()
    if suffix:
        lines.append(f"        {suffix}")
    return lines


def _cleanup_constraint_references(item: str) -> str:
    item = re.sub(r"\s+REFERENCES\s+\S+\.", " REFERENCES ", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+ENABLE\b", "", item, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", item).strip()


def _format_check_constraint(expression: str, named: bool) -> list[str]:
    expression_line = expression.strip().lower()
    return [
        "        CHECK (" if named else "    CHECK (",
        f"            {expression_line}" if named else f"        {expression_line}",
        "        )" if named else "    )",
    ]


def _format_constraint_columns(
    constraint_type: str,
    columns: str,
    named: bool,
) -> list[str]:
    column_names = [column.strip().lower() for column in columns.split(",")]
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


def _format_table_suffix(suffix: str) -> list[str]:
    trailing_lines = _trailing_table_statements(suffix)
    cluster_match = re.search(
        r"\bCLUSTER\s+(?P<cluster>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if cluster_match:
        cluster = re.sub(r"\s+", " ", cluster_match.group("cluster")).strip()
        return [f") CLUSTER {cluster};", *trailing_lines]

    if re.search(r"\bON\s+COMMIT\s+DELETE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT DELETE ROWS;", *trailing_lines]
    if re.search(r"\bON\s+COMMIT\s+PRESERVE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT PRESERVE ROWS;", *trailing_lines]
    if re.search(r"\bUSAGE\s+QUEUE\b", suffix, flags=re.IGNORECASE):
        return [") USAGE QUEUE;", *trailing_lines]

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


def _normalize_sql_identifier(name: str) -> str:
    name = name.strip()
    qualified = name.split(".")[-1]
    quoted_match = re.fullmatch(r'"([A-Z][A-Z0-9_$#]*)"', qualified)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", qualified):
        return qualified.lower()
    return qualified.strip('"')


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
    object_name = context.object_name.upper()
    return [
        "BEGIN",
        f"    DBMS_UTILITY.EXEC_DDL_STATEMENT('DROP TYPE {object_name}');",
        "    DBMS_OUTPUT.PUT_LINE('--');",
        f"    DBMS_OUTPUT.PUT_LINE('-- DROP TYPE {object_name}, DONE');",
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
    del context
    line = " ".join(lines)
    line = re.sub(r"\s+FOR\s+", "\n    FOR ", line, count=1, flags=re.IGNORECASE)
    return _ensure_sql_terminator(line.splitlines())
