"""Raw-mirror SQLite engine for the ``dependencies`` command.

The READ half lives in `store_reads.py` since ADT #512, when `#510`'s
connection guard pushed this module past the 20 KB context guard. What is left
here is the half that WRITES: opening and versioning the mirror, the refresh
paths, and the lifecycle. `DependencyStore` inherits the rest, so nothing a
caller imports or calls moved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.dependencies import queries, refresh
from adt_ai.dependencies.db import dict_factory
from adt_ai.dependencies.owner_case import fold_owner_case, normalize_owner
from adt_ai.dependencies.schema import (
    DROP_SCHEMA,
    LEGACY_TABLES,
    REFRESHES_DDL,
    RETIRED_COLUMNS,
    RETIRED_INDEXES,
    SCHEMA,
    SCHEMA_VERSION,
)
from adt_ai.dependencies.store_reads import (  # noqa: F401  (re-exported for existing importers)
    DEFAULT_MAX_DEPTH,
    DependencyQueries,
)
from adt_ai.shared.sqlite_store import Migration, drop_columns, open_store

_LAST_REFRESH_PREFIX = "last_refresh"


def _lift_3(connection: Any) -> None:
    """Version 3 to 4: the refresh stamps leave `_meta` for their own table.

    A stamp and the offset read beside it describe one refresh, so the two
    rows an older ADT wrote under parallel keys become one row here; a scope
    carrying only one of them keeps that one and NULL for the other, which is
    what `patch/graph_mirror.py` already reads either half as.
    """
    scopes: dict[tuple[str, str], list[str | None]] = {}
    for row in connection.execute(queries.LEGACY_STAMP_ROWS_QUERY).fetchall():
        prefix, _, remainder = str(row["key"]).partition(":")
        scope_type, _, scope_name = remainder.partition(":")
        entry = scopes.setdefault((scope_type, scope_name), [None, None])
        entry[0 if prefix == _LAST_REFRESH_PREFIX else 1] = row["value"]
    connection.executescript(queries.MIRROR_LIFT_3_SCRIPT)
    connection.executemany(
        queries.REFRESH_UPSERT,
        [(scope_type, scope_name, *entry) for (scope_type, scope_name), entry in scopes.items()],
    )
    connection.execute(queries.LEGACY_STAMP_DELETE)


def _lift_4(connection: Any) -> None:
    """Version 4 to 5: drop what the mirror stored and nothing read (ADT #873)."""
    connection.executescript(queries.MIRROR_LIFT_4_SCRIPT)
    for table, columns in RETIRED_COLUMNS.items():
        drop_columns(connection, table, columns, indexes=RETIRED_INDEXES)


def _lift_5(connection: Any) -> None:
    """Version 5 to 6: nothing to move (ADT #895).

    Version 6 only adds the text mirrors `search TERM` reads, and the schema
    script run after every lift creates them. Their stamps start absent, so the
    next refresh fills them and `search` names the layer until it has.
    """


def _lift_6(connection: Any) -> None:
    """Version 6 to 7: the component source is keyed by text ids (ADT #901).

    Its `COMPONENT_ID` could not hold an APEX id past SQLite's 64-bit INTEGER,
    so the table is recreated as TEXT and refilled by the next `rebuild -app`;
    every other table and stamp stays as it was. The `refreshes` DDL is
    idempotent and runs first, because a lift chained up from an older file
    reaches this step before the schema script has created that table.
    """
    connection.executescript(REFRESHES_DDL)
    connection.executescript(queries.MIRROR_LIFT_6_SCRIPT)


def _lift_7(connection: Any) -> None:
    """Version 7 to 8: the mirror stops copying the schema's source (ADT #904).

    `USER_SOURCE` held what the exported object files already hold, and
    `search TERM` reads those now, so the table and its `source` stamps go and
    every other row and stamp stays.
    """
    connection.executescript(queries.MIRROR_LIFT_7_SCRIPT)


MIGRATIONS: tuple[Migration, ...] = (
    Migration("3", "4", _lift_3),
    Migration("4", "5", _lift_4),
    Migration("5", "6", _lift_5),
    Migration("6", "7", _lift_6),
    Migration("7", "8", _lift_7),
)


def build_db(db_path: str | Path) -> DependencyStore:
    """Create a fresh database (replacing any existing file) and return its store."""
    if not (isinstance(db_path, str) and db_path == ":memory:"):
        Path(db_path).unlink(missing_ok=True)
    return DependencyStore.open(db_path)


class DependencyStore(DependencyQueries):
    """Query + write API over the raw-mirror dependency database."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @classmethod
    def open(cls, db_path: str | Path, *, rebuild: bool = False) -> DependencyStore:
        """Open, creating the schema when absent and lifting an older file.

        A file the migrations know (version 3 or 4) is lifted in place on either
        path. One they do not is wiped and recreated with ``rebuild=True``
        (refresh path only) and refused with a ``StoreVersionError`` otherwise,
        so a version mismatch never silently destroys data mid-query.
        """
        wiped: list[bool] = []

        def reset(connection: Any) -> None:
            connection.executescript(DROP_SCHEMA)
            wiped.append(True)

        # Closed on failure since ADT #510, by the shared opener: `connect`
        # succeeds against any readable path and only the first statement
        # discovers the bytes are not a database. The statements below run on
        # the same suspect file, so they close it the same way.
        connection = open_store(
            db_path,
            schema      = SCHEMA,
            version     = SCHEMA_VERSION,
            migrations  = MIGRATIONS,
            row_factory = dict_factory,
            reset       = reset if rebuild else None,
        )
        try:
            for _legacy in LEGACY_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS [{_legacy}]")
            connection.commit()
            if rebuild and not wiped:
                # Heal a mirror an older ADT split across two spellings of one
                # schema (ADT #413). Refresh path only, for the same reason the
                # version wipe is: this deletes the duplicate copy, and a query
                # mode must never rewrite data underneath a report. A wiped mirror
                # has nothing left to fold.
                fold_owner_case(connection)
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    # writers

    def refresh_schema(
        self,
        owner: str,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """Replace one owner's ``USER_*`` rows in a single transaction."""
        return refresh.refresh_schema_full(self.connection, normalize_owner(owner), tables)

    def refresh_schema_incremental(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Patch one owner's ``USER_*`` rows without wiping unchanged objects."""
        return refresh.refresh_schema_incremental(
            self.connection, normalize_owner(owner), object_rows, tables, force=force
        )

    def refresh_schema_deep(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        object_names: Iterable[str],
    ) -> dict[str, int]:
        """Replace rows to/from named objects without wiping the whole owner."""
        return refresh.refresh_schema_deep(
            self.connection,
            normalize_owner(owner),
            object_rows,
            tables,
            object_names=object_names,
        )

    def schema_changed_objects(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        *,
        force: bool = False,
    ) -> list[tuple[str, str]]:
        """Return added or modified ``USER_OBJECTS`` keys for ``owner``."""
        return refresh.schema_changed_objects(
            self.connection, normalize_owner(owner), object_rows, force=force
        )

    def refresh_app(
        self,
        app_id: int,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """Replace one app's ``APEX_*`` rows in a single transaction."""
        return refresh.refresh_app_full(self.connection, app_id, tables)

    def refresh_app_incremental(
        self,
        app_id: int,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Patch one app's ``APEX_*`` rows without wiping unchanged rows."""
        return refresh.refresh_app_incremental(self.connection, app_id, tables, force=force)

    def app_page_rows(
        self, app_id: int, page_ids: Iterable[int]
    ) -> dict[str, list[dict[str, Any]]]:
        """One app's stored dependency rows for ``page_ids``, and the objects they name.

        What a page the fallback walk could not scan keeps (ADT #865), in the
        ``{table: rows}`` shape :meth:`refresh_app_incremental` takes.
        """
        pages = list(page_ids)
        if not pages:
            return {}
        props = self.connection.execute(
            queries.select_page_props_query(len(pages)), (app_id, *pages)
        ).fetchall()
        objects = self.connection.execute(
            queries.select_page_objects_query(len(pages)), (app_id, app_id, *pages)
        ).fetchall()
        return {
            "APEX_USED_DB_OBJECTS": [dict(row) for row in objects],
            "APEX_USED_DB_OBJECT_COMP_PROPS": [dict(row) for row in props],
        }

    def record_refresh(
        self,
        scope_type: str,
        scope_name: str,
        timestamp: str,
        *,
        db_offset: str | None = None,
    ) -> None:
        """Stamp one refreshed scope's completion time into ``refreshes``.

        ``scope_type`` is ``"schema"`` or ``"app"``; the pair is the row's key,
        so re-refreshing a scope replaces its stamp rather than duplicating it.

        ``db_offset`` is that scope's DATABASE UTC offset (``+02:00``), kept in
        the same row so `patch -create` reads a mirrored ``LAST_DDL_TIME`` on
        the clock that produced it (ADT #394). ``None`` leaves a recorded
        offset alone rather than erasing it.

        A schema scope is normalized the way its mirror rows are, so `-schema
        app_owner` and `-schema APP_OWNER` stamp one row rather than two (ADT
        #413). An app scope is a numeric id and carries no case question.
        """
        if scope_type == "schema":
            scope_name = normalize_owner(scope_name)
        with self.connection:
            self.connection.execute(
                queries.REFRESH_UPSERT,
                (scope_type, scope_name, timestamp, db_offset or None),
            )

    def last_refreshes(self) -> list[dict[str, str]]:
        """Per-scope last-refresh stamps, schemas first then apps (offline).

        Reads the ``refreshes`` rows that carry a stamp as
        ``{type, scope, last_refresh}``. `search -app` reads it to tell an
        application the mirror holds from one nobody scanned (`#30`).
        """
        rows = self.connection.execute(queries.REFRESHES_QUERY).fetchall()
        parsed: list[dict[str, str]] = [
            {
                "type": str(row["scope_type"]),
                "scope": str(row["scope_name"]),
                "last_refresh": str(row["refreshed_at"]),
            }
            for row in rows
            if row["refreshed_at"]
        ]
        schemas = sorted(
            (item for item in parsed if item["type"] == "schema"),
            key=lambda item: item["scope"],
        )
        apps = sorted(
            (item for item in parsed if item["type"] == "app"),
            key=lambda item: int(item["scope"]) if item["scope"].isdigit() else item["scope"],
        )
        others = [item for item in parsed if item["type"] not in ("schema", "app")]
        return schemas + apps + others

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DependencyStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

__all__ = [
    "Any",
    "DEFAULT_MAX_DEPTH",
    "DROP_SCHEMA",
    "DependencyQueries",
    "DependencyStore",
    "Iterable",
    "LEGACY_TABLES",
    "MIGRATIONS",
    "Mapping",
    "Migration",
    "Path",
    "SCHEMA",
    "SCHEMA_VERSION",
    "annotations",
    "build_db",
    "dict_factory",
    "fold_owner_case",
    "normalize_owner",
    "open_store",
    "queries",
    "refresh",
]
