from __future__ import annotations

import re

from adt_ai.export_db.normalizer_clauses import strip_default_clauses
from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _drop_create_wrap,
    _ensure_statement_semicolon,
    _matching_parenthesis_index,
    _trim_trailing_blank_lines,
    qualified,
)
from adt_ai.export_db.object_normalizers.annotations import (
    annotation_clause_spans,
    carries_annotations,
)
from adt_ai.export_db.object_normalizers.view_columns import column_block

# Storage / physical / refresh-noise clauses DBMS_METADATA still emits for a
# materialized view even with PHYSICAL_PROPERTIES/SEGMENT_ATTRIBUTES suppressed.
# Old ADT keeps only the BUILD/REFRESH intent, so we allowlist those and drop
# everything else between the CREATE line and the AS query body.
#
# The query-options line joined the allowlist at ADT #923: DBMS_METADATA writes
# constraint trust, on-query computation, query rewrite and concurrent refresh on
# one line, and dropping it whole exported an MV built `USING TRUSTED
# CONSTRAINTS ... ENABLE QUERY REWRITE` as one with neither. Old ADT kept TRUSTED
# (export_db.py:847). Only the non-default options survive, see below.
_MVIEW_KEEP_OPTION = re.compile(
    r"^(BUILD|REFRESH|NEXT|START\s+WITH|USING\s+(?:ENFORCED|TRUSTED)\s+CONSTRAINTS"
    r"|(?:ENABLE|DISABLE)\s+(?:ON\s+QUERY\s+COMPUTATION|QUERY\s+REWRITE|CONCURRENT\s+REFRESH))\b",
    flags=re.IGNORECASE,
)
# The defaults on that line, which a replay produces without being asked.
_MVIEW_DEFAULT_OPTIONS = (
    r"USING\s+ENFORCED\s+CONSTRAINTS",
    r"DISABLE\s+ON\s+QUERY\s+COMPUTATION",
    r"DISABLE\s+QUERY\s+REWRITE",
    r"DISABLE\s+CONCURRENT\s+REFRESH",
)
_MVIEW_LOG_KEEP_OPTION = re.compile(
    r"^(WITH|INCLUDING|EXCLUDING)\b",
    flags=re.IGNORECASE,
)

# Both writers below pass this to `_drop_create_wrap`, so the CREATE half ends on its
# `;` and nothing follows it. Old ADT appended `/` to every type but TABLE and
# INDEX and the rewrite carried that over, which is fatal for exactly these two: a
# materialized view and its log are plain SQL, so SQLcl has already run the statement
# by the time it reaches the `/`, and the `/` re-runs the buffer against an object that
# cannot be created twice. Measured on SANDBOX 2026-09-04, a `patch -deploy` printed
# `Materialized view log ADT681_MASTER created.` and then ORA-12000, and rolled back.
# A VIEW or SYNONYM survives the same shape because `CREATE OR REPLACE` is idempotent,
# and a TYPE needs the `/` because its body is PL/SQL, so neither changes here.
_SLASH_AFTER_CREATE = False


def normalize_materialized_view(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    name = context.display_name
    text, columns = _split_mview_column_list("\n".join(lines), name)
    source = text.splitlines()
    header, body = _split_mview_header_body(source)
    header, annotations = _split_mview_annotations(header)

    create_line = f"CREATE MATERIALIZED VIEW {qualified(name, context)}"
    # An annotated column list is kept at either setting: the declared list is
    # the only place a column annotation can live, so dropping it is data loss
    # rather than the layout cleanup the default exists for (ADT #761).
    if columns is not None and (
        context.keep_view_column_names or carries_annotations(columns)
    ):
        kept = [f"{create_line} (", *column_block(columns), ")"]
    else:
        kept = [create_line]
    for line in header[1:]:
        stripped = line.strip()
        if not (stripped and _MVIEW_KEEP_OPTION.match(stripped)):
            continue
        stripped = strip_default_clauses(f" {stripped}", _MVIEW_DEFAULT_OPTIONS).strip()
        if stripped:
            kept.append(stripped)
    if annotations:
        kept.append(annotations)
    kept.append("AS")
    kept.extend(body)

    kept = _trim_trailing_blank_lines(kept)
    kept = _ensure_statement_semicolon(kept)
    return _drop_create_wrap(
        kept,
        f"DROP MATERIALIZED VIEW {qualified(context.object_name.upper(), context)}",
        slash = _SLASH_AFTER_CREATE,
    )


def normalize_mview_log(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    master = context.display_name
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
        slash = _SLASH_AFTER_CREATE,
    )


def _split_mview_column_list(text: str, name: str) -> tuple[str, str | None]:
    """`text` with the explicit ``(col, col, ...)`` list lifted out of it.

    Old ADT lets the column list be implied by the query, so the qualified
    column projection DBMS_METADATA emits is dropped by default. The list comes
    back as the second element so `keep_view_column_names` can re-render it;
    `None` means there was none to lift.
    """
    match = re.search(
        rf"MATERIALIZED\s+VIEW\s+{re.escape(name)}\s*\(",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return text, None
    open_index = match.end() - 1
    close_index = _matching_parenthesis_index(text, open_index)
    if close_index is None:
        return text, None
    return (
        text[:open_index].rstrip() + text[close_index + 1 :],
        text[open_index + 1 : close_index],
    )


def _split_mview_annotations(header: list[str]) -> tuple[list[str], str | None]:
    """`header` with its object-level ``ANNOTATIONS (...)`` clause lifted out.

    The header is rebuilt from the `_MVIEW_KEEP_OPTION` allowlist, so a clause
    that is not on it is dropped, which silently deleted every object-level
    annotation a materialized view carried, leaving a valid file and no
    documentation (ADT #761). The clause is matched by parentheses rather than
    by line because an annotation value may hold a newline, and DBMS_METADATA
    emits it verbatim.

    The column list is lifted before this runs, so the only `ANNOTATIONS` left
    in the header is the object's own. A clause whose parentheses never close
    is reported as no clause at all, so the header keeps its own text rather
    than losing a slice of the wrong length.
    """
    text = "\n".join(header)
    spans = annotation_clause_spans(text)
    if not spans:
        return header, None
    start, end = spans[0]
    return (text[:start].rstrip() + text[end:]).splitlines(), text[start:end].strip()


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
