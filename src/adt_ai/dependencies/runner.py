"""Refresh the raw-mirror dependency database from live Oracle dictionaries.

Two independent axes feed one ``config/dependencies.db``:

* ``-schema`` (USER_* axis) — for each schema, pull the object inventory, use
  ``LAST_DDL_TIME`` to detect changed objects, run the same-connection PL/Scope
  prerequisite only for those changed objects (see
  :mod:`adt_ai.dependencies.plscope`), then bulk-fetch detail mirrors and let
  SQLite keep only rows for changed objects.
* ``-app`` (APEX axis) — for each app, re-scan its component sources
  (``APEX_APP_OBJECT_DEPENDENCY.SCAN``) then pull each ``APEX_USED_DB*`` view and
  hand it to :meth:`DependencyStore.refresh_app_incremental`.

The refresh keeps producing ``config/db_dependencies.yaml`` as a compatibility
artifact derived from the freshly written ``.db`` rather than a YAML index. No
graph/edges/constraints/columns YAML or per-object ``.md`` cards are written
anymore; the query modes recompute from the raw mirrors at query time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adt_ai.db import QueryGateway
from adt_ai.dependencies import plscope, queries, refresh
from adt_ai.dependencies.store import DependencyStore
from adt_ai.export_apex import queries as export_apex_queries
from adt_ai.progress import fixed_width_count_line, fixed_width_status_line


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


GatewayFactory = Callable[[str], QueryGateway]


class DependencyIndexRunner:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def refresh(self, request: DependencyIndexRequest) -> None:
        progress = _progress_reporter(request.progress)
        apps = list(request.apps or [])
        refresh_names = list(request.refresh_names or [])
        app_schema = request.app_schema or (request.schemas[0] if request.schemas else None)

        db_path = request.root / "config" / "dependencies.db"
        store = DependencyStore.open(db_path)
        prepared: set[int] = set()
        try:
            for schema in request.schemas:
                gateway = self.gateway_factory(schema)
                scoped_params = (
                    {"object_name_filter": ",".join(refresh_names)}
                    if refresh_names
                    else None
                )
                progress.begin("USER_OBJECTS")
                object_query = (
                    queries.USER_OBJECTS_SCOPED_QUERY
                    if refresh_names
                    else queries.USER_OBJECTS_QUERY
                )
                try:
                    object_rows = (
                        gateway.fetch_all(object_query, scoped_params)
                        if scoped_params
                        else gateway.fetch_all(object_query)
                    )
                except Exception:
                    progress.fail("USER_OBJECTS")
                    raise
                if refresh_names:
                    changed_objects = [
                        (row["OBJECT_TYPE"], row["OBJECT_NAME"]) for row in object_rows
                    ]
                else:
                    changed_objects = store.schema_changed_objects(
                        schema, object_rows, force=request.force
                    )
                changed = set(changed_objects)
                if request.force or refresh_names:
                    progress.finish("USER_OBJECTS", len(object_rows))
                else:
                    progress.finish("USER_OBJECTS", len(changed_objects), total=len(object_rows))
                if id(gateway) not in prepared:
                    plscope.ensure_plscope(
                        gateway,
                        candidates=changed_objects,
                        progress=progress.line,
                    )
                    prepared.add(id(gateway))
                tables = {}
                table_queries = (
                    queries.USER_TABLE_SCOPED_QUERIES
                    if refresh_names
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
                    if request.force or refresh_names:
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
                if refresh_names:
                    store.refresh_schema_deep(
                        schema,
                        object_rows,
                        tables,
                        object_names=refresh_names,
                    )
                else:
                    store.refresh_schema_incremental(
                        schema, object_rows, tables, force=request.force
                    )

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
                gateway = self.gateway_factory(app_schema)
                if id(gateway) not in prepared:
                    plscope.ensure_plscope(gateway, progress=progress.line)
                    prepared.add(id(gateway))
                gateway.execute(export_apex_queries.EXPORT_START_QUERY, {"app_id": app})
                gateway.execute(queries.APEX_SCAN_STATEMENT, {"app_id": app})
                gateway.execute(queries.DEPSCAN_CLEANUP_STATEMENT)
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

            alias = store.dependency_alias()
        finally:
            store.close()

        _write_db_dependencies(request.root / "config" / "db_dependencies.yaml", alias)


class _NoProgressReporter:
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
    def __init__(self, progress: Callable[[str], None]) -> None:
        self._progress = progress

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


def _progress_reporter(progress: Any):
    if progress is None:
        return _NoProgressReporter()
    if callable(progress):
        return _CallableProgressReporter(progress)
    return progress


def _write_db_dependencies(path: Path, alias: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {
                "dependencies": alias,
                "sorted": _sort_objects_by_dependencies(alias),
            },
            allow_unicode=True,
            default_flow_style=False,
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )


def _sort_objects_by_dependencies(dependencies: dict[str, list[str]]) -> list[str]:
    # Iterative DFS post-order: dependencies are emitted before the objects that
    # use them. An explicit stack (rather than recursion) keeps deep dependency
    # chains — thousands of objects long — from overflowing the interpreter
    # stack. The `visiting` set still breaks cycles by treating a back-edge to an
    # in-progress object as already handled, matching the prior behaviour.
    sorted_objects: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    for object_code in dependencies:
        if object_code in visited or object_code in visiting:
            continue
        visiting.add(object_code)
        stack: list[tuple[str, Iterator[str]]] = [
            (object_code, iter(dependencies.get(object_code, [])))
        ]
        while stack:
            node, dependency_iter = stack[-1]
            descended = False
            for dependency in dependency_iter:
                if dependency not in dependencies:
                    continue
                if dependency in visited or dependency in visiting:
                    continue
                visiting.add(dependency)
                stack.append((dependency, iter(dependencies.get(dependency, []))))
                descended = True
                break
            if not descended:
                stack.pop()
                visiting.discard(node)
                visited.add(node)
                sorted_objects.append(node)
    return sorted_objects
