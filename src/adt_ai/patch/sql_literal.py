"""Escaping a value bound for a single-quoted PL/SQL literal (ADT #554, #670).

`harden.py` fixed this once already: `_first_name` hands back a
quoted identifier with its quotes stripped, so an object called `IT'S_PKG`
reached `:= 'IT'S';` and the generated block would not compile (`#554`). The
same shape was still open in `patch/signatures.py`'s `object_rows` and in
`patch/apex_drop.py`'s drop transport, both splicing an
`object_name`, `object_type` or `owner` into a `'...'` literal through
`.format()` with no escaping at all, an apostrophe in any of them breaks not
just that one line but the whole `BEGIN` block it sits inside (`#670`).

One function rather than a copy per caller, so the rule cannot drift between
the places that need it. The private copies that outlived #670 (`harden.py`'s
`_literal`, the build-status and workspace slots in `apex_lock.py` and
`templates.py`, the patch code in `snapshots.py`) route through here since
ADT #923.
"""

from __future__ import annotations


def escape_literal(value: str) -> str:
    """A value going into a single-quoted PL/SQL literal, apostrophes doubled.

    SQL's own escape for a literal apostrophe is a second one, `''`, which is
    the one rule every caller here needs and the only one this function knows.
    """
    return value.replace("'", "''")


__all__ = ["escape_literal"]
