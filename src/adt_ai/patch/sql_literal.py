"""Escaping a value bound for a single-quoted PL/SQL literal (ADT #554, #670).

`harden.py::_literal` fixed this once already: `_first_name` hands back a
quoted identifier with its quotes stripped, so an object called `IT'S_PKG`
reached `:= 'IT'S';` and the generated block would not compile (`#554`). The
same shape was still open in `patch/signatures.py`'s `object_rows` and in
`patch/apex_drop.py`'s drop transport, both splicing an
`object_name`, `object_type` or `owner` into a `'...'` literal through
`.format()` with no escaping at all, an apostrophe in any of them breaks not
just that one line but the whole `BEGIN` block it sits inside (`#670`).

One function rather than three copies, so the rule cannot drift between the
places that need it. `harden.py`'s own `_literal` is out of this module's
reach (a different agent owns that file at the time this was written), so it
keeps its private copy of the identical rule; this is the "small shared spot"
for the callers that can reach it.
"""

from __future__ import annotations


def escape_literal(value: str) -> str:
    """A value going into a single-quoted PL/SQL literal, apostrophes doubled.

    SQL's own escape for a literal apostrophe is a second one, `''`, which is
    the one rule every caller here needs and the only one this function knows.
    """
    return value.replace("'", "''")


__all__ = ["escape_literal"]
