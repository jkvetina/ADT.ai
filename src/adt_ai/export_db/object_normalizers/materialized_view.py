from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _drop_create_wrap,
    _ensure_statement_semicolon,
    _matching_parenthesis_index,
    _trim_trailing_blank_lines,
    qualified,
)

# Storage / physical / refresh-noise clauses DBMS_METADATA still emits for a
# materialized view even with PHYSICAL_PROPERTIES/SEGMENT_ATTRIBUTES suppressed.
# Old ADT keeps only the BUILD/REFRESH intent, so we allowlist those and drop
# everything else between the CREATE line and the AS query body.
_MVIEW_KEEP_OPTION = re.compile(
    r"^(BUILD|REFRESH|NEXT|START\s+WITH)\b",
    flags=re.IGNORECASE,
)
_MVIEW_LOG_KEEP_OPTION = re.compile(
    r"^(WITH|INCLUDING|EXCLUDING)\b",
    flags=re.IGNORECASE,
)


def normalize_materialized_view(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    name = context.object_name.lower()
    text = _strip_mview_column_list("\n".join(lines), name)
    source = text.splitlines()
    header, body = _split_mview_header_body(source)

    kept = [f"CREATE MATERIALIZED VIEW {qualified(name, context)}"]
    for line in header[1:]:
        stripped = line.strip()
        if stripped and _MVIEW_KEEP_OPTION.match(stripped):
            kept.append(stripped)
    kept.append("AS")
    kept.extend(body)

    kept = _trim_trailing_blank_lines(kept)
    kept = _ensure_statement_semicolon(kept)
    return _drop_create_wrap(
        kept,
        f"DROP MATERIALIZED VIEW {qualified(context.object_name.upper(), context)}",
    )


def normalize_mview_log(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    master = context.object_name.lower()
    source = "\n".join(lines).splitlines()

    kept = [f"CREATE MATERIALIZED VIEW LOG ON {qualified(master, context)}"]
    for line in source[1:]:
        stripped = line.strip()
        if stripped and _MVIEW_LOG_KEEP_OPTION.match(stripped):
            kept.append(stripped)

    kept = _trim_trailing_blank_lines(kept)
    kept = _ensure_statement_semicolon(kept)
    return _drop_create_wrap(
        kept,
        f"DROP MATERIALIZED VIEW LOG ON {qualified(context.object_name.upper(), context)}",
    )


def _strip_mview_column_list(text: str, name: str) -> str:
    """Remove the explicit ``(col, col, ...)`` list after the MV name.

    Old ADT lets the column list be implied by the query, so the qualified
    column projection DBMS_METADATA emits is dropped.
    """
    match = re.search(
        rf"MATERIALIZED\s+VIEW\s+{re.escape(name)}\s*\(",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return text
    open_index = match.end() - 1
    close_index = _matching_parenthesis_index(text, open_index)
    if close_index is None:
        return text
    return text[:open_index].rstrip() + text[close_index + 1 :]


def _split_mview_header_body(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split the MV DDL at the ``AS`` keyword that introduces the query body."""
    for index, line in enumerate(lines):
        if index == 0:
            continue
        match = re.match(r"AS\b(.*)", line.strip(), flags=re.IGNORECASE)
        if match:
            remainder = match.group(1).strip()
            body = [remainder] if remainder else []
            body.extend(lines[index + 1 :])
            return lines[:index], body
    return lines, []
