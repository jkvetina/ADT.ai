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
from collections.abc import Iterable

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
