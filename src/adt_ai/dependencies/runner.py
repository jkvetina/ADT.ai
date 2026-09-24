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

The ``-app`` axis then fills the text mirrors `search TERM` reads, the
component source and static files per app; the logic lives in
:mod:`adt_ai.dependencies.source_mirror` (ADT #895). The schema axis mirrors no
source: `search` reads a schema's from the files `export_db` wrote (ADT #904).

The ``.db`` is the single source of truth: no YAML index, graph/edges/
constraints/columns YAML, or per-object ``.md`` cards are written anymore; the
query modes recompute from the raw mirrors at query time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from adt_ai.dependencies import plscope, queries, refresh, source_mirror
from adt_ai.dependencies.component_scan import (
    SCAN_SESSION_STATEMENTS,
    run_component_scan,
    scan_error_sentence,
    scan_page_by_page,
    union_scans,
)
from adt_ai.dependencies.plscope import _Crawl
from adt_ai.dependencies.store import DependencyStore
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import (
    DottedProgressBar,
    fixed_width_count_line,
    fixed_width_status_line,
    print_adt_header,
)
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar

#: `#861`, spelled by Jan picking it: the state leads and what the run did about
#: it trails, the shape `WARNING - NOT COMMITTED SCRIPTS, IGNORED:` already has.
APEX_TOO_OLD_HEADER = "WARNING - APEX TOO OLD, SKIPPED:"

#: The component scan's row, and the key its duration is kept under in
#: `apex.db`'s per-application timers (ADT #904).
SCAN_HEADER = "SCANNING COMPONENTS"
SCAN_TIMER_ACTION = "component_scan"

#: Both take the shape above, the state leading and the rest trailing. ADT #908
#: for the application nothing scanned, whose rows the run kept because the scan
#: clears the cache the `APEX_USED_DB*` views read from; ADT #929, Jan spelling
#: it live, for the pages a walk could not scan: *"SHOW IT BELOW AS: WARNING -
#: COMPONENT SCAN FAILED, BROKEN PAGES:"*.
SCAN_FAILED_HEADER = "WARNING - COMPONENT SCAN FAILED, DEPENDENCIES KEPT:"
SCAN_FAILED_PAGES_HEADER = "WARNING - COMPONENT SCAN FAILED, BROKEN PAGES:"


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
    # Per-scope last-refresh stamp; defaults to "now" when the request omits it.
    refreshed_at: str | None = None
    # app_id -> display label ("122" or "122/ALIAS"), for the per-app section header.
    app_labels: dict[int, str] | None = None
    # Called with each application's id once its scan is stored, before the next
    # application's header opens: `rebuild -app` reads the page links there, so
    # they print under the one header the scan opened (`#30`).
    on_app_refreshed: Callable[[int], None] | None = None


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

    def refresh(self, request: DependencyIndexRequest) -> list[int]:
        """Refresh every scope the request names; return the apps whose scan failed.

        The return value is what lets `rebuild` exit non-zero on a run it
        finished anyway (ADT #908): a failed component scan is one
        application's problem, not the run's, so it is reported and stepped
        over rather than raised.
        """
        progress = _progress_reporter(request.progress)
        scan_failures: list[int] = []
        apps = list(request.apps or [])
        refresh_names = list(request.refresh_names or [])
        app_schema = request.app_schema or (request.schemas[0] if request.schemas else None)
        refreshed_at = request.refreshed_at or _now_stamp()

        db_path = internal_path(request.root, "dependencies.db")
        store = DependencyStore.open(db_path, rebuild=True)
        prepared: set[int] = set()
        apex_columns: dict[str, set[str]] | None = None
        try:
            for schema in request.schemas:
                gateway = self.gateway_factory(schema)
                scope_names = refresh_names
                progress.begin("USER_OBJECTS")
                object_query = (
                    queries.USER_OBJECTS_SCOPED_QUERY
                    if refresh_names
                    else queries.USER_OBJECTS_QUERY
                )
                scoped_params: dict[str, Any] | None = (
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

            # One version per run: every app is read through `app_schema`, so the
            # floor answers for all of them at once and names them in one section.
            apex_version = (request.apex_versions or {}).get(app_schema) if app_schema else None
            if apps and app_schema and not queries.supports_apex_used_views(apex_version):
                _print_apex_too_old(apps, request.app_labels or {})
                apps = []
            for app in apps:
                if app_schema is None:
                    continue
                label = (request.app_labels or {}).get(app, str(app))
                print_adt_header(f"APP {label}, REFRESHING:")
                gateway = self.gateway_factory(app_schema)
                if id(gateway) not in prepared:
                    plscope.ensure_plscope(
                        gateway, progress=progress.line, bar=progress.bar()
                    )
                    prepared.add(id(gateway))
                scan_error = _scan_components(gateway, app, progress, request.root)
                # Held back until the section below is finished (#929).
                broken: tuple[BaseException, list[int], dict[int, str]] | None = None
                if scan_error is None:
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
                else:
                    # No `APEX_USED_DB*` row is read after the failed whole-app
                    # scan (ADT #908). Its `CLEAR_CACHE` is rolled back with it,
                    # so the views still hold whatever the previous scan left,
                    # measured on app 430 (ADT #865: 6865 rows, 285 of 302
                    # pages), which is neither this scan's answer nor reliably
                    # the last good one. The page walk below reads them only
                    # after a page scan that worked, crawling on the scan's own
                    # label, opened before it reads the application's pages.
                    walk = scan_page_by_page(
                        gateway,
                        app,
                        queries.apex_table_queries(apex_version),
                        _Crawl(progress.bar(), SCAN_HEADER),
                    )
                    if walk.scans:
                        failed_pages = [page for page, _error in walk.failed]
                        tables = union_scans(
                            app, [store.app_page_rows(app, failed_pages), *walk.scans]
                        )
                        for table, rows in tables.items():
                            progress.begin(table)
                            progress.finish(table, len(rows))
                        store.refresh_app_incremental(app, tables, force=request.force)
                        store.record_refresh("app", str(app), refreshed_at)
                    if walk.failed or not walk.scans:
                        scan_failures.append(app)
                        broken = (
                            scan_error,
                            [page for page, _error in walk.failed] if walk.scans else [],
                            walk.names,
                        )
                # Component source and static files, a full replace per app;
                # the dictionary's column list is read on the first app only.
                apex_columns = source_mirror.refresh_app_source(
                    store.connection,
                    gateway,
                    app,
                    apex_columns,
                    progress     = progress,
                    refreshed_at = refreshed_at,
                )
                if request.on_app_refreshed is not None:
                    request.on_app_refreshed(app)
                # Below the whole section, never inside the progress list (#929).
                if broken is not None:
                    _print_scan_failed(
                        label,
                        broken[0],
                        failed_pages = broken[1],
                        page_names   = broken[2],
                    )
        finally:
            store.close()
        return scan_failures


def _scan_components(
    gateway: QueryGateway, app: int, progress: Any, root: Path
) -> BaseException | None:
    """The APEX component scan, crawling on a timed row like the recompile beside it.

    The scan is one opaque call with no count of its own, minutes on an app with
    many pages, so it runs under the shared timed bar: a percentage counting
    down from what the same application's scan took last time, closed on the
    time it really took. It ended on a bare `DONE` until ADT #904, Jan:
    *"Recompile has a timer but scan has DONE? Use timer there too!"*. The
    figure is kept in `apex.db` beside the export timers, per application,
    folded `(elapsed + previous) / 2` like every other countdown target.

    One boundary for the whole helper lifecycle (`#699`): the scan installs
    `DEPSCAN$` procedures and the cleanup that removes them runs in a
    `finally`, so a scan that raises leaves none of them on the schema. A
    reporter with no terminal line to draw on runs the scan bare, and still
    closes a failed row on `FAILED`.

    **The error comes back rather than out** (ADT #908). The scan is APEX's own
    PL/SQL, and an application whose components it cannot compile raises out of
    `WWV_FLOW_OBJECT_DEPENDENCY_DEV` however long the rest of the run would have
    taken: one such application took a 542-second `rebuild -app` with it. The
    caller reports it and carries on, so what this returns is the failure, and a
    caller that ignores the answer is the one thing that would restore the abort.
    """
    bar = progress.bar()
    if bar is None:
        try:
            run_component_scan(gateway, app, session_statements=SCAN_SESSION_STATEMENTS)
        except Exception as error:  # noqa: BLE001 - returned, reported by the caller
            progress.fail(SCAN_HEADER)
            return error
        return None
    # The figure only paces the bar, so an unreadable `apex.db` crawls from the
    # fallback and records nothing rather than failing the scan.
    try:
        with ApexStore.load(root) as store:
            previous = store.timers().get(app, {}).get(SCAN_TIMER_ACTION, 0.0)
    except (sqlite3.Error, OSError):
        previous = None
    try:
        # `TimedProgressBar` closes the row on `FAILED` itself before re-raising,
        # so the row is already finished by the time the error arrives here.
        elapsed = TimedProgressBar().run(
            SCAN_HEADER,
            previous or FALLBACK_TARGET_SECONDS,
            lambda: run_component_scan(gateway, app, session_statements=SCAN_SESSION_STATEMENTS),
        )
    except Exception as error:  # noqa: BLE001 - returned, reported by the caller
        return error
    if previous is None:
        return None
    with ApexStore.load(root) as store:
        store.store_timer(
            app, SCAN_TIMER_ACTION, (elapsed + previous) / 2 if previous > 0 else elapsed
        )
    return None


def _print_scan_failed(
    label: str,
    error: BaseException,
    *,
    failed_pages: list[int] | None = None,
    page_names: dict[int, str] | None = None,
) -> None:
    """One warning section naming what APEX could not scan (ADT #908).

    Two blocks, for two questions. Nothing scanned: the application, with the
    error as `scan_error_sentence` reads it, because a warning a run carries on
    from is a sentence and not the APEX stack behind it. The page-by-page
    fallback (#865) got the application and a few pages broke: the LIST, one row
    per page, its id and its `apex_application_pages` name, ids right aligned so
    the names start on one column. ADT #929, Jan: *"SHOW IT BELOW AS: 2110
    PAGE_NAME"*, which withdrew all that was not the list: the `APP.PAGE` prefix
    the header carries, the per-row `ORA-` sentence repeating one cause, and
    `#921`'s line saying fixing them restores the whole scan.

    **It stands below the application's whole section**, closing on a blank line
    like every standalone warning. Until #929 it printed the moment the walk
    came back, wedged between `APEX_USED_DB_OBJECT_COMP_PROPS` and
    `APEX_COMPONENT_SOURCE`, so one progress list read as two; nothing reads
    after it now, so the blank retires a spent header.
    """
    if not failed_pages:
        print_adt_header(SCAN_FAILED_HEADER)
        print(f"  APP {label} kept its previous dependencies: {scan_error_sentence(error)}")
        print()
        return
    print_adt_header(SCAN_FAILED_PAGES_HEADER)
    width = max(len(str(page)) for page in failed_pages)
    for page in failed_pages:
        name = (page_names or {}).get(page)
        row = f"  {page:>{width}}"
        print(f"{row} {name}" if name else row)
    print()


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


class _CallableProgressReporter:
    """Test-only adapter: one complete formatted string per callback.

    Not console-streaming safe, ``begin()`` cannot emit a bare label the way
    ``FixedWidthProgressPrinter.begin()`` does, because a plain callable has
    no notion of "the same terminal line, filled in later". Real CLI output
    always goes through ``FixedWidthProgressPrinter`` (see
    ``cli/rebuild_refresh.py``); this class exists only so tests can assert
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


def _progress_reporter(progress: Any) -> DependencyProgress:
    if progress is None:
        return _NoProgressReporter()
    if callable(progress):
        return _CallableProgressReporter(progress)
    # Anything else is a reporter the caller built itself, the console
    # `FixedWidthProgressPrinter` above all; it is taken at its word.
    return cast(DependencyProgress, progress)


def _print_apex_too_old(apps: list[int], labels: dict[int, str]) -> None:
    """The apps whose component scan the APEX release cannot run (`#861`).

    It was a bare progress line per app, `APEX dependency scan requires APEX 24.2
    or newer; skipping APEX app N.`, sitting under whatever section came before.
    """
    print_adt_header(APEX_TOO_OLD_HEADER)
    for app in apps:
        print(f"  APP {labels.get(app, str(app))} needs APEX 24.2 or newer")
    print()
