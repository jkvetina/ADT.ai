"""The CLI's object-type vocabulary: canonical Oracle type names and their aliases.

``-type`` names an Oracle object type, so Oracle's own spelling is the contract:
multi-word types carry a space (``PACKAGE BODY``, ``MATERIALIZED VIEW``), and a
bare ``PACKAGE`` is the specification — never the body. The filters match a type
with LIKE, so an unwildcarded pattern is already an exact match; this module's job
is only to resolve the spellings a user reasonably types (``MVIEW``, ``MATERIALIZED``,
``package_body``) onto that canonical vocabulary.

Shared rather than per-module because ``-type`` must mean the same thing on every
command that takes it.
"""

from __future__ import annotations

from collections.abc import Iterable

# PL/SQL object types that accept the PLSQL_* compilation flags + REUSE SETTINGS.
# These are also exactly the types whose stored source lives in ``user_source`` and
# round-trips faithfully through CREATE OR REPLACE, which is what -trailing needs.
PLSQL_OBJECT_TYPES = ("PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER")

# The object types ADT.ai knows about, spelled the way Oracle spells them. DATA and
# GRANT are ADT's own pseudo-types (they have no user_objects row); MVIEW LOG is
# ADT's name for a materialized view log, which Oracle stores as an MLOG$_ table.
ORACLE_OBJECT_TYPES = frozenset(
    {
        "DATA",
        "FUNCTION",
        "GRANT",
        "INDEX",
        "JOB",
        "MATERIALIZED VIEW",
        "MVIEW LOG",
        "PACKAGE",
        "PACKAGE BODY",
        "PROCEDURE",
        "SCHEDULE",
        "SEQUENCE",
        "SYNONYM",
        "TABLE",
        "TRIGGER",
        "TYPE",
        "TYPE BODY",
        "VIEW",
    }
)

# Spellings that name a canonical type without being one. Keys are matched against
# the whole token (never word-by-word), so MVIEW -> MATERIALIZED VIEW cannot corrupt
# MVIEW LOG into 'MATERIALIZED VIEW LOG'.
#
# SPEC is the counterpart of BODY: Oracle has no 'PACKAGE SPEC' type because the bare
# type name *is* the specification, but a reader who just typed `-type PACKAGE BODY`
# reasonably expects `-type PACKAGE SPEC` to work as its opposite.
OBJECT_TYPE_ALIASES = {
    "MVIEW": "MATERIALIZED VIEW",
    "MATERIALIZED": "MATERIALIZED VIEW",
    "MATERIALIZED VIEW LOG": "MVIEW LOG",
    "PACKAGE SPEC": "PACKAGE",
    "TYPE SPEC": "TYPE",
}

# The longest canonical/alias key in words, which bounds how far the joiner looks
# ahead. Derived, never hardcoded, so a longer alias cannot silently stop joining.
MAX_OBJECT_TYPE_WORDS = max(
    len(name.split()) for name in (*ORACLE_OBJECT_TYPES, *OBJECT_TYPE_ALIASES)
)


def normalize_object_type_pattern(pattern: str) -> str:
    """Resolve one ``-type`` token to its canonical Oracle type name.

    Resolution happens only when the token definitively names a type — via an alias,
    an underscore-for-space spelling, or any casing. Anything else is returned
    uppercased but otherwise untouched, so ``PACKAGE%`` stays the LIKE pattern the
    user meant and ``_`` keeps its single-char-wildcard meaning everywhere it is not
    simply how someone spelled a real type name.

    Uppercasing is not cosmetic: the database compares ``user_objects.object_type``
    raw, so a lowercase pattern would match nothing against a real Oracle DB while
    passing every client-side test.
    """
    token = pattern.strip().upper()
    if not token:
        return token
    # Oracle has no underscore in any object type name, so an underscore that lands
    # on a real type was someone spelling PACKAGE BODY without the space.
    candidate = token.replace("_", " ")
    aliased = OBJECT_TYPE_ALIASES.get(candidate)
    if aliased is not None:
        return aliased
    if candidate in ORACLE_OBJECT_TYPES:
        return candidate
    return token


def names_object_type(pattern: str) -> bool:
    """Whether the token definitively names a type, rather than being a LIKE pattern."""
    token = pattern.strip().upper().replace("_", " ")
    return token in OBJECT_TYPE_ALIASES or token in ORACLE_OBJECT_TYPES


def join_object_type_words(patterns: Iterable[str]) -> list[str]:
    """Rejoin tokens that a shell split out of one multi-word type name.

    ``-type`` is nargs="+", so an unquoted ``-type PACKAGE BODY`` arrives as two
    tokens. Left alone they become two independent filters and the run reports
    package *specifications* — the opposite of the request — because BODY names no
    type and quietly matches nothing. Joining makes the unquoted and quoted forms
    identical, so quoting never changes what a command means.

    Greedy longest-first, and only when the joined words name a real type: two real
    types side by side (``-type PACKAGE TRIGGER``) stay two filters, since 'PACKAGE
    TRIGGER' is not a type. ``-type MATERIALIZED VIEW`` is the one genuinely
    ambiguous input — it reads as the single type, not as MATERIALIZED VIEW + VIEW;
    write ``-type MVIEW VIEW`` for the pair.
    """
    tokens = list(patterns)
    joined: list[str] = []
    index = 0
    while index < len(tokens):
        for width in range(min(MAX_OBJECT_TYPE_WORDS, len(tokens) - index), 1, -1):
            candidate = " ".join(tokens[index : index + width])
            if names_object_type(candidate):
                joined.append(candidate)
                index += width
                break
        else:
            joined.append(tokens[index])
            index += 1
    return joined


def normalize_object_type_patterns(patterns: Iterable[str]) -> list[str]:
    """Rejoin shell-split type names, then resolve each to its canonical spelling.

    The join has to run first: ``('PACKAGE', 'BODY')`` only resolves correctly once
    it is one token. See :func:`join_object_type_words` and
    :func:`normalize_object_type_pattern`.
    """
    return [normalize_object_type_pattern(pattern) for pattern in join_object_type_words(patterns)]
