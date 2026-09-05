"""Refresh the raw-mirror dependency database from live Oracle dictionaries.

Two independent axes feed one ``config/internal/dependencies.db``:

* ``-schema`` (USER_* axis), for each schema, pull the object inventory, use
  ``LAST_DDL_TIME`` to detect changed objects, run the same-connection PL/Scope
  prerequisite only for those changed objects (see
  :mod:`adt_ai.dependencies.plscope`), then bulk-fetch detail mirrors and let
  SQLite keep only rows for changed objects.
* ``-app`` (APEX axis), for each app, re-scan its component sources
  (``APEX_APP_OBJECT_DEPENDENCY.SCAN``) then pull each ``APEX_USED_DB*`` view and
  hand it to :meth:`DependencyStore.refresh_app_incremental`.

The ``.db`` is the single source of truth: no YAML index, graph/edges/
constraints/columns YAML, or per-object ``.md`` cards are written anymore; the
query modes recompute from the raw mirrors at query time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from adt_ai.dependencies import plscope, queries, refresh
from adt_ai.dependencies.component_scan import run_component_scan
from adt_ai.dependencies.store import DependencyStore
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import (
    DottedProgressBar,
    fixed_width_count_line,
    fixed_width_status_line,
    print_adt_header,
)
from adt_ai.shared.recent_state import is_bare_recent, recent_days


@dataclass(frozen=True)
class DependencyIndexRequest:
    root: Path
    schemas: list[str]
    config: dict[str, Any]
    apps: list[int] | None = None
    app_schema: str | None = None
    force: bool = False
    progress: Any = None
    apex_versions: dict[str, str] | None = None
    refresh_names: list[str] | None = None
    # `-recent` narrowing for `-refresh`: None (full refresh), an int day window,
    # or BARE_RECENT (since each schema scope's own `refreshes` stamp).
    recent: Any = None
    # Per-scope last-refresh stamp; defaults to "now" when the request omits it.
    refreshed_at: str | None = None
    # app_id -> display label ("122" or "122/ALIAS"), for the per-app section header.
    app_labels: dict[int, str] | None = None


GatewayFactory = Callable[[str], QueryGateway]


def _now_stamp() -> str:
    """Sortable, human-readable local timestamp for the ``refreshes`` row."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _database_utc_offset(gateway: QueryGateway) -> str | None:
    """The database server's UTC offset, or None when it cannot be read.

    Recorded per schema rather than once per run because a run's schemas are
    resolved through one gateway each and nothing says they live on the same
    database, so one offset for the whole refresh would be a guess on a
    multi-database project.

    None on an empty or unreadable answer: a mirror carrying no offset is a
    mirror `patch -create` refuses to compare clocks against, which is the
    honest outcome, whereas writing a wrong offset here would be the original
    defect with a stamp of authority on it.
    """
    rows = gateway.fetch_all(queries.DB_UTC_OFFSET_QUERY)
    if not rows:
        return None
    value = rows[0].get("DB_UTC_OFFSET")
    return str(value).strip() if value else None


