from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved runner helpers.
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.export_apex import queries
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.filters import (
    ApexComponentFilter,
    ApexExplicitFilter,
    ApexPageSelection,
)
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.metadata import (
    _load_yaml_mapping,
    _merge_app_groups,
    _parse_app_group_blocks,
    _render_app_group_blocks,
    _store_application_metadata,
    _store_workspace_developers,
    _store_yaml_mapping,
    _workspace_developers_from_rows,
)
from adt_ai.export_apex.partial import (
    _component_row,
    _has_explicit,
    _is_partial,
    _print_components,
)
from adt_ai.export_apex.postprocess import (
    _bind_params,
    _blob_bytes,
    _clean_page_author,
    _clean_split_sql,
    _default_id_offset,
    _embedded_relative,
    _enrich_sql,
    _enrichments,
    _export_options,
    _extract_first,
    _flag,
    _normalize_text_line_endings,
    _override_apex_release,
    _payload_for,
    _skip_collection_file,
    _strip_app_prefix,
    _target_path,
)
from adt_ai.export_apex.progress import (
    ApexProgressReporter,
    ConsoleApexProgressReporter,
    _load_timers,
    _store_timers,
    _timer_value,
    _update_timer,
)
from adt_ai.export_apex.recent import (
    RecentComponentFilter,
    _page_id_from_export_path,
    _print_application_export_header,
    _print_recent_changes_header,
    _print_recent_components,
    _recent_component_filter,
    _recent_since,
    _used_on_pages,
)
from adt_ai.export_apex.recent_authors import (
    dedupe_recent_rows,
    merge_workspace_developers,
    recent_author_label,
    recent_authors,
    workspace_developers_from_mapping,
)
from adt_ai.export_apex.request import ApexExportRequest
from adt_ai.export_apex.rest import (
    _cleanup_sqlcl,
    _matches_prefix,
    _plsql_block,
    _rest_module_name,
    _rest_prefixes,
    _schema_definition,
    _split_rest_modules,
)
from adt_ai.row_values import row_value

GatewayFactory = Callable[[str], QueryGateway]


ACTION_HEADERS = {
    "full": "  FULL APP EXPORT",
    "split": "  SPLIT COMPONENTS",
    "readable": "  READABLE COMPONENTS",
    "embedded": "  EMBEDDED CODE REPORT",
    "rest": "  REST SERVICES",
    "files": "  APPLICATION FILES",
    "files_ws": "  WORKSPACE FILES",
}


@dataclass(frozen=True)
class CollectionWriteResult:
    rows: list[dict[str, Any]]


