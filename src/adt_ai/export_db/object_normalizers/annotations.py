"""Where a 23ai ``ANNOTATIONS (...)`` clause is, and what may not be done to it.

One reader for the whole export. A clause turns up in three unrelated places
(on a column inside a declared list, on the object after that list closes, and
on a table or index the raw normalizers write untouched), so "is this text an
annotation" was going to be asked from `view_columns`, `materialized_view` and
`normalizers` alike, which is how `tests/contracts/shared_readers.txt` describes
a question growing three answers.

It lives beside the object normalizers rather than in `normalizers.py` because
that module sits at its 20 KB context cap (`test_normalizer_structure.py`) and
the split is the repo's own answer to it.
"""

from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    _code_positions,
    _matching_parenthesis_index,
    _replace_outside_sql_strings,
)

# The keyword opening an annotation list, wherever it sits.
ANNOTATIONS_RE = re.compile(r"\bANNOTATIONS\b\s*\(", flags=re.IGNORECASE)

_SIMPLE_QUOTED_IDENTIFIER_RE = re.compile(r'"([A-Z][A-Z0-9_$#]*)"')


def annotation_clause_spans(payload: str) -> list[tuple[int, int]]:
    """`(start, end)` of every top-level ``ANNOTATIONS (...)`` clause in `payload`."""
    code = set(_code_positions(payload))
    spans: list[tuple[int, int]] = []
    for match in ANNOTATIONS_RE.finditer(payload):
        if match.start() not in code:
            continue
        if spans and match.start() < spans[-1][1]:
            continue
        close_index = _matching_parenthesis_index(payload, match.end() - 1)
        if close_index is not None:
            spans.append((match.start(), close_index + 1))
    return spans


def annotations_index(payload: str) -> int | None:
    """Index of the `ANNOTATIONS` keyword in `payload`, ignoring quoted text.

    Read through the span scan rather than off the bare pattern, so a column
    literally named `"MY ANNOTATIONS (X"` stays a name rather than a clause.
    """
    spans = annotation_clause_spans(payload)
    return spans[0][0] if spans else None


def carries_annotations(payload: str) -> bool:
    """Whether `payload` carries an annotation clause worth preserving."""
    return bool(annotation_clause_spans(payload))


def lower_simple_quoted_identifiers(payload: str) -> str:
    """`"NAME"` unquoted where Oracle resolves it identically, outside annotations.

    An annotation name is stored as written, and a reserved word is legal there
    ONLY double-quoted, so unquoting `"ORDER"` would turn a stored annotation
    into a file that no longer parses. TABLE and INDEX never meet this pass at
    all (`RAW_NORMALIZER_OBJECT_TYPES`) and keep their clauses verbatim; a
    MATERIALIZED VIEW is the one annotated type that runs through it, and it
    only began carrying annotation text once `#761` stopped dropping it.

    The carve-out is measured over the WHOLE payload rather than inside
    `_replace_outside_sql_strings`: an annotation clause is full of string
    literals, so per-chunk the clause arrives already cut into `ANNOTATIONS(x `
    and `, "Group" `, and its parentheses never balance.
    """
    pieces: list[str] = []
    cursor = 0
    for start, end in annotation_clause_spans(payload):
        pieces.append(_replace_outside_sql_strings(payload[cursor:start], _unquote_simple))
        pieces.append(payload[start:end])
        cursor = end
    pieces.append(_replace_outside_sql_strings(payload[cursor:], _unquote_simple))
    return "".join(pieces)


def _unquote_simple(chunk: str) -> str:
    return _SIMPLE_QUOTED_IDENTIFIER_RE.sub(lambda match: match.group(1).lower(), chunk)
