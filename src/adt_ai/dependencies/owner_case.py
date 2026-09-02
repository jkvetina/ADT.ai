"""One schema is one scope in the mirror, whatever case the caller typed.

An Oracle owner is an uppercase identifier, but ADT never reads it from the
dictionary: it comes from a `-schema` argument or a connection-file key, where
`app_owner` is as likely as `APP_OWNER`. The query side has always known that
(`store._owner_params` uppercases its filter). The write side did not, so
`record_refresh` and the `refresh_schema*` writers stored the caller's own
spelling, and refreshing one schema both ways produced two scopes holding two
complete copies of the same rows.

That is not a cosmetic split. `patch/files.py` names every install target
`schema.upper()`, so `patch -create`'s gate, its `Run:` hint and `#367`'s
auto-refresh all speak the uppercase name, while `patch/staleness.py` folded the
two refresh stamps onto one key and kept whichever the query returned last. On
`a client DEV environment`, 2026-08-20, that was the lowercase one, frozen two hours behind:
the gate read a stamp its own remedy could not advance, so the command refused
identically however many times it was run (ADT `#413`).

Two functions, and the split between them is the whole design.
:func:`normalize_owner` is what every writer calls from now on, so the case can
never diverge again. :func:`fold_owner_case` is for the mirrors that already
did: it runs on the refresh path only, keeps the scope whose stamp is newest,
and drops the other copy rather than merging it, because both copies describe
the same schema and the older one may still be carrying objects the newer
refresh already saw dropped.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from adt_ai.dependencies.queries import (
    REFRESH_DELETE_SCOPE,
    REFRESH_RENAME_SCOPE,
    REFRESH_SCHEMA_ROWS_QUERY,
    delete_owner_rows_query,
    distinct_owners_query,
    rename_owner_query,
)
from adt_ai.dependencies.schema import USER_TABLES

# The `refreshes` row of a schema scope carries its stamp and the clock it was
# read on together, so the two are dropped or renamed as one and can never end
# up naming two different scopes.
_SCHEMA_SCOPE = "schema"


def normalize_owner(owner: str) -> str:
    """The one spelling a schema is stored under.

    Trimmed as well as uppercased: a `-schema ' app_owner'` typo would otherwise
    open a third scope that no `.upper()` anywhere else could ever match.
    """
    return str(owner).strip().upper()


def owner_params(owners: Iterable[str] | None) -> list[str]:
    """A query mode's ``-schema`` filter as deduped, normalized owner params.

    Empty and whitespace entries (and ``None``) collapse to ``[]``, so an empty
    owner filter behaves exactly like an absent one. Here rather than in the
    store because it is the same question :func:`normalize_owner` answers, read
    from the other side.
    """
    params: list[str] = []
    for owner in owners or ():
        normalized = normalize_owner(owner)
        if normalized and normalized not in params:
            params.append(normalized)
    return params


def fold_owner_case(connection: Any) -> None:
    """Collapse a mirror's case variants of one schema into the uppercase scope.

    A no-op on a mirror that never saw two spellings, which after
    :func:`normalize_owner` is every mirror ADT writes, so the standing cost is
    the two `SELECT DISTINCT`s below.
    """
    stamps = _schema_stamps(connection)
    groups: dict[str, set[str]] = {}
    for scope in set(_mirror_owners(connection)) | set(stamps):
        groups.setdefault(normalize_owner(scope), set()).add(scope)

    for upper, spellings in groups.items():
        if spellings == {upper}:
            continue
        winner = _winner(spellings, stamps, upper)
        _drop(connection, spellings - {winner})
        _rename(connection, winner, upper)


def _winner(spellings: set[str], stamps: dict[str, str], upper: str) -> str:
    """The spelling whose refresh is current, ties going to the canonical one.

    Read off the `refreshes` stamps rather than guessed from the case: the newest
    refresh is the one that describes the schema, and on `a client DEV environment` that
    happened to be the uppercase row while on another project it would not be.
    A scope with no stamp at all has never completed a refresh, so it sorts
    below every scope that has.
    """
    return max(spellings, key=lambda scope: (stamps.get(scope, ""), scope == upper, scope))


def _mirror_owners(connection: Any) -> list[str]:
    owners: list[str] = []
    for table in USER_TABLES:
        rows = connection.execute(distinct_owners_query(table)).fetchall()
        owners.extend(str(row["OWNER"]) for row in rows)
    return owners


def _schema_stamps(connection: Any) -> dict[str, str]:
    """``scope spelling -> last refresh``, for the schema scopes only.

    App scopes share the `refreshes` table and are numeric ids, so they carry
    no case question and are never touched here. A scope that recorded a clock
    but never a stamp is present with an empty one, and sorts last.
    """
    rows = connection.execute(REFRESH_SCHEMA_ROWS_QUERY).fetchall()
    return {
        str(row["scope_name"]): str(row["refreshed_at"] or "")
        for row in rows
        if row["scope_name"]
    }


def _drop(connection: Any, losers: set[str]) -> None:
    """Remove the duplicate copy, mirror rows and refresh row together."""
    for scope in losers:
        with connection:
            for table in USER_TABLES:
                connection.execute(delete_owner_rows_query(table), (scope,))
            connection.execute(REFRESH_DELETE_SCOPE, (_SCHEMA_SCOPE, scope))


def _rename(connection: Any, winner: str, upper: str) -> None:
    """Move the surviving copy onto the canonical spelling.

    After :func:`_drop` there is nothing left to collide with, which matters:
    `OWNER` is part of every mirror table's primary key, so an update run before
    the delete would fail on exactly the mirrors this exists to heal.
    """
    if winner == upper:
        return
    with connection:
        for table in USER_TABLES:
            connection.execute(rename_owner_query(table), (upper, winner))
        connection.execute(REFRESH_RENAME_SCOPE, (upper, _SCHEMA_SCOPE, winner))


__all__ = ["fold_owner_case", "normalize_owner", "owner_params"]
