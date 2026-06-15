from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _ensure_sql_terminator,
    _identifier_key,
    _normalize_sql_identifier,
)


def normalize_synonym(lines: list[str], context: NormalizationContext) -> list[str]:
    line = " ".join(lines)
    line = re.sub(
        r"\s+FOR\s+(?P<target>\S+)",
        lambda match: f"\n    FOR {_normalize_synonym_target(match.group('target'), context)}",
        line,
        count=1,
        flags=re.IGNORECASE,
    )
    return _ensure_sql_terminator(line.splitlines())


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
