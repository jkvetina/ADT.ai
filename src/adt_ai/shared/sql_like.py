from __future__ import annotations

import fnmatch


def matches_sql_like(value: str, pattern: str) -> bool:
    """Case-insensitive SQL LIKE match: `%` any run, `_` any single char.

    This is the one client-side mirror of the LIKE semantics the database
    applies server-side; every prefix/ignore/type filter must go through it
    so a pattern filters identically in SQL and in Python.
    """
    return fnmatch.fnmatchcase(value.upper(), sql_like_to_fnmatch(pattern.upper()))


def sql_like_to_fnmatch(pattern: str) -> str:
    """Translate a SQL LIKE pattern to fnmatch syntax.

    fnmatch metacharacters (`*?[]`) in the input match literally — the
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
