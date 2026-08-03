from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class NormalizationContext:
    object_type      : str
    object_name      : str
    object_owner     : str | None = None
    add_if_not_exists: bool = True

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
        from adt_ai.export_db.object_normalizers.index import normalize_index
        from adt_ai.export_db.object_normalizers.job import normalize_job
        from adt_ai.export_db.object_normalizers.materialized_view import (
            normalize_materialized_view,
            normalize_mview_log,
        )
        from adt_ai.export_db.object_normalizers.sequence import normalize_sequence
        from adt_ai.export_db.object_normalizers.synonym import normalize_synonym
        from adt_ai.export_db.object_normalizers.table import normalize_table
        from adt_ai.export_db.object_normalizers.trigger import normalize_trigger
        from adt_ai.export_db.object_normalizers.type import normalize_type, normalize_type_body
        from adt_ai.export_db.object_normalizers.view import normalize_view

        return cls(
            {
                "INDEX": normalize_index,
                "JOB": normalize_job,
                "MATERIALIZED VIEW": normalize_materialized_view,
                "MVIEW LOG": normalize_mview_log,
                "SEQUENCE": normalize_sequence,
                "SYNONYM": normalize_synonym,
                "TABLE": normalize_table,
                "TRIGGER": normalize_trigger,
                "TYPE": normalize_type,
                "TYPE BODY": normalize_type_body,
                "VIEW": normalize_view,
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
    add_if_not_exists: bool = True,
) -> str:
    registry = registry or NormalizerRegistry.builtin()
    normalized_payload = payload.replace("\t", "    ").strip()
    lines = normalized_payload.splitlines()
    context = NormalizationContext(
        object_type       = object_type.upper(),
        object_name       = object_name,
        object_owner      = _extract_definition_owner(normalized_payload, object_type),
        add_if_not_exists = add_if_not_exists,
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
        lines = _split_spec_from_body(lines, context)
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

def _drop_create_wrap(lines: list[str], drop_statement: str) -> list[str]:
    """Prefix a CREATE block with the old-ADT EXEC_DDL drop-before-create guard.

    Objects that cannot be replaced in place (types, materialized views and
    their logs) are dropped first inside an autonomous block that swallows
    errors, then recreated.
    """
    return [
        "BEGIN",
        f"    DBMS_UTILITY.EXEC_DDL_STATEMENT('{drop_statement}');",
        "    DBMS_OUTPUT.PUT_LINE('--');",
        f"    DBMS_OUTPUT.PUT_LINE('-- {drop_statement}, DONE');",
        "    DBMS_OUTPUT.PUT_LINE('--');",
        "EXCEPTION",
        "WHEN OTHERS THEN",
        "    NULL;",
        "END;",
        "/",
        "--",
        *_ensure_sql_terminator(lines),
    ]

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

def _constraint_column_names(columns: str) -> list[str]:
    return [_normalize_sql_identifier(column) for column in _split_top_level_commas(columns)]

def _normalize_identifier_part(identifier: str) -> str:
    identifier = identifier.strip()
    quoted_match = re.fullmatch(r'"([A-Z][A-Z0-9_$#]*)"', identifier)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", identifier):
        return identifier.lower()
    return identifier.strip('"')

def _identifier_key(identifier: str) -> str:
    return identifier.strip().strip('"').upper()


def build_table_fix_sql(payload: str, object_name: str) -> str | None:
    from adt_ai.export_db.object_normalizers.table import (
        build_table_fix_sql as _build_table_fix_sql,
    )

    return _build_table_fix_sql(payload, object_name)
