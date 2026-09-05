"""The two halves of `export_data`'s formula guard: neutralize out, restore in.

A cell whose first character is `=`, `+`, `-` or `@` is a formula to Excel,
Numbers and LibreOffice, so a row in an application's table becomes code the
moment somebody opens the export (`#707`). The neutralized form is the one every
spreadsheet already understands: a leading apostrophe, which those programs read
as "the rest of this cell is text" and strip on display.

**Quoting is not the fix, and it was never missing.** The writer runs under
`csv.QUOTE_NONNUMERIC`, so every text cell has been wrapped in `"` since the
module was written; a spreadsheet unquotes the field first and reads the first
character afterwards, which is why `"=cmd|'/C calc'!A0"` still evaluates.

**The apostrophe is escaped too, and that is what makes the pair lossless.**
`export_data` writes the CSV and the MERGE that reloads it, so a prefix the
reader does not strip would put a character in the database that the table never
held. Stripping alone is not enough either: without escaping, a stored `'=x` and
a stored `=x` would write the same cell and the reader could not tell which one
to replay. So the prefix is added whenever the first character that is not an
apostrophe is a formula lead, and exactly one apostrophe is taken back off when
one stands in front of such a run. `restored(neutralized(value)) == value` for
every string, which `tests/export_data/test_csv_formula_injection.py` pins.

**Only `str` cells pass through here.** A NUMBER is written unquoted and a
spreadsheet reads `-5` as the number it is, so prefixing it would break the type
to answer a threat that is not there; a DATE, TIMESTAMP or INTERVAL renders from
a typed Oracle value whose text form the driver produces, not the row. Text is
the column class that carries what somebody typed, and it is the one guarded.
"""

from __future__ import annotations

import re

#: A run of apostrophes, then the character a spreadsheet reads as "formula".
#: `-` sits last inside the class so it is a literal rather than a range.
_FORMULA_LEAD = re.compile(r"^'*[=+@-]")


def neutralized(text: str) -> str:
    """`text` as it is written to the CSV: prefixed when a spreadsheet would run it."""
    return f"'{text}" if _FORMULA_LEAD.match(text) else text


def restored(text: str) -> str:
    """One CSV cell as the database held it, undoing exactly what `neutralized` added.

    A cell with no prefix comes back untouched, which is what lets a CSV exported
    before `#707` replay the value it already carries instead of being read as a
    neutralized cell that lost its apostrophe.
    """
    if text.startswith("'") and _FORMULA_LEAD.match(text[1:]):
        return text[1:]
    return text
