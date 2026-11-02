from __future__ import annotations

from adt_ai.export_db.normalizers import NormalizationContext


def normalize_program(lines: list[str], context: NormalizationContext) -> list[str]:
    """A scheduler program's file is written exactly as `program_ddl.py` rendered it (`#1100`).

    Registered as a raw type, so the common pass never meets it either; that
    pass lower-cases quoted names, and the program's own name can be one.
    """
    return lines
