from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnRef:
    """A column-level reference resolved from PL/Scope.

    ``unit`` is the referencing program unit node (``PACKAGE BODY.CORE``),
    ``relation`` the owning table/view node (``TABLE.CORE_LOGS``), and
    ``operations`` the enclosing SQL statement types (sorted, may be empty).
    """

    unit: str
    relation: str
    column: str
    operations: tuple[str, ...] = ()
