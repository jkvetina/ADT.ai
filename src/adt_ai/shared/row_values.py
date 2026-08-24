"""Case-tolerant row lookup shared by export inventory and runners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def row_value(row: Mapping[str, Any], key: str) -> Any:
    """Return ``row[key]``, tolerating whatever case the driver spelled it in.

    Oracle drivers return column names in upper case, but some callers index
    rows with lower-case keys; this tolerates both without a second lookup
    table. Any OTHER spelling is resolved on the miss path only, which costs a
    fold over the row's keys and nothing at all on the hit.

    That last fallback arrived with ADT #474 row G, and it is why deleting
    `patch/deploy._row_get` was a widening here rather than a straight deletion.
    That function took a varargs key list and every call site passed one key in
    both cases, which this already answered; what it ALSO did, and the card's
    reading of it missed, was fold the row's own keys, so a driver handing back
    `Object_Type` resolved there and would have stopped resolving here.
    """
    if key in row:
        return row[key]
    lowered = key.lower()
    if lowered in row:
        return row[lowered]
    for candidate, value in row.items():
        if str(candidate).lower() == lowered:
            return value
    return None
