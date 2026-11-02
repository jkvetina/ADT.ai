"""Validation for Oracle identifiers spliced into generated SQL.

ADT builds SQL by interpolating table, column and object names directly into
statement text, bind variables cannot stand in for identifiers. Those names
are normally read from the data dictionary or derived from versioned file
names, but validating them at the point of interpolation is cheap
defense-in-depth: an object created with a quoted, non-conforming name (or a
corrupted/hand-crafted value) is rejected instead of being able to break out of
its identifier position in the surrounding SQL.

Free-form fragments that are *meant* to carry SQL, column definitions in an
``ALTER TABLE`` and ``where`` conditions in the export config, are trusted
project content and are deliberately out of scope here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# Oracle unquoted identifier: a leading letter / _ / $ / # then up to 127 more
# of the same class plus digits (128-byte limit on 12.2+). Matched against the
# upper-cased value because generated SQL freely lower-cases names.
_IDENTIFIER = re.compile(r"[A-Z_$#][A-Z0-9_$#]{0,127}")


def is_identifier(value: str) -> bool:
    """Return True if ``value`` is a safe, unquoted Oracle identifier."""
    return bool(_IDENTIFIER.fullmatch(str(value).upper()))


def safe_identifier(value: str, *, role: str = "identifier") -> str:
    """Return ``value`` unchanged, or raise ``ValueError`` if it is unsafe."""
    if not is_identifier(value):
        raise ValueError(f"unsafe SQL {role}: {value!r}")
    return value


def safe_identifiers(values: Iterable[str], *, role: str = "identifier") -> list[str]:
    """Validate every name in ``values`` and return them as a list."""
    return [safe_identifier(value, role=role) for value in values]


def safe_qualified_identifier(value: str, *, role: str = "identifier") -> str:
    """Validate a name that may carry its owner: ``object`` or ``owner.object``.

    Generated DML names its table with the owner when ``keep_owner`` is set, and
    a bare ``safe_identifier`` rejects that outright because of the dot. Both
    halves are still validated individually, so the guard is exactly as strict
    as before on each part; only the single separating dot is new. Anything
    deeper (``a.b.c``) is not a name ADT ever writes and is rejected.
    """
    parts = str(value).split(".")
    if len(parts) > 2:
        raise ValueError(f"unsafe SQL {role}: {value!r}")
    for part in parts:
        safe_identifier(part, role=role)
    return value


#: What Oracle reads back unchanged without quotes: a letter first, upper case.
_ORDINARY = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}")
#: What a quoted name may hold here. Oracle allows more inside the quotes, but
#: nothing ADT has met needs it, and a quote, a space or a paren stays refused.
_QUOTABLE = re.compile(r"[A-Za-z0-9_$#]{1,128}")
#: Oracle SQL reserved words (SQL Language Reference, appendix "Oracle SQL
#: Reserved Words"). A column named one of them exists only because it was
#: created quoted, and naming it bare is a parse error (ORA-00936).
RESERVED_WORDS = frozenset({
    "ACCESS", "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AUDIT", "BETWEEN", "BY",
    "CHAR", "CHECK", "CLUSTER", "COLUMN", "COMMENT", "COMPRESS", "CONNECT", "CREATE",
    "CURRENT", "DATE", "DECIMAL", "DEFAULT", "DELETE", "DESC", "DISTINCT", "DROP", "ELSE",
    "EXCLUSIVE", "EXISTS", "FILE", "FLOAT", "FOR", "FROM", "GRANT", "GROUP", "HAVING",
    "IDENTIFIED", "IMMEDIATE", "IN", "INCREMENT", "INDEX", "INITIAL", "INSERT", "INTEGER",
    "INTERSECT", "INTO", "IS", "LEVEL", "LIKE", "LOCK", "LONG", "MAXEXTENTS", "MINUS",
    "MLSLABEL", "MODE", "MODIFY", "NOAUDIT", "NOCOMPRESS", "NOT", "NOWAIT", "NULL", "NUMBER",
    "OF", "OFFLINE", "ON", "ONLINE", "OPTION", "OR", "ORDER", "PCTFREE", "PRIOR", "PUBLIC",
    "RAW", "RENAME", "RESOURCE", "REVOKE", "ROW", "ROWID", "ROWNUM", "ROWS", "SELECT",
    "SESSION", "SET", "SHARE", "SIZE", "SMALLINT", "START", "SUCCESSFUL", "SYNONYM", "SYSDATE",
    "TABLE", "THEN", "TO", "TRIGGER", "UID", "UNION", "UNIQUE", "UPDATE", "USER", "VALIDATE",
    "VALUES", "VARCHAR", "VARCHAR2", "VIEW", "WHENEVER", "WHERE", "WITH",
})


def sql_name(value: str, *, role: str = "column name", lower: bool = False) -> str:
    """How generated SQL names one table or column read from the data dictionary (ADT #1096).

    The dictionary holds the exact name: upper case for an ordinary one, any
    case for one created quoted. An ordinary name renders as it always has,
    lower-cased when ``lower`` asks for the MERGE spelling. Anything Oracle
    would not read back unchanged (a reserved word, a leading digit or
    underscore, a lower-case letter) is double-quoted in its exact case;
    unquoted, it was a parse error or named a different column.
    """
    text = str(value)
    if not _QUOTABLE.fullmatch(text):
        raise ValueError(f"unsafe SQL {role}: {value!r}")
    if _ORDINARY.fullmatch(text) and text not in RESERVED_WORDS:
        return text.lower() if lower else text
    return f'"{text}"'


def column_type(types: Mapping[str, str] | None, column: str) -> str:
    """The Oracle type recorded for ``column`` in a name -> type map, or ``""``.

    The map is keyed by the dictionary's exact name (ADT #1111), so ``foo`` and
    a quoted ``FOO`` beside it keep their own types. An upper-cased key is the
    older spelling, read only when the exact one is absent.
    """
    if not types:
        return ""
    if column in types:
        return str(types[column]).strip().upper()
    return str(types.get(column.upper(), "")).strip().upper()


def sql_names(
    values: Iterable[str], *, role: str = "column name", lower: bool = False
) -> list[str]:
    """`sql_name` for every name in ``values``."""
    return [sql_name(value, role=role, lower=lower) for value in values]


def sql_qualified_name(value: str, *, role: str = "table name", lower: bool = False) -> str:
    """`sql_name` for a table that may carry its owner, ``owner.table`` under `keep_owner`.

    The owner comes from the connection's schema list, never the dictionary,
    so it keeps the old guard and is only lower-cased; the table is rendered.
    """
    parts = str(value).split(".")
    if len(parts) > 2:
        raise ValueError(f"unsafe SQL {role}: {value!r}")
    table = sql_name(parts[-1], role=role, lower=lower)
    if len(parts) == 1:
        return table
    owner = safe_identifier(parts[0], role=role)
    return f"{owner.lower() if lower else owner}.{table}"


def safe_object_type(value: str, *, role: str = "object type") -> str:
    """Validate a (possibly multi-word) object type, e.g. ``PACKAGE BODY``.

    Object types are one or more identifier words joined by single spaces
    (``MATERIALIZED VIEW``, ``TYPE BODY``); each word must be a safe
    identifier.
    """
    text = str(value)
    words = text.split(" ")
    if not text or any(not is_identifier(word) for word in words):
        raise ValueError(f"unsafe SQL {role}: {value!r}")
    return value
