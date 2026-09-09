"""Which discovered rows survive `-type`, `-name`, `-prefix` and `-ignore`.

Split out of `inventory.py` when `#740` took that module past the 24 KB context
cap `tests/contracts/test_context_file_size.py` pins, the same move `#679` and
`#740` made for `normalizer_context.py` and `normalizer_plugins.py`. Every name
is re-exported from `inventory`, so nothing that imported one from there changes.

The seam is real rather than convenient: `inventory` asks the database what a
schema holds, and this file decides which of the answers the user asked for.
Nothing here touches a gateway, and every predicate is pure.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from adt_ai.shared.diff_tables import is_diff_table
from adt_ai.shared.sql_like import matches_sql_like


@dataclass(frozen=True)
class ObjectFilters:
    object_types : list[str] | None = None
    names        : list[str] | None = None
    prefix       : list[str] | None = None
    ignore       : list[str] | None = None

    def matches(self, object_type: str, object_name: str) -> bool:
        if is_old_adt_system_generated_object(object_name):
            return False
        if self.object_types and not matches_any(object_type, self.object_types):
            return False
        if self.names and not matches_any(object_name, self.names):
            return False
        if self.prefix and not matches_any(object_name, self.prefix):
            return False
        return not (self.ignore and matches_any(object_name, self.ignore))

    def matches_exact(self, object_type: str, object_name: str) -> bool:
        if is_old_adt_system_generated_object(object_name):
            return False
        if self.object_types and not matches_any(object_type, self.object_types):
            return False
        if self.names and object_name.upper() not in set(self.names):
            return False
        if self.prefix and not matches_any(object_name, self.prefix):
            return False
        return not (self.ignore and matches_any(object_name, self.ignore))

    def matches_index(self, index_name: str, table_name: str) -> bool:
        if (
            is_old_adt_system_generated_object(index_name)
            or is_old_adt_system_generated_index(index_name)
        ):
            return False
        if self.object_types and not matches_any("INDEX", self.object_types):
            return False
        if self.names and not matches_any_of([index_name, table_name], self.names):
            return False
        if self.prefix and not matches_any_of([index_name, table_name], self.prefix):
            return False
        return not (self.ignore and matches_any_of([index_name, table_name], self.ignore))


def normalize_list(values: Iterable[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [value.upper() for value in values]


def normalize_patterns(values: Iterable[str] | str | None) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return [value.strip().upper() for value in values.split(",") if value.strip()]
    return [value.upper() for value in values]


def query_pattern_list(values: Iterable[str] | None, default: str) -> str:
    if values is None:
        return default
    normalized = [str(value).upper() for value in values if str(value).strip()]
    return ",".join(normalized) if normalized else default


def has_exact_name_filter(names: Iterable[str] | None) -> bool:
    normalized = normalize_list(names) or []
    return bool(normalized) and all(not has_wildcard(name) for name in normalized)


def has_wildcard(pattern: str) -> bool:
    return any(character in pattern for character in "%*?")


def matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(matches_like(value, pattern) for pattern in patterns)


def matches_any_of(values: Iterable[str], patterns: Iterable[str]) -> bool:
    return any(matches_any(value, patterns) for value in values if value)


def matches_like(value: str, pattern: str) -> bool:
    return matches_sql_like(value, pattern)


def includes_object_type(object_type: str, object_types: Iterable[str] | None) -> bool:
    if object_types is None:
        return True
    return matches_any(object_type, object_types)


def user_object_types(object_types: Iterable[str] | None) -> list[str] | None:
    """The requested types that `user_objects` can actually answer for.

    The four excluded types each have their own discovery query, because
    `user_objects` either has no row for them or spells the row differently:
    an INDEX and a JOB are narrowed by their own views, an MVIEW LOG has no row
    of its own, and an ASSERTION's row is typed `UNDEFINED` (`#740`). Leaving
    one in the shared filter only spends a round trip that can never match.
    """
    if object_types is None:
        return None
    return [
        object_type
        for object_type in object_types
        if matches_like(object_type, "%")
        and object_type.upper() not in {"ASSERTION", "INDEX", "JOB", "MVIEW LOG"}
    ]


def is_old_adt_eligible_index(row: dict[str, Any]) -> bool:
    return (
        str(row.get("GENERATED") or "").upper() == "N"
        and str(row.get("CONSTRAINT_INDEX") or "").upper() == "NO"
        and not row.get("CONSTRAINT_NAME")
    )


def is_old_adt_system_generated_object(object_name: str) -> bool:
    name = object_name.upper()
    return (
        name.startswith("SYS_")
        or name.startswith("ISEQ$$_")
        or name.startswith("BIN$")
        or (name.startswith("ST") and name.endswith("="))
        # A SQLcl DIFF leftover is machine-made scaffolding like the rest of this
        # list, and it must never reach the repo (ADT #356). The sweep that drops
        # them is not enough on its own: an export reading the dictionary in the
        # same second would still write the file, and a committed `%$1` table is
        # permanent in a way the table itself is not.
        or is_diff_table(name)
    )


def is_old_adt_system_generated_index(object_name: str) -> bool:
    return fnmatch.fnmatchcase(object_name.upper(), "SYS*$$")
