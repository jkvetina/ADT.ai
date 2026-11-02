"""Set the opaque spans of a DDL item aside while regexes rewrite the rest (ADT #1094).

A string literal is the user's data and a quoted identifier is one token, and
neither takes part in a whitespace collapse, a keyword strip or a line split.
The rewrites in `table_items.py` and `job.py` ran whole-item regexes with no
span awareness, so `DEFAULT 'x  TABLESPACE y'` lost its tail and a scheduler
comment holding `, b=>c` had an attribute line break injected into it. Each
caller swaps the spans for numbered placeholders first, runs its rewrites, and
puts the spans back exactly where they stood.

The placeholder is a control character around a number, which no regex in
either caller matches: `\\s` does not cover it, `[a-z_]+` does not reach the
digits, and `str.splitlines` does not split on it.
"""

from __future__ import annotations

import re

from adt_ai.export_db.normalizers import sql_spans

#: One marker per span kind, so a caller can restore its quoted names before the
#: rewrites that read them and its literals only after the last rewrite.
_MARKERS = {"string": "\x00", "quoted": "\x01"}


def set_aside(text: str, kind: str) -> tuple[str, list[str]]:
    """``text`` with every ``kind`` span swapped for a placeholder, and the spans in order."""
    marker = _MARKERS[kind]
    kept: list[str] = []
    pieces: list[str] = []
    for span_kind, start, end in sql_spans(text, identifiers=True):
        if span_kind == kind:
            pieces.append(f"{marker}{len(kept)}{marker}")
            kept.append(text[start:end])
        else:
            pieces.append(text[start:end])
    return "".join(pieces), kept


def restore(text: str, kept: list[str], kind: str) -> str:
    """Every placeholder ``set_aside`` wrote for ``kind`` replaced by its span."""
    marker = re.escape(_MARKERS[kind])
    return re.sub(rf"{marker}(\d+){marker}", lambda match: kept[int(match.group(1))], text)
