"""The one way ADT.ai opens a local SQLite store (ADT #642).

Five stores, and until this module five openers, no two alike: each its own
copy of connect, folder creation, row factory, the foreign-key pragma and the
close-on-failure guard, and each its own idea of a version. `commits/<branch>.db`
kept it in a table called `meta`, `apex.db` and `dependencies.db` in `_meta`,
`flow.db` read the declared type of one column to decide whether to rebuild, and
`ut.db` probed for a column. Jan's reading of it, 2026-09-01: *"like it was build
by 1 professional and not 5 drunk lazy guys."*

:func:`open_store` is the professional. It connects, creates the folder, sets
the row factory, turns foreign keys on, lifts the file to the version the store
ships through the store's own :class:`Migration` steps, runs the schema, stamps
`_meta.schema_version`, and closes the connection when any of that raises. A
file it cannot lift is refused with :class:`StoreVersionError` rather than
guessed at, unless the store hands over a ``reset`` for the case, which is the
refresh path of a cache that is cheaper to rebuild than to migrate.

**The migrations are the store's, not this module's.** Each store knows its own
history; the opener only knows how to walk one: from the version the file
carries (``None`` for a file written before the store had one) step by step to
the version the code declares. A step is applied before the schema script runs,
so a rename can drop or move the old shape and let the script create the new.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.shared.queries import sqlite_store as queries

#: The path SQLite reads as "no file at all".
MEMORY = ":memory:"

#: How long a connection waits for a writer holding the file, in seconds.
#:
#: The driver's own default is five, which ordinary work reaches: a `rebuild`
#: writing a branch's commits takes longer than that, and a second command
#: reading the same store during it got `sqlite3.OperationalError: database is
#: locked` -- a message that reads as a corrupt store rather than as a queue
#: (ADT #670). Thirty says "wait for the other command to finish" while still
#: ending, so a genuinely stuck lock is still reported rather than waited on
#: forever.
#:
#: WAL would remove most of the contention and is deliberately NOT enabled: the
#: stores live under `config/` and `.adt/` INSIDE the user's own repository, and
#: WAL leaves `-wal`/`-shm` sidecars beside every one of them. Litter in a
#: working tree that is not ours to litter, in exchange for a wait nobody
#: notices.
BUSY_TIMEOUT_SECONDS = 30.0

#: A store version's spelling everywhere: the value of the `_meta` row.
Version = str | None


class StoreVersionError(sqlite3.DatabaseError):
    """A store file this ADT.ai has no migration for.

    A subclass of the driver's own error on purpose: every reader that fails
    soft on an unreadable store already catches ``sqlite3.Error``, and an
    unrecognised version is one more way for a file to be unreadable.
    """


@dataclass(frozen=True)
class Migration:
    """One step of a store's history: lifts a file at ``source`` to ``target``.

    ``source`` is ``None`` for a file written before the store carried a
    version at all. ``apply`` runs against the open connection, before the
    schema script, and may assume the file is exactly at ``source``.
    """

    source: Version
    target: str
    apply: Callable[[sqlite3.Connection], object]

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise ValueError(f"a migration cannot lift {self.source!r} to itself")


def open_store(
    db_path: str | Path,
    *,
    schema: str,
    version: str,
    migrations: Iterable[Migration] = (),
    row_factory: Any = sqlite3.Row,
    reset: Callable[[sqlite3.Connection], None] | None = None,
) -> sqlite3.Connection:
    """Open ``db_path`` at ``version``, creating, lifting or refusing it.

    ``schema`` is the store's idempotent DDL (``CREATE ... IF NOT EXISTS``),
    run after the migrations so a step that dropped the old shape gets the new
    one created behind it. ``reset`` is called, when given, on a file whose
    version no migration starts from; it wipes and the schema recreates. With
    no ``reset`` such a file raises :class:`StoreVersionError`.

    Closed on failure, since ADT #510: ``sqlite3.connect`` succeeds against any
    readable path and only the first statement learns the bytes are not a
    database, so a raise here would otherwise leave the connection open and
    unreferenced for the collector to report later, against whatever test was
    running by then.
    """
    connection = _connect(db_path)
    try:
        connection.row_factory = row_factory
        connection.execute(queries.FOREIGN_KEYS_ON)
        _lift(connection, version, tuple(migrations), reset)
        connection.executescript(schema)
        connection.executescript(queries.META_TABLE_DDL)
        connection.execute(queries.META_VERSION_UPSERT, (version,))
        connection.commit()
    except BaseException:
        connection.close()
        raise
    return connection


def stored_version(connection: sqlite3.Connection) -> Version:
    """The version the file carries, or ``None`` when it carries none."""
    if not has_table(connection, "_meta"):
        return None
    value = _scalar(connection, queries.META_VERSION_QUERY)
    return None if value is None else str(value)


def has_table(connection: sqlite3.Connection, name: str) -> bool:
    return _scalar(connection, queries.TABLE_EXISTS_QUERY, (name,)) is not None


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # The timeout is spelled on both branches rather than on the one that can
    # contend: one opener means one reading, and a second spelling here is how
    # the five copies this module replaced drifted apart in the first place.
    if isinstance(db_path, str) and db_path == MEMORY:
        return sqlite3.connect(MEMORY, timeout=BUSY_TIMEOUT_SECONDS)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SECONDS)


def _lift(
    connection: sqlite3.Connection,
    version: str,
    migrations: tuple[Migration, ...],
    reset: Callable[[sqlite3.Connection], None] | None,
) -> None:
    if _scalar(connection, queries.TABLE_COUNT_QUERY) == 0:
        return  # a fresh file: nothing to lift, the schema creates it
    steps = {step.source: step for step in migrations}
    stored = stored_version(connection)
    while stored != version:
        step = steps.get(stored)
        if step is None:
            if reset is None:
                raise StoreVersionError(
                    f"store is at schema version {stored!r} and this ADT.ai reads "
                    f"{version!r}; nothing here can lift it"
                )
            reset(connection)
            return
        step.apply(connection)
        stored = step.target


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """One value, read past whatever row factory the store installed."""
    cursor = connection.cursor()
    cursor.row_factory = None
    row = cursor.execute(sql, params).fetchone()
    return None if row is None else row[0]


__all__ = [
    "BUSY_TIMEOUT_SECONDS",
    "MEMORY",
    "Migration",
    "StoreVersionError",
    "Version",
    "has_table",
    "open_store",
    "stored_version",
]