class DependencyIndexRunner:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    @staticmethod
    def _schema_last_refresh(store: DependencyStore, schema: str) -> str | None:
        """The schema scope's own ``refreshes`` stamp, bare ``-recent``'s cutoff."""
        for row in store.last_refreshes():
            if row["type"] == "schema" and row["scope"] == schema:
                return row["last_refresh"]
        return None

    def refresh(self, request: DependencyIndexRequest) -> None:
        progress = _progress_reporter(request.progress)
        apps = list(request.apps or [])
        refresh_names = list(request.refresh_names or [])
        app_schema = request.app_schema or (request.schemas[0] if request.schemas else None)
        refreshed_at = request.refreshed_at or _now_stamp()

        db_path = internal_path(request.root, "dependencies.db")
        store = DependencyStore.open(db_path, rebuild=True)
        prepared: set[int] = set()
        try:
            for schema in request.schemas:
                gateway = self.gateway_factory(schema)
                # `-recent` narrows THIS schema's refresh to objects changed since
                # its own `refreshes` stamp (bare) or an N-day window, selected
                # server-side and patched through the deep per-object path so the
                # untouched mirror rows survive. A user-named deep refresh or
                # `-force` wins over `-recent`; a bare `-recent` with no stamp yet
                # falls back to the plain full refresh.
                recent_params: dict[str, Any] | None = None
                if request.recent is not None and not refresh_names and not request.force:
                    stamp = (
                        self._schema_last_refresh(store, schema)
                        if is_bare_recent(request.recent)
                        else None
                    )
                    if is_bare_recent(request.recent) and stamp is None:
                        progress.line(
                            f"  RECENT: no previous refresh recorded for {schema}, "
                            "refreshing all objects"
                        )
                    else:
                        recent_params = {
                            "changed_since": stamp,
                            # Not `int(...)`: a sub-day window (`-recent 1/24`)
                            # floors to 0, and `SYSDATE - 0` selects nothing.
                            "recent_days": (
                                None if stamp is not None else recent_days(request.recent)
                            ),
                        }
                recent_names: list[str] | None = None
                scope_names = refresh_names
                progress.begin("USER_OBJECTS")
                if recent_params is not None:
                    object_query = queries.USER_OBJECTS_RECENT_QUERY
                    scoped_params: dict[str, Any] | None = recent_params
                else:
                    object_query = (
                        queries.USER_OBJECTS_SCOPED_QUERY
                        if refresh_names
                        else queries.USER_OBJECTS_QUERY
                    )
                    scoped_params = (
                        {"object_name_filter": ",".join(refresh_names)}
                        if refresh_names
                        else None
                    )
                try:
                    # Under the USER_OBJECTS row already open above, on purpose.
                    # It is one `FROM DUAL` read and a row of its own would be a
                    # new console string nobody asked for (`#372`); the open line
                    # is the announcement the console contract asks for.
                    db_offset = _database_utc_offset(gateway)
                    object_rows = (
                        gateway.fetch_all(object_query, scoped_params)
                        if scoped_params
                        else gateway.fetch_all(object_query)
                    )
                except Exception:
                    progress.fail("USER_OBJECTS")
                    raise
                if recent_params is not None:
                    recent_names = sorted({row["OBJECT_NAME"] for row in object_rows})
                    scope_names = recent_names
                    scoped_params = (
                        {"object_name_filter": ",".join(recent_names)}
                        if recent_names
                        else None
                    )
                if scope_names:
                    changed_objects = [
                        (row["OBJECT_TYPE"], row["OBJECT_NAME"]) for row in object_rows
                    ]
                else:
                    changed_objects = store.schema_changed_objects(
                        schema, object_rows, force=request.force
                    )
                changed = set(changed_objects)
                if request.force or scope_names:
                    progress.finish("USER_OBJECTS", len(object_rows))
                else:
                    progress.finish("USER_OBJECTS", len(changed_objects), total=len(object_rows))
                if recent_names is not None and not recent_names:
                    # Nothing changed since the cutoff: skip the detail pulls
                    # entirely, but still advance the stamp, the scope WAS
                    # covered for everything since the previous refresh.
                    store.record_refresh(
                        "schema", schema, refreshed_at, db_offset=db_offset
                    )
                    continue
                if id(gateway) not in prepared:
                    # No row of its own (`#372`). The refresh header above says
                    # what is happening and stands for every call in the
                    # section, and "objects recompiled" is not the mirrored-row
                    # count the dictionary rows beside it report, so a `0` there
                    # read as a table that returned nothing. Skips still print:
                    # a locked object is news.
                    plscope.ensure_plscope(
                        gateway,
                        candidates=changed_objects,
                        progress=progress.line,
                        bar=progress.bar(),
                    )
                    prepared.add(id(gateway))
                tables = {}
                table_queries = (
                    queries.USER_TABLE_SCOPED_QUERIES
                    if scope_names
                    else queries.USER_TABLE_QUERIES
                )
                for table, query in table_queries.items():
                    if table == "USER_OBJECTS":
                        continue
                    progress.begin(table)
                    try:
                        rows = (
                            gateway.fetch_all(query, scoped_params)
                            if scoped_params
                            else gateway.fetch_all(query)
                        )
                    except Exception:
                        progress.fail(table)
                        raise
                    if request.force or scope_names:
                        progress.finish(table, len(rows))
                    else:
                        progress.finish(
                            table,
                            len(
                                refresh.schema_detail_rows_for_changed_objects(
                                    table, rows, changed
                                )
                            ),
                            total=len(rows),
                        )
                    tables[table] = rows
                if scope_names:
                    store.refresh_schema_deep(
                        schema,
                        object_rows,
                        tables,
                        object_names=scope_names,
                    )
                else:
                    store.refresh_schema_incremental(
                        schema, object_rows, tables, force=request.force
                    )
                store.record_refresh("schema", schema, refreshed_at, db_offset=db_offset)

            for app in apps:
                if app_schema is None:
                    continue
                apex_version = (request.apex_versions or {}).get(app_schema)
                if not queries.supports_apex_used_views(apex_version):
                    progress.line(
                        "  APEX dependency scan requires APEX 24.2 or newer; "
                        f"skipping APEX app {app}."
                    )
                    continue
                label = (request.app_labels or {}).get(app, str(app))
                print_adt_header(f"APP {label}, REFRESHING:")
                gateway = self.gateway_factory(app_schema)
                if id(gateway) not in prepared:
                    plscope.ensure_plscope(
                        gateway, progress=progress.line, bar=progress.bar()
                    )
                    prepared.add(id(gateway))
                # The component scan below is a real, potentially slow DB call
                # with no natural row count; without a visible row here the
                # header prints and the console then sits silent until the
                # scan resolves, minutes on an app with many pages.
                progress.begin("SCANNING COMPONENTS")
                try:
                    # One boundary for the whole helper lifecycle (`#699`): the
                    # scan installs `DEPSCAN$` procedures and the cleanup that
                    # removes them now runs in a `finally`, so a scan that
                    # raises here leaves none of them on the schema. PL/Scope is
                    # not passed as a session statement, because
                    # `ensure_plscope` above already prepared this connection.
                    run_component_scan(gateway, app)
                except Exception:
                    progress.fail("SCANNING COMPONENTS")
                    raise
                progress.status("SCANNING COMPONENTS", "DONE")
                tables = {}
                for table, query in queries.apex_table_queries(apex_version).items():
                    progress.begin(table)
                    try:
                        rows = gateway.fetch_all(query, {"app_id": app})
                    except Exception:
                        progress.fail(table)
                        raise
                    progress.finish(table, len(rows))
                    tables[table] = rows
                store.refresh_app_incremental(app, tables, force=request.force)
                store.record_refresh("app", str(app), refreshed_at)
        finally:
            store.close()