class ApexExportRunner:
    EXPORT_START_QUERY    = queries.EXPORT_START_QUERY
    EXPORT_FULL_QUERY     = queries.EXPORT_FULL_QUERY
    EXPORT_SPLIT_QUERY    = queries.EXPORT_SPLIT_QUERY
    EXPORT_READABLE_QUERY = queries.EXPORT_READABLE_QUERY
    EXPORT_EMBEDDED_QUERY = queries.EXPORT_EMBEDDED_QUERY
    FETCH_FILES_QUERY     = queries.FETCH_FILES_QUERY
    RECENT_COMPONENTS_QUERY = queries.RECENT_COMPONENTS_QUERY
    APEX_FILES_QUERY      = queries.APEX_FILES_QUERY
    APEX_ID_NAMES_QUERY   = queries.APEX_ID_NAMES_QUERY
    WORKSPACE_DEVELOPERS_QUERY = queries.WORKSPACE_DEVELOPERS_QUERY
    PAGE_COMMENTS_QUERY = queries.PAGE_COMMENTS_QUERY
    PAGE_REGION_COMMENTS_QUERY = queries.PAGE_REGION_COMMENTS_QUERY

    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def run(self, request: ApexExportRequest) -> None:
        base_resolver = ApexFileResolver.from_config(request.root, dict(request.config))
        reporter = request.reporter or ConsoleApexProgressReporter()
        timers_file = request.timers_file or request.root / "config" / "apex_timers.yaml"
        timers = _load_timers(timers_file)
        developers_path = request.root / "config" / "apex_developers.yaml"
        if not request.recent_report_only:
            _store_application_metadata(
                request.root / "config" / "apex_apps.yaml",
                [
                    application
                    for schema in request.schemas
                    for application in request.applications.get(schema, [])
                ],
            )
        for schema in request.schemas:
            resolver = base_resolver.for_schema(schema)
            gateway = self.gateway_factory(schema)
            developer_rows = gateway.fetch_all(self.WORKSPACE_DEVELOPERS_QUERY)
            developers = merge_workspace_developers(
                workspace_developers_from_mapping(_load_yaml_mapping(developers_path)),
                _workspace_developers_from_rows(developer_rows),
            )
            if not request.recent_report_only:
                _store_workspace_developers(developers_path, developer_rows)
            for application in request.applications.get(schema, []):
                if request.recent_report_only:
                    recent_components = self._recent_components(
                        gateway, application, request, developers
                    )
                    if recent_components is not None:
                        self._print_recent_changes(
                            application,
                            developers,
                            request.recent_days,
                            recent_author_label(application, developers, request),
                            recent_components,
                        )
                    continue
                component_filters = request.component_filters
                gateway.execute(self.EXPORT_START_QUERY, {"app_id": application.app_id})
                enrichments = _enrichments(gateway, application)
                recent_components = self._recent_components(
                    gateway, application, request, developers
                )
                recent_filter = _recent_component_filter(recent_components)
                explicit_filter = ApexExplicitFilter(
                    request.page_selection,
                    component_filters,
                )
                (resolver.app_root(application) / "comments").mkdir(parents=True, exist_ok=True)
                page_names = self._write_page_comments(
                    gateway,
                    resolver,
                    application,
                    recent_filter,
                    explicit_filter,
                )
                if recent_components is not None:
                    self._print_recent_changes(
                        application,
                        developers,
                        request.recent_days,
                        recent_author_label(application, developers, request),
                        recent_components,
                    )
                if any(request.actions.values()):
                    _print_application_export_header(application)
                self._run_text_actions(
                    gateway,
                    resolver,
                    application,
                    request,
                    enrichments,
                    developers,
                    reporter,
                    timers,
                    timers_file,
                    recent_filter,
                    explicit_filter,
                    page_names,
                )
                if request.actions.get("rest"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "rest",
                        lambda gateway=gateway, resolver=resolver: self._write_rest_export(
                            gateway, resolver, request.config
                        ),
                    )
                if request.actions.get("files"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "files",
                        lambda gateway=gateway, application=application, resolver=resolver: (
                            self._write_static_files(
                                gateway,
                                resolver,
                                application,
                                application.app_id,
                            )
                        ),
                    )
                if request.actions.get("files_ws"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "files_ws",
                        lambda gateway=gateway, application=application, resolver=resolver: (
                            self._write_static_files(gateway, resolver, application, 0)
                        ),
                    )

    def _run_text_actions(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        request: ApexExportRequest,
        enrichments: Mapping[int, str],
        developers: Mapping[str, Mapping[str, str]],
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        recent_filter: RecentComponentFilter,
        explicit_filter: ApexExplicitFilter,
        page_names: dict[int, str],
    ) -> None:
        for action, sql in (
            ("full", self.EXPORT_FULL_QUERY),
            ("split", self.EXPORT_SPLIT_QUERY),
            ("readable", self.EXPORT_READABLE_QUERY),
            ("embedded", self.EXPORT_EMBEDDED_QUERY),
        ):
            if not request.actions.get(action):
                continue

            def operation(sql: str = sql, action: str = action) -> list[dict[str, Any]]:
                gateway.execute(
                    sql,
                    _bind_params(
                        sql, {"app_id": application.app_id, **_export_options(request.config)}
                    ),
                )
                return self._write_collection_files(
                    gateway,
                    resolver,
                    application,
                    action,
                    enrichments,
                    request.config,
                    developers,
                    request.release,
                    recent_filter,
                    explicit_filter,
                    page_names,
                )

            if _is_partial(request):
                result = self._run_partial_action(
                    reporter,
                    timers,
                    application,
                    action,
                    operation,
                )
                if _has_explicit(request):
                    _print_components(result.rows)
            else:
                self._run_action(
                    reporter,
                    timers,
                    timers_file,
                    application,
                    action,
                    operation,
                )

    def _run_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        application: ApexApplication,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or 999.0,
            operation,
        )
        _update_timer(timers, application.app_id, action, elapsed)
        _store_timers(timers_file, timers)

    def _run_partial_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        application: ApexApplication,
        action: str,
        operation: Callable[[], CollectionWriteResult],
    ) -> CollectionWriteResult:
        result: CollectionWriteResult | None = None

        def wrapped_operation() -> None:
            nonlocal result
            result = operation()

        reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or 999.0,
            wrapped_operation,
        )
        return result or CollectionWriteResult([])


    def _write_collection_files(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        action: str,
        enrichments: Mapping[int, str],
        config: Mapping[str, object],
        developers: Mapping[str, Mapping[str, str]],
        release: str | None,
        recent_filter: RecentComponentFilter,
        explicit_filter: ApexExplicitFilter,
        page_names: dict[int, str] | None = None,
    ) -> CollectionWriteResult:
        rows = []
        for row in gateway.fetch_all(self.FETCH_FILES_QUERY):
            file_name = str(row_value(row, "FILE_NAME") or "")
            payload = str(row_value(row, "CLOB_CONTENT") or "")
            relative = _strip_app_prefix(file_name, application)
            if _skip_collection_file(action, relative):
                continue
            if not recent_filter.matches(action, relative):
                continue
            if not explicit_filter.matches(action, relative):
                continue
            if page_names is not None:
                component_row = _component_row(action, relative, page_names)
                if component_row is not None:
                    rows.append(component_row)
            target = _target_path(resolver, application, action, file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = _payload_for(
                action,
                payload,
                relative,
                application,
                enrichments,
                config,
                developers,
                release,
            )
            if action == "readable" and target == resolver.workspace_root() / "app_groups.yaml":
                content = _merge_app_groups(target, content)
            target.write_text(content, encoding="utf-8")
        return CollectionWriteResult(rows)

    def _write_static_files(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        app_id: int,
    ) -> None:
        for row in gateway.fetch_all(self.APEX_FILES_QUERY, {"app_id": app_id}):
            file_name = str(row_value(row, "FILENAME") or "")
            payload = _blob_bytes(row_value(row, "BLOB_CONTENT"))
            target = (
                resolver.workspace_file(file_name)
                if app_id == 0
                else resolver.application_file(application, file_name)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    def _write_page_comments(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        recent_filter: RecentComponentFilter,
        explicit_filter: ApexExplicitFilter,
    ) -> dict[int, str]:
        comments: dict[int, dict[str, Any]] = {}
        for row in gateway.fetch_all(self.PAGE_COMMENTS_QUERY, {"app_id": application.app_id}):
            page_id = int(row_value(row, "PAGE_ID") or 0)
            relative = f"application/pages/page_{page_id:05d}.sql"
            if not recent_filter.matches("split", relative):
                continue
            if not explicit_filter.matches("split", relative):
                continue
            comments[page_id] = {
                "page": {
                    "page_name": row_value(row, "PAGE_NAME"),
                    "page_comment": row_value(row, "PAGE_COMMENT"),
                    "updated_by": row_value(row, "LAST_UPDATED_BY"),
                    "updated_at": row_value(row, "LAST_UPDATED_ON"),
                },
                "regions": {},
            }
        for row in gateway.fetch_all(
            self.PAGE_REGION_COMMENTS_QUERY, {"app_id": application.app_id}
        ):
            page_id = int(row_value(row, "PAGE_ID") or 0)
            relative = f"application/pages/page_{page_id:05d}.sql"
            if not recent_filter.matches("split", relative):
                continue
            if not explicit_filter.matches("split", relative):
                continue
            region_id = int(row_value(row, "REGION_ID") or 0)
            if page_id not in comments:
                comments[page_id] = {
                    "page": {
                        "page_name": row_value(row, "PAGE_NAME"),
                    },
                    "regions": {},
                }
            comments[page_id]["regions"][region_id] = {
                "region_name": row_value(row, "REGION_NAME"),
                "region_comment": row_value(row, "COMPONENT_COMMENT"),
                "updated_by": row_value(row, "LAST_UPDATED_BY"),
                "updated_at": row_value(row, "LAST_UPDATED_ON"),
            }
        comments_root = resolver.app_root(application) / "comments"
        comments_root.mkdir(parents=True, exist_ok=True)
        for page_id, payload in comments.items():
            _store_yaml_mapping(comments_root / f"p{page_id:05d}.yaml", payload)
        return {
            page_id: str(payload.get("page", {}).get("page_name") or "")
            for page_id, payload in comments.items()
        }

    def _print_recent_changes(
        self,
        application: ApexApplication,
        developers: Mapping[str, Mapping[str, str]],
        recent_days: int | None,
        author_label: str | None,
        rows: list[dict[str, Any]],
    ) -> None:
        if recent_days is None:
            return
        if author_label and not rows:
            return
        _print_recent_changes_header(application, _recent_since(recent_days), author_label or "")
        _print_recent_components(rows)

    def _recent_components(
        self,
        gateway: QueryGateway,
        application: ApexApplication,
        request: ApexExportRequest,
        developers: Mapping[str, Mapping[str, str]],
    ) -> list[dict[str, Any]] | None:
        if not request.recent_days or request.recent_days <= 0:
            return None
        authors = recent_authors(application, developers, request)
        if not authors:
            return gateway.fetch_all(
                self.RECENT_COMPONENTS_QUERY,
                {
                    "app_id": application.app_id,
                    "recent": request.recent_days,
                    "author": None,
                },
            )
        rows: list[dict[str, Any]] = []
        for author in authors:
            rows.extend(
                gateway.fetch_all(
                    self.RECENT_COMPONENTS_QUERY,
                    {
                        "app_id": application.app_id,
                        "recent": request.recent_days,
                        "author": author,
                    },
                )
            )
        return dedupe_recent_rows(rows)

    def _write_rest_export(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        config: Mapping[str, object],
    ) -> None:
        root = resolver.apex_root()
        root.mkdir(parents=True, exist_ok=True)
        resolver.rest_export("__enable_schema").parent.mkdir(parents=True, exist_ok=True)
        lines = _cleanup_sqlcl(gateway.sqlcl_request("SET LINESIZE 200;\nrest export;", root))
        first, modules = _split_rest_modules(lines)
        prefixes = _rest_prefixes(config)
        for module in modules:
            name = _rest_module_name(module)
            if not _matches_prefix(name, prefixes):
                continue
            target = resolver.rest_export(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_plsql_block(module), encoding="utf-8")
        if modules:
            target = resolver.rest_export("__enable_schema")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_plsql_block(_schema_definition(first)), encoding="utf-8")


__all__ = [name for name in globals() if not name.startswith("__")]
