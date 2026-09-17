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
    # No `/` after the `;`. SQLcl runs a statement at its `;` and runs the buffer
    # again at a following `/`, so the file created the sequence and then failed
    # on `ORA-00955`, which rolled back every patch shipping a new sequence
    # (measured on SANDBOX, ADT #830). Old ADT appended the same `/` to every
    # type but TABLE and INDEX (export_db.py:458) and had the same failure.
    return [
        f"-- DROP SEQUENCE {qualified(context.display_name, context)};",
        line,
        "",
    ]
