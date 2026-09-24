"""How an exported file spells an identifier, and when its quotes may go.

DBMS_METADATA quotes every name. A quote may be dropped only where Oracle
resolves the bare name identically: an uppercase simple name that is not a
reserved word. `"createdAt"` unquoted is `CREATEDAT`, a different column;
`"COMMENT"` unquoted is a reserved word and a parse error; `"Order Id"` is not
one token. Old ADT only ever unquoted `"[A-Z0-9_$#]+"` names
(export_db.py:1037); the rewrite let every other shape through, one copy of the
rule per normalizer (ADT #923).

It sits beside `normalizers.py` rather than inside it because that file is held
under the 20 KB cap `tests/export_db/test_normalizer_structure.py` measures, and
it depends on nothing from it.
"""

from __future__ import annotations

import re

#: The Oracle SQL reserved words (SQL Language Reference 26ai, "Oracle SQL
#: Reserved Words"): the names Oracle refuses as nonquoted identifiers, so a
#: quoted one keeps its quotes.
RESERVED_WORDS = frozenset({
    "ACCESS", "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AUDIT", "BETWEEN",
    "BY", "CHAR", "CHECK", "CLUSTER", "COLUMN", "COLUMN_VALUE", "COMMENT", "COMPRESS",
    "CONNECT", "CREATE", "CURRENT", "DATE", "DECIMAL", "DEFAULT", "DELETE", "DESC",
    "DISTINCT", "DROP", "ELSE", "EXCLUSIVE", "EXISTS", "FILE", "FLOAT", "FOR", "FROM",
    "GRANT", "GROUP", "HAVING", "IDENTIFIED", "IMMEDIATE", "IN", "INCREMENT", "INDEX",
    "INITIAL", "INSERT", "INTEGER", "INTERSECT", "INTO", "IS", "LEVEL", "LIKE", "LOCK",
    "LONG", "MAXEXTENTS", "MINUS", "MLSLABEL", "MODE", "MODIFY", "NESTED_TABLE_ID",
    "NOAUDIT", "NOCOMPRESS", "NOT", "NOWAIT", "NULL", "NUMBER", "OF", "OFFLINE", "ON",
    "ONLINE", "OPTION", "OR", "ORDER", "PCTFREE", "PRIOR", "PUBLIC", "RAW", "RENAME",
    "RESOURCE", "REVOKE", "ROW", "ROWID", "ROWNUM", "ROWS", "SELECT", "SESSION", "SET",
    "SHARE", "SIZE", "SMALLINT", "START", "SUCCESSFUL", "SYNONYM", "SYSDATE", "TABLE",
    "THEN", "TO", "TRIGGER", "UID", "UNION", "UNIQUE", "UPDATE", "USER", "VALIDATE",
    "VALUES", "VARCHAR", "VARCHAR2", "VIEW", "WHENEVER", "WHERE", "WITH",
})

_SIMPLE_NAME = re.compile(r"[A-Z][A-Z0-9_$#]*")
_QUOTED_SIMPLE_NAME = re.compile(r'"([A-Z][A-Z0-9_$#]*)"')

#: A dot with an even number of double quotes after it, i.e. one outside every
#: quoted name: `"HR"."Ref.No"` splits at the first dot and never at the second.
_PART_SEPARATOR = re.compile(r'\.(?=(?:[^"]*"[^"]*")*[^"]*$)')


def identifier_key(identifier: str) -> str:
    """The case- and quote-insensitive key two spellings of one name share."""
    return identifier.strip().strip('"').upper()


def split_qualified_name(name: str) -> list[str]:
    """`owner.object` split at the dots between its parts, never inside a quoted one.

    `"Comm.Base"."X"` is one owner and one object, and a column named `"Ref.No"`
    is one name: a plain `name.split(".")` read them as three parts and two, and
    kept the wrong half of each.
    """
    return [part.strip() for part in _PART_SEPARATOR.split(name.strip())]


def normalize_identifier_part(identifier: str) -> str:
    """One name part lowercased and unquoted where that is safe, else verbatim."""
    identifier = identifier.strip()
    quoted = _QUOTED_SIMPLE_NAME.fullmatch(identifier)
    if quoted and quoted.group(1) not in RESERVED_WORDS:
        return quoted.group(1).lower()
    if _SIMPLE_NAME.fullmatch(identifier):
        return identifier.lower()
    return identifier


def unquote_simple_identifiers(code: str, *, lower: bool = True) -> str:
    """Every `"NAME"` in a code chunk unquoted where Oracle reads it identically.

    `lower=False` keeps the name's case, which is how a CHECK condition has always
    been written out; the quote rule is the same either way.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in RESERVED_WORDS:
            return match.group(0)
        return name.lower() if lower else name

    return _QUOTED_SIMPLE_NAME.sub(replace, code)
