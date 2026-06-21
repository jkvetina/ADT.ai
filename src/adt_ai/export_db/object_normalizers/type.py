from __future__ import annotations

from adt_ai.export_db.normalizers import NormalizationContext, _drop_create_wrap


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
    return _drop_create_wrap(lines, f"DROP {drop_clause} {object_name}")
