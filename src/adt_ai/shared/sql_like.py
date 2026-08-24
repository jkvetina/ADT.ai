from __future__ import annotations

import fnmatch
from typing import Any


def split_patterns(value: Any) -> list[str] | None:
    """A configured or typed pattern list, read one way (ADT #474).

    `export_db/config.py` and `export_data/runner.py` each carried a private
    `_split_patterns`, both reading the same `schema_export.<schema>.ignore` key
    out of the same config file, and they disagreed on two inputs. `['A,B']` was
    ONE pattern in `export_db` and TWO in `export_data`, so a comma inside a list
    item was a separator on one command and a literal on the other; and the empty
    case inverted, `''` and `[]` giving `[]` in `export_db` (a filter matching
    nothing) against `None` in `export_data` (no filter at all).

    Both points are settled the second way. **A comma is a separator wherever a
    pattern list is written**, which is what the CLI's own `-type A,B` form
    already means, so the two spellings a project might use cannot mean different
    things. **An empty value is no filter**, decided on the patterns produced
    rather than on the raw value, so a list of blanks and a blank string answer
    the same as an absent key: a project that leaves the key empty is saying it
    wants nothing filtered, not that it wants nothing to match.
    """
    if value is None:
        return None
    items = value if isinstance(value, list | tuple) else [value]
    patterns = [
        part.strip()
        for item in items
        for part in str(item).split(",")
        if part.strip()
    ]
    return patterns or None


def matches_sql_like(value: str, pattern: str) -> bool:
    """Case-insensitive SQL LIKE match: `%` any run, `_` any single char.

    This is the one client-side mirror of the LIKE semantics the database
    applies server-side; every prefix/ignore/type filter must go through it
    so a pattern filters identically in SQL and in Python.
    """
    return fnmatch.fnmatchcase(value.upper(), sql_like_to_fnmatch(pattern.upper()))


def sql_like_to_fnmatch(pattern: str) -> str:
    """Translate a SQL LIKE pattern to fnmatch syntax.

    fnmatch metacharacters (`*?[]`) in the input match literally, the
    pattern language is SQL LIKE, not glob. `\\` escapes the next character.
    """
    converted = []
    escaped = False
    for character in pattern:
        if escaped:
            converted.append(f"[{character}]" if character in "*?[]" else character)
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character == "%":
            converted.append("*")
        elif character == "_":
            converted.append("?")
        elif character in "*?[]":
            converted.append(f"[{character}]")
        else:
            converted.append(character)
    if escaped:
        converted.append("\\")
    return "".join(converted)
