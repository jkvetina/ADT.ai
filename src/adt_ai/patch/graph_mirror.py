"""Reading `config/internal/dependencies.db` on behalf of the `patch` gates.

`patch/staleness.py` owns what to do with the answers; this owns getting them.
The split is the one `patch/clocks.py` already draws: policy stays in the gate,
and everything that is really "what does the mirror say" lives beside it. Every
function here fails soft, an absent or unreadable mirror yields ``None`` or an
empty mapping rather than raising, because the graph gate runs first and already
refuses that case with the message that names the fix.

**One schema can be in the mirror twice**, as `APP_OWNER` and as `app_owner`,
because ADT stored the caller's own `-schema` spelling until `#413`. Every read
below folds the two, and how it folds is the fix: by the NEWEST reading, never
by whichever row SQLite happened to return last. `dependencies/owner_case.py`
removes the duplicate on the next refresh; these are what the gates answer until
then.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from adt_ai.dependencies import queries as dependency_queries
from adt_ai.dependencies.store import DependencyStore
from adt_ai.patch.clocks import stamp_seconds
from adt_ai.shared.internal_paths import internal_path

GRAPH_FILE = "config/internal/dependencies.db"


def schema_meta(root: Path) -> dict[str, tuple[str | None, str | None]] | None:
    """``OWNER -> (last refresh, database UTC offset)``, None when unreadable.

    App stamps are dropped on purpose: ``-refresh -app 100`` says nothing about
    the database objects an install script orders, so it must never read as
    coverage for them.

    On two spellings of one owner the newest stamp wins, because an older copy
    is a leftover refresh and never evidence that the schema is behind. The old
    last-wins fold over `ORDER BY key` picked the LOWERCASE row every time, and
    that is the row `patch`'s own auto-refresh can never advance
    (`patch/files.py` names its targets `schema.upper()`), so `patch -create`
    refused identically however many times it was run (ADT `#413`). The offset
    travels with the stamp it was written beside, because the two describe one
    refresh.
    """
    db_path = internal_path(root, "dependencies.db")
    if not db_path.is_file():
        return None
    try:
        with DependencyStore.open(db_path) as store:
            rows = store.connection.execute(
                dependency_queries.REFRESH_SCHEMA_ROWS_QUERY
            ).fetchall()
    except sqlite3.Error:
        return None

    newest: dict[str, tuple[str | None, str | None]] = {}
    # Either half can be missing: a pre-`#394` refresh recorded a stamp and no
    # clock, and a mirror built by hand can do the reverse.
    for row in rows:
        owner = str(row["scope_name"]).upper()
        stamp = row["refreshed_at"] or None
        current = newest.get(owner)
        if current is None or (stamp_seconds(stamp or "") or 0) > (
            stamp_seconds(current[0] or "") or 0
        ):
            offset = row["db_utc_offset"] or (current or (None, None))[1]
            newest[owner] = (stamp, offset)
    return newest


def schema_stamps(root: Path) -> dict[str, str] | None:
    """``OWNER -> last refresh`` from ``refreshes``, or None when unreadable.

    A scope carrying a clock but no stamp has never completed a refresh, so it
    is absent here rather than present with a blank: absent is what the gate
    reads as "never refreshed", which is the truth.
    """
    meta = schema_meta(root)
    if meta is None:
        return None
    return {owner: stamp for owner, (stamp, _) in meta.items() if stamp}


def schema_offsets(root: Path) -> dict[str, str]:
    """``OWNER -> database UTC offset`` from the mirror, empty when absent."""
    meta = schema_meta(root) or {}
    return {owner: offset for owner, (_, offset) in meta.items() if offset}


def object_ddl_times(root: Path) -> dict[tuple[str, str, str], str]:
    """``(owner, type, name) -> LAST_DDL_TIME`` from the mirror, or empty.

    A pre-`#413` mirror holding one schema under two spellings offers the same
    object twice, and the reading kept is the LATEST of the two. The query
    carries no `ORDER BY`, so the old dict comprehension took whichever row
    SQLite returned last, and the gate this feeds exists to refuse a file the
    database has already moved past: of two readings of one object, the later
    one is the one that can still refuse.
    """
    db_path = root / GRAPH_FILE
    if not db_path.is_file():
        return {}
    try:
        with DependencyStore.open(db_path) as store:
            rows = store.connection.execute(dependency_queries.LAST_DDL_TIMES_QUERY).fetchall()
    except sqlite3.Error:
        return {}
    times: dict[tuple[str, str, str], str] = {}
    for row in rows:
        if not row["LAST_DDL_TIME"]:
            continue
        key = (
            str(row["OWNER"]).upper(),
            str(row["OBJECT_TYPE"]).upper(),
            str(row["OBJECT_NAME"]).upper(),
        )
        last_ddl = row["LAST_DDL_TIME"]
        if key not in times or str(last_ddl) > str(times[key]):
            times[key] = last_ddl
    return times


__all__ = ["GRAPH_FILE", "object_ddl_times", "schema_meta", "schema_offsets", "schema_stamps"]