class DependencyProgress(Protocol):
    """What `DependencyIndexRunner` calls on whatever it was given.

    A Protocol rather than a base class: the two implementations below are
    the no-op and the test adapter, and the real console reporter is
    `FixedWidthProgressPrinter`, which lives in `shared/` and knows nothing
    about this module. Structural typing is what lets all three arrive here
    without the runner importing the console or the console importing this.
    """

    #: The console printer hands out a real bar; the two no-op reporters
    #: below have no terminal line to draw one on and hand out nothing.
    def bar(self) -> DottedProgressBar | None: ...

    def begin(self, label: str, *, indent: str = ...) -> None: ...

    def finish(
        self, label: str, count: int, *, total: int | None = ..., indent: str = ...
    ) -> None: ...

    def line(self, text: str) -> None: ...

    def fail(self, label: str, *, status: str = ..., indent: str = ...) -> None: ...

    def status(self, label: str, status: str, *, indent: str = ...) -> None: ...


class _NoProgressReporter:
    def bar(self) -> None:
        return None

    def begin(self, label: str, *, indent: str = "  ") -> None:
        return None

    def finish(
        self,
        label: str,
        count: int,
        *,
        total: int | None = None,
        indent: str = "  ",
    ) -> None:
        return None

    def line(self, text: str) -> None:
        return None

    def fail(self, label: str, *, status: str = "FAILED", indent: str = "  ") -> None:
        return None

    def status(self, label: str, status: str, *, indent: str = "  ") -> None:
        return None


class _CallableProgressReporter:
    """Test-only adapter: one complete formatted string per callback.

    Not console-streaming safe, ``begin()`` cannot emit a bare label the way
    ``FixedWidthProgressPrinter.begin()`` does, because a plain callable has
    no notion of "the same terminal line, filled in later". Real CLI output
    always goes through ``FixedWidthProgressPrinter`` (see
    ``commands_dependencies.py``); this class exists only so tests can assert
    on complete formatted rows via a plain callback like ``list.append``.
    """

    def __init__(self, progress: Callable[[str], None]) -> None:
        self._progress = progress

    def bar(self) -> None:
        # Same reason `begin` is a no-op here: a plain callable has no notion of
        # a terminal line to redraw.
        return None

    def begin(self, label: str, *, indent: str = "  ") -> None:
        return None

    def finish(
        self,
        label: str,
        count: int,
        *,
        total: int | None = None,
        indent: str = "  ",
    ) -> None:
        self._progress(fixed_width_count_line(label, count, total=total, indent=indent))

    def line(self, text: str) -> None:
        self._progress(text)

    def fail(self, label: str, *, status: str = "FAILED", indent: str = "  ") -> None:
        self._progress(fixed_width_status_line(label, status, indent=indent))

    def status(self, label: str, status: str, *, indent: str = "  ") -> None:
        self._progress(fixed_width_status_line(label, status, indent=indent))


def _progress_reporter(progress: Any) -> DependencyProgress:
    if progress is None:
        return _NoProgressReporter()
    if callable(progress):
        return _CallableProgressReporter(progress)
    # Anything else is a reporter the caller built itself, the console
    # `FixedWidthProgressPrinter` above all; it is taken at its word.
    return cast(DependencyProgress, progress)
