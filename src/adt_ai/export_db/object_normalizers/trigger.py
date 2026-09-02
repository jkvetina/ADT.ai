from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _ensure_sql_terminator,
    _ensure_statement_semicolon,
    _trim_trailing_blank_lines,
    sql_spans,
)

_FOR_EACH_ROW  = re.compile(r"\bFOR\s+EACH\s+ROW\b", re.IGNORECASE)
_BODY_START    = re.compile(r"^\s*(DECLARE|BEGIN)\b", re.IGNORECASE)
_ALTER_ENABLE  = re.compile(r"^\s*ALTER\s+TRIGGER\b.*\bENABLE\s*;\s*$", re.IGNORECASE)
_ALTER_DISABLE = re.compile(r"^\s*ALTER\s+TRIGGER\b.*\bDISABLE\s*;\s*$", re.IGNORECASE)


def normalize_trigger(lines: list[str], context: NormalizationContext) -> list[str]:
    lines, disable = _take_generated_status_statements(lines)
    lines = _split_for_each_row(lines)
    lines = _trim_trailing_blank_lines(lines)
    lines = _ensure_statement_semicolon(lines)
    lines = _ensure_sql_terminator(lines)
    if disable:
        lines = _trim_trailing_blank_lines(lines) + ["", disable, ""]
    return lines

def _take_generated_status_statements(lines: list[str]) -> tuple[list[str], str | None]:
    """Lift the generated ALTER TRIGGER status statements out of the CREATE block.

    DBMS_METADATA appends the trigger's status as a second statement but leaves
    it *inside* the CREATE block, above the terminator. ENABLE is the default
    and is dropped outright; DISABLE carries real state, so it is returned to
    the caller and re-emitted below the terminator, where it is a statement of
    its own and the file actually runs.

    The match runs on the line's CODE spans only (ADT #652): the same words in a
    string literal or a comment are prose, and matching raw text made the body's
    own `-- ALTER TRIGGER ... DISABLE;` note disable the trigger on export.
    """
    kept: list[str] = []
    disable: str | None = None
    for line in lines:
        code = _code_only(line)
        if _ALTER_ENABLE.match(code):
            continue
        if _ALTER_DISABLE.match(code):
            disable = line.strip()
            continue
        kept.append(line)
    return kept, disable

def _code_only(line: str) -> str:
    """`line` with its string and comment spans blanked out, offsets preserved."""
    chars = list(line)
    for kind, start, end in sql_spans(line, identifiers=True):
        if kind == "code":
            continue
        chars[start:end] = " " * (end - start)
    return "".join(chars)

def _split_for_each_row(lines: list[str]) -> list[str]:
    """Put the trigger header's FOR EACH ROW clause on a line of its own.

    Oracle hands back the trigger header exactly as the developer typed it, so
    a one-line `... ON tab FOR EACH ROW` survives the export and every schema
    reads differently. Only the first match ahead of the PL/SQL body is
    rewritten: past DECLARE/BEGIN the same words are data, not a clause, and a
    header comment mentioning them is prose.
    """
    result: list[str] = []
    for index, line in enumerate(lines):
        if _BODY_START.match(line):
            return result + lines[index:]

        match = _FOR_EACH_ROW.search(line) if not _is_comment(line) else None
        if match is None:
            result.append(line)
            continue

        indent = line[: len(line) - len(line.lstrip())]
        head   = line[: match.start()].rstrip()
        tail   = line[match.end() :].strip()
        if head:
            result.append(head)
        result.append(f"{indent}{match.group(0)}")
        if tail:
            result.append(f"{indent}{tail}")
        return result + lines[index + 1 :]
    return result

def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("--")
