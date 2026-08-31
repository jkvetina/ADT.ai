from __future__ import annotations

import re

from adt_ai.export_db.normalizers import NormalizationContext, qualified


def normalize_sequence(lines: list[str], context: NormalizationContext) -> list[str]:
    line = " ".join(lines)
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
