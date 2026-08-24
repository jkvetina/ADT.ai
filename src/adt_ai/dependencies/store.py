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
from adt_ai.dependencies.db import connect
from adt_ai.dependencies.owner_case import fold_owner_case, normalize_owner
from adt_ai.dependencies.schema import DROP_SCHEMA, LEGACY_TABLES, SCHEMA, SCHEMA_VERSION
from adt_ai.dependencies.store_reads import (  # noqa: F401  (re-exported for existing importers)
    DEFAULT_MAX_DEPTH,
    DependencyQueries,
)


def _meta_table_exists(connection: Any) -> bool:
    row = connection.execute(queries.META_TABLE_EXISTS_QUERY).fetchone()
    return row is not None


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
        """Open, creating the schema when absent.

        Pass ``rebuild=True`` (refresh path only) to wipe and recreate all
        tables when the stored schema version doesn't match SCHEMA_VERSION.
        Query-mode callers leave this False so a version mismatch never
        silently destroys data mid-query.
        """
        connection = connect(db_path)
        # Closed on failure since ADT #510: `connect` succeeds against any
        # readable path and only the first statement discovers the bytes are not a
        # database, so a raise anywhere below used to leave the connection open
        # and unreferenced. `ApexStore` and `CommitStore` carry the same guard for
        # the same reason; the argument is written out at `ApexStore.open`. The
        # whole body is inside it here rather than the schema step alone, because
        # every statement below reads or writes the same suspect file.
        try:
            version_sql = (
                queries.META_SCHEMA_VERSION_QUERY
                if _meta_table_exists(connection) else
                queries.META_SCHEMA_VERSION_NULL_QUERY
            )
            stored = connection.execute(version_sql).fetchone()
            wiped = rebuild and (stored is None or stored["value"] != SCHEMA_VERSION)
            if wiped:
                connection.executescript(DROP_SCHEMA)
            connection.executescript(SCHEMA)
            for _legacy in LEGACY_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS [{_legacy}]")
            connection.execute(queries.META_UPSERT_SCHEMA_VERSION, (SCHEMA_VERSION,))
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

    def record_refresh(
        self,
        scope_type: str,
        scope_name: str,
        timestamp: str,
        *,
        db_offset: str | None = None,
    ) -> None:
        """Stamp one refreshed scope's completion time into ``_meta``.

        ``scope_type`` is ``"schema"`` or ``"app"``; the key is
        ``last_refresh:<type>:<name>`` so re-refreshing a scope replaces its
        stamp rather than duplicating it.

        ``db_offset`` is that scope's DATABASE UTC offset (``+02:00``), under
        the parallel ``db_utc_offset:`` key so `patch -create` reads a mirrored
        ``LAST_DDL_TIME`` on the clock that produced it (ADT #394).

        A schema scope is normalized the way its mirror rows are, so `-schema
        ict_owner` and `-schema ICT_OWNER` stamp one row rather than two (ADT
        #413). An app scope is a numeric id and carries no case question.
        """
        if scope_type == "schema":
            scope_name = normalize_owner(scope_name)
        rows = [
            (f"{queries.META_LAST_REFRESH_PREFIX}{scope_type}:{scope_name}", timestamp)
        ]
        if db_offset:
            rows.append(
                (f"{queries.META_DB_OFFSET_PREFIX}{scope_type}:{scope_name}", db_offset)
            )
        with self.connection:
            self.connection.executemany(queries.META_UPSERT_QUERY, rows)

    def last_refreshes(self) -> list[dict[str, str]]:
        """Per-scope last-refresh stamps, schemas first then apps (offline).

        Reads the ``last_refresh:*`` rows from ``_meta`` and splits each key back
        into ``{type, scope, last_refresh}``. Backs the ``-age`` query mode, so
        an agent can check staleness without the file-mtime heuristic.
        """
        rows = self.connection.execute(queries.META_LAST_REFRESH_QUERY).fetchall()
        parsed: list[dict[str, str]] = []
        for row in rows:
            _, scope_type, scope_name = row["key"].split(":", 2)
            parsed.append(
                {"type": scope_type, "scope": scope_name, "last_refresh": row["value"]}
            )
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
