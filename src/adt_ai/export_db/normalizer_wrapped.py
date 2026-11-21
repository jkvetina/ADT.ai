"""Whether a PL/SQL unit's DDL is wrapped (ADT #1121, old ADT PR 4).

A wrapped unit is a clear header ending in `wrapped` and then encoded text that
opens on the format line `a000000`. Everything after the header is opaque: the
terminator fix every other unit gets would append `;` to the last encoded line,
a byte inside the payload, so the file would no longer match the database.

The format line is what makes the answer safe. Old ADT read the header alone,
so a unit NAMED `wrapped` (`CREATE OR REPLACE PROCEDURE wrapped` on a line of its
own) read as wrapped and lost its terminator; requiring `a000000` straight after
the `wrapped` word rules that out, because clear source never carries it there.
"""

from __future__ import annotations

import re

FORMAT_LINE = "a000000"

_HEADER_ENDS_WRAPPED = re.compile(r"\swrapped$", re.IGNORECASE)


def is_wrapped_unit(lines: list[str]) -> bool:
    """True when `lines` hold one wrapped unit, its CREATE header first.

    Two spellings, both from old ADT PR 4: `wrapped` closing the header line, or
    `wrapped` alone on the line after it. Either way the format line follows.
    """
    head = [line.strip() for line in lines[:3]]
    if len(head) >= 2 and _HEADER_ENDS_WRAPPED.search(head[0]) and head[1] == FORMAT_LINE:
        return True
    return len(head) == 3 and head[1].lower() == "wrapped" and head[2] == FORMAT_LINE
