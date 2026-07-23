from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved runner helpers.
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.filters import (
    ApexComponentFilter,
    ApexExplicitFilter,
    ApexPageSelection,
)
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.metadata import (
    _merge_app_groups,
    _parse_app_group_blocks,
    _render_app_group_blocks,
    _store_application_metadata,
    _store_workspace_developers,
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
    FALLBACK_TARGET_SECONDS,
    ApexProgressReporter,
    ConsoleApexProgressReporter,
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
from adt_ai.shared import text_files
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.recent_state import (
    RecentStore,
    is_bare_recent,
    may_advance,
    read_db_now,
)
from adt_ai.shared.row_values import row_value
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

GatewayFactory = Callable[[str], QueryGateway]


ACTION_HEADERS = {
    "full": "  FULL APP EXPORT",
    "split": "  SPLIT COMPONENTS",
    "readable": "  READABLE COMPONENTS",
    "embedded": "  EMBEDDED CODE REPORT",
    "checksum": "  APP CHECKSUM",
    "rest": "  REST SERVICES",
    "files": "  APPLICATION FILES",
    "files_ws": "  WORKSPACE FILES",
}

# Formats whose coverage a `-recent` watermark can describe. `rest`, `files`, and
# `files_ws` export artefacts that carry no component `last_updated_on`, so a
# component-level cutoff says nothing about whether they are current. `checksum`
# is a single whole-app fingerprint: it reports *that* the app changed, never
# which components were exported, so it must not stamp "everything is current".
_WATERMARKED_FORMATS = ("full", "split", "readable", "embedded")


def is_watermarking(request: ApexExportRequest) -> bool:
    """Whether this run could stamp a watermark, and so needs a database clock read."""
    if request.recent_report_only or request.narrowed:
        return False
    return any(
        action in _WATERMARKED_FORMATS
        for action, wanted in request.actions.items()
        if wanted
    )


@dataclass(frozen=True)
class CollectionWriteResult:
    rows: list[dict[str, Any]]


class ApexExportRunner:
    EXPORT_START_QUERY    = queries.EXPORT_START_QUERY
    EXPORT_FULL_QUERY     = queries.EXPORT_FULL_QUERY
    EXPORT_SPLIT_QUERY    = queries.EXPORT_SPLIT_QUERY
    EXPORT_READABLE_QUERY = queries.EXPORT_READABLE_QUERY
    EXPORT_EMBEDDED_QUERY = queries.EXPORT_EMBEDDED_QUERY
    EXPORT_CHECKSUM_QUERY = queries.EXPORT_CHECKSUM_QUERY
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
        timers = load_yaml_mapping(timers_file)
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
                workspace_developers_from_mapping(load_yaml_mapping(developers_path)),
                _workspace_developers_from_rows(developer_rows),
            )
            if not request.recent_report_only:
                _store_workspace_developers(developers_path, developer_rows)
            for application in request.applications.get(schema, []):
                cutoff = self._listing_cutoff(request, application)
                # Database clock BEFORE the component listing: anything changed
                # mid-export stays at or after it and is re-selected next run.
                candidate = (
                    read_db_now(gateway)
                    if is_watermarking(request)
                    else None
                )
                if request.recent_report_only:
                    recent_components = self._recent_components(
                        gateway, application, request, developers, cutoff
                    )
                    if recent_components is not None:
                        self._print_recent_changes(
                            application,
                            developers,
                            request.recent_days,
                            recent_author_label(request),
                            recent_components,
                            cutoff,
                        )
                    continue
                component_filters = request.component_filters
                deep_rows: list[dict[str, Any]] = []
                gateway.execute(self.EXPORT_START_QUERY, {"app_id": application.app_id})
                enrichments = _enrichments(gateway, application)
                recent_components = self._recent_components(
                    gateway, application, request, developers, cutoff
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
                        recent_author_label(request),
                        recent_components,
                        cutoff,
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
                    deep_rows,
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
                # Reached only when every requested format wrote successfully, so
                # an app that raised mid-export keeps its previous watermarks.
                self._advance_watermarks(request, application, candidate)

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
        deep_rows: list[dict[str, Any]],
    ) -> None:
        for action, sql in (
            ("full", self.EXPORT_FULL_QUERY),
            ("split", self.EXPORT_SPLIT_QUERY),
            ("readable", self.EXPORT_READABLE_QUERY),
            ("embedded", self.EXPORT_EMBEDDED_QUERY),
            ("checksum", self.EXPORT_CHECKSUM_QUERY),
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
                    _print_components([*deep_rows, *result.rows])
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
            _timer_value(timers, application.app_id, action) or FALLBACK_TARGET_SECONDS,
            operation,
        )
        _update_timer(timers, application.app_id, action, elapsed)
        store_yaml_mapping(timers_file, timers)

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
            _timer_value(timers, application.app_id, action) or FALLBACK_TARGET_SECONDS,
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
            text_files.write_text(target, content)
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
            store_yaml_mapping(comments_root / f"p{page_id:05d}.yaml", payload)
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
        changed_since: str | None = None,
    ) -> None:
        if recent_days is None and changed_since is None:
            return
        if author_label and not rows:
            return
        # Watermark mode shows the stored instant verbatim; -recent N keeps the
        # day-count arithmetic it has always used.
        since = changed_since if changed_since is not None else _recent_since(recent_days)
        _print_recent_changes_header(application, since, author_label or "")
        _print_recent_components(rows)

    def _recent_components(
        self,
        gateway: QueryGateway,
        application: ApexApplication,
        request: ApexExportRequest,
        developers: Mapping[str, Mapping[str, str]],
        changed_since: str | None = None,
    ) -> list[dict[str, Any]] | None:
        if request.recent is None:
            return None
        if changed_since is None and (not request.recent_days or request.recent_days <= 0):
            # Bare -recent with no watermark for any requested format: nothing to
            # narrow by, so the export covers the whole app (and may then seed).
            return None
        binds = {
            "app_id": application.app_id,
            "recent": request.recent_days,
            "changed_since": changed_since,
        }
        authors = recent_authors(application, developers, request)
        if not authors:
            return gateway.fetch_all(
                self.RECENT_COMPONENTS_QUERY,
                {**binds, "author": None},
            )
        rows: list[dict[str, Any]] = []
        for author in authors:
            rows.extend(
                gateway.fetch_all(
                    self.RECENT_COMPONENTS_QUERY,
                    {**binds, "author": author},
                )
            )
        return dedupe_recent_rows(rows)

    def _requested_formats(self, request: ApexExportRequest) -> list[str]:
        return [action for action, wanted in request.actions.items() if wanted]

    def _listing_cutoff(
        self,
        request: ApexExportRequest,
        application: ApexApplication,
    ) -> str | None:
        """The oldest watermark across the formats this run will export.

        One listing feeds every requested format, so it must reach back as far as
        the least recently exported one. A format with no watermark yet makes the
        listing unbounded — over-export is harmless, a missed component is not.
        """
        if not is_bare_recent(request.recent) or request.environment is None:
            return None
        formats = self._requested_formats(request) or _WATERMARKED_FORMATS
        store = RecentStore.load(request.root)
        stored = [
            store.get("export_apex", [request.environment, application.app_id, action])
            for action in formats
        ]
        if any(value is None for value in stored):
            return None
        return min(stored)  # type: ignore[type-var]

    def _advance_watermarks(
        self,
        request: ApexExportRequest,
        application: ApexApplication,
        candidate: str | None,
    ) -> None:
        """Stamp each exported format's own key after the app's pass succeeded.

        Each format is judged against **its own** stored watermark, never the
        shared listing cutoff: the listing reached back to the oldest of them, so
        every format was covered at least as far back as its own stamp.
        """
        if candidate is None or request.environment is None or request.recent_report_only:
            return
        store = RecentStore.load(request.root)
        advanced = False
        for action in self._requested_formats(request):
            if action not in _WATERMARKED_FORMATS:
                continue
            scope = [request.environment, application.app_id, action]
            if not may_advance(
                recent   = request.recent,
                stored   = store.get("export_apex", scope),
                db_now   = candidate,
                narrowed = request.narrowed,
                dry_run  = False,
            ):
                continue
            store.set("export_apex", scope, candidate)
            advanced = True
        if advanced:
            store.save()

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
            text_files.write_text(target, _plsql_block(module))
        if modules:
            target = resolver.rest_export("__enable_schema")
            target.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(target, _plsql_block(_schema_definition(first)))


__all__ = [name for name in globals() if not name.startswith("__")]
