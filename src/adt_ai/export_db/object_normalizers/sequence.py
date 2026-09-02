from __future__ import annotations

import re

from adt_ai.export_db.normalizer_clauses import strip_default_clauses
from adt_ai.export_db.normalizers import NormalizationContext, qualified

_SEQUENCE_DEFAULTS = (
    r"INCREMENT\s+BY\s+1",
    r"CACHE\s+20",
    r"NOORDER",
    r"NOCYCLE",
    r"NOKEEP",
    r"NOSCALE",
    r"NOPARTITION",
    r"GLOBAL",
)


def normalize_sequence(lines: list[str], context: NormalizationContext) -> list[str]:
    line = " ".join(lines)
    line = re.sub(r" START WITH \d+", "", line)
    line = strip_default_clauses(line, _SEQUENCE_DEFAULTS)
    line = re.sub(r"\s+MAXVALUE\s+9{28}(?!\d)", "", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line).replace(" ;", ";").strip()
    line = line.replace(" MINVALUE", "\n    MINVALUE")
    line = re.sub(r"\s+;", ";", line)
    return [
        f"-- DROP SEQUENCE {qualified(context.object_name.lower(), context)};",
        line,
        "/",
        "",
    ]
