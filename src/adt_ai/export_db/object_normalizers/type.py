from __future__ import annotations

from adt_ai.export_db.normalizers import NormalizationContext, _ensure_sql_terminator


def normalize_type(lines: list[str], context: NormalizationContext) -> list[str]:
    return _normalize_type_with_drop(lines, context, drop_clause="TYPE")


def normalize_type_body(lines: list[str], context: NormalizationContext) -> list[str]:
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
