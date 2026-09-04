from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType

from adt_ai.export_db.normalizer_clauses import owner_qualifier_stripper

# Moved to their own module when `#679` took this file past the 20 KB context
# cap; re-exported so every object normalizer keeps importing them from here.
from adt_ai.export_db.normalizer_context import NormalizationContext as NormalizationContext
from adt_ai.export_db.normalizer_context import Normalizer as Normalizer
from adt_ai.export_db.normalizer_context import qualified as qualified

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
    keep_owner: bool = False,
    keep_view_column_names: bool = False,
    object_display_name: str | None = None,
) -> str:
    registry = registry or NormalizerRegistry.builtin()
    normalized_payload = payload.replace("\t", "    ").strip()
    lines = normalized_payload.splitlines()
    context = NormalizationContext(
        object_type         = object_type.upper(),
        object_name         = object_name,
        object_owner        = _extract_definition_owner(normalized_payload, object_type),
        add_if_not_exists   = add_if_not_exists,
        keep_owner          = keep_owner,
        keep_view_column_names = keep_view_column_names,
        object_display_name = object_display_name,
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
    if not context.keep_owner:
        payload = _replace_outside_sql_strings(
            payload,
            lambda chunk: re.sub(
                rf"\b(?P<owner>[A-Za-z0-9_$#]+)\.(?={re.escape(context.object_name.lower())}\b)",
                owner_qualifier_stripper(context.object_owner),
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
    # A third spelling of the view column-list strip used to sit here (`#680`).
    # It could never fire: `VIEW` is a body-preserving type, so it returns above
    # without reaching this branch, and no other type carries a `CREATE OR
    # REPLACE FORCE VIEW` header for the pattern to match. Removing it leaves
    # `object_normalizers/view_columns.py` as the one reader, which is what lets
    # `keep_view_column_names` be honoured in one place rather than three.
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

    name = _normalize_definition_name(
        match.group("name"),
        keep_owner   = context.keep_owner,
        display_name = context.display_name,
    )
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

_QUALIFIED_DEFINITION_NAME = re.compile(
    r'^(?P<owner>"[^"]+"|[A-Za-z0-9_$#]+)\.(?P<object>"[^"]+"|[A-Za-z0-9_$#]+)$'
)

def _split_definition_name(name: str) -> tuple[str | None, str]:
    """Split ``owner.object`` without splitting a dot INSIDE a quoted name.

    ``"Comm.Base"."X"`` is one owner and one object, so the naive
    ``name.split(".")`` this replaced read it as three parts and kept the wrong
    half. The alternation here is the same one the definition-line regex uses.
    """
    match = _QUALIFIED_DEFINITION_NAME.fullmatch(name.strip())
    if not match:
        return None, name.strip()
    return match.group("owner"), match.group("object")

def _normalize_definition_name(
    name: str,
    keep_owner: bool = False,
    display_name: str | None = None,
) -> str:
    owner, object_name = _split_definition_name(name)
    normalized = _normalize_definition_name_part(object_name)
    # The OBJECT half follows the file; the owner never does. An owner is a
    # schema, not this file's name, and `keep_owner` exports are qualified
    # against the dictionary rather than against the working tree.
    #
    # The equality test is what keeps this a re-SPELLING and never a rename:
    # a quoted mixed-case identifier comes back from the part normalizer with
    # its quotes intact, matches nothing, and is left exactly as it was.
    if display_name and normalized.casefold() == display_name.casefold():
        normalized = display_name
    if keep_owner and owner is not None:
        return f"{_normalize_definition_name_part(owner)}.{normalized}"
    return normalized

def _normalize_definition_name_part(part: str) -> str:
    quoted_match = re.fullmatch(r'"([A-Z][A-Z0-9_$#]*)"', part)
    if quoted_match:
        return quoted_match.group(1).lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", part):
        return part.lower()
    return part

def sql_spans(payload: str, *, identifiers: bool = False) -> list[tuple[str, int, int]]:
    """Split SQL text into ``code``, ``string``, ``comment`` and ``quoted`` spans.

    Every scan and rewrite of DDL text goes through this, so a comment is never
    read as SQL: the apostrophe in ``don't`` is not a string delimiter and the
    parenthesis in ``-- (see spec)`` is not a parenthesis. Both used to flip the
    scanners' state and corrupt the rest of the object (ADT #299).

    ``identifiers=True`` adds the fourth span kind, a double-quoted identifier,
    and is the half this scanner was missing (ADT #474). Without it an apostrophe
    inside ``"IT'S"`` opens a string here and a ``--`` inside ``"A--B"`` opens a
    comment, which is the `#299` defect one character over, in the scanner the
    rule points at. It is OPT-IN because the callers that predate it rewrite
    identifiers on purpose: the view normalizer lowercases quoted select-list
    items, and reading them as opaque would stop it. A scanner asking where the
    SQL *structure* is, a paren, a top-level comma, a statement terminator, wants
    them opaque and says so.

    The identifier form is a plain toggle rather than an escape-aware read, which
    is what the walks this replaced did: ``"A""B"`` reads as two identifiers with
    an empty code span between them, and that is the same answer.
    """

    spans: list[tuple[str, int, int]] = []
    length = len(payload)
    index = 0
    start = 0
    q_closers = {"(": ")", "[": "]", "{": "}", "<": ">"}

    while index < length:
        if identifiers and payload[index] == '"':
            if start < index:
                spans.append(("code", start, index))
            closing = payload.find('"', index + 1)
            end = length if closing < 0 else closing + 1
            spans.append(("quoted", index, end))
            index = start = end
            continue

        if (
            payload[index] in "qQ"
            and index + 2 < length
            and payload[index + 1] == "'"
        ):
            if start < index:
                spans.append(("code", start, index))
            opener = payload[index + 2]
            closer = q_closers.get(opener, opener)
            closing = payload.find(closer + "'", index + 3)
            end = length if closing < 0 else closing + 2
            spans.append(("string", index, end))
            index = start = end
            continue

        if payload[index] == "'":
            if start < index:
                spans.append(("code", start, index))
            end = index + 1
            while end < length:
                if payload[end] == "'":
                    if end + 1 < length and payload[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            spans.append(("string", index, end))
            index = start = end
            continue

        if payload.startswith("--", index) or payload.startswith("/*", index):
            if start < index:
                spans.append(("code", start, index))
            if payload[index + 1] == "-":
                newline = payload.find("\n", index)
                end = length if newline < 0 else newline
            else:
                closing = payload.find("*/", index + 2)
                end = length if closing < 0 else closing + 2
            spans.append(("comment", index, end))
            index = start = end
            continue

        index += 1

    if start < length:
        spans.append(("code", start, length))
    return spans

def _replace_outside_sql_strings(
    payload: str,
    replace_chunk: Callable[[str], str],
) -> str:
    return "".join(
        replace_chunk(payload[start:end]) if kind == "code" else payload[start:end]
        for kind, start, end in sql_spans(payload)
    )

def _drop_create_wrap(lines: list[str], drop_statement: str, slash: bool = True) -> list[str]:
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
        *(_ensure_sql_terminator(lines) if slash else _ensure_statement_semicolon(lines) + [""]),
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

def _code_positions(payload: str, from_index: int = 0) -> Iterable[int]:
    for kind, start, end in sql_spans(payload):
        if kind != "code" or end <= from_index:
            continue
        yield from range(max(start, from_index), end)

def _matching_parenthesis_index(payload: str, open_index: int) -> int | None:
    depth = 0
    for index in _code_positions(payload, open_index):
        char = payload[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None

def _split_top_level_commas(body: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    for index in _code_positions(body):
        char = body[index]
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


def build_table_fix_sql(
    payload: str,
    object_name: str,
    object_display_name: str | None = None,
) -> str | None:
    from adt_ai.export_db.object_normalizers.table import (
        build_table_fix_sql as _build_table_fix_sql,
    )

    return _build_table_fix_sql(payload, object_name, object_display_name)
