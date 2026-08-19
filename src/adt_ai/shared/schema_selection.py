"""Turning what the user typed into the schemas a connection file actually holds.

Split out of `shared/connections.py` when that file crossed the repo's 20 KB
context budget (ADT #395), on the `normalizers.py` precedent: split rather than
add a debt entry. Nothing about the behaviour moved, and these three functions
are the cohesive half, they answer "which configured schemas does this argument
name" and know nothing about credentials, wallets or files.
"""

from __future__ import annotations

import fnmatch
from typing import Any


def match_schema(schemas: Any, wanted: str) -> tuple[str, Any]:
    """Find ``wanted`` among the configured schema keys, exact match first.

    Returns the key as the file spells it alongside its data, so everything
    downstream reports the configured name rather than the caller's casing.
    """
    if not isinstance(schemas, dict):
        return wanted, None
    if wanted in schemas:
        return wanted, schemas[wanted]
    folded = str(wanted).casefold()
    for key, data in schemas.items():
        if str(key).casefold() == folded:
            return str(key), data
    return wanted, None


def split_schema_values(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list | tuple) else [value]
    schemas: list[str] = []
    for item in values:
        if isinstance(item, list | tuple):
            # Defensive against a -schema group list (action="append" + nargs="+")
            # reaching here unflattened: recurse rather than str()-ing the inner
            # list, which would yield "['DA', 'GSN']" and match no schema.
            schemas.extend(split_schema_values(item))
            continue
        schemas.extend(
            part.strip()
            for part in str(item).split(",")
            if part.strip()
        )
    return schemas


def expand_schema_patterns(patterns: list[str], available: list[str]) -> list[str]:
    schemas: list[str] = []
    for pattern in patterns:
        if pattern == "%":
            matches = available
        elif "%" in pattern or "*" in pattern:
            wildcard = pattern.upper().replace("%", "*")
            matches = [
                schema
                for schema in available
                if fnmatch.fnmatchcase(schema.upper(), wildcard)
            ]
        else:
            matches = [pattern]
        for schema in matches:
            if schema not in schemas:
                schemas.append(schema)
    return schemas
