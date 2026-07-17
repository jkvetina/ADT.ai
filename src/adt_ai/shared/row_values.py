"""Case-tolerant row lookup shared by export inventory and runners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def row_value(row: Mapping[str, Any], key: str) -> Any:
    """Return ``row[key]``, falling back to the lowercase key.

    Oracle drivers return column names in upper case, but some callers index
    rows with lower-case keys; this tolerates both without a second lookup
    table.
    """
    return row.get(key) if key in row else row.get(key.lower())
