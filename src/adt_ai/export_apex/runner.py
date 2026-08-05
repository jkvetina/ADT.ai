from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved runner helpers.
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.deep import deep_component_filters, deep_db_object_rows
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
    _print_schema_export_header,
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
from adt_ai.export_apex.watermarks import (
    _WATERMARKED_FORMATS,
    ApexWatermarkMixin,
    is_watermarking,
)
from adt_ai.export_apex.writers import ApexCollectionWriterMixin, CollectionWriteResult
from adt_ai.shared import text_files
from adt_ai.shared.apex_version import readable_yaml_removed, supports_apexlang
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.progress import FixedWidthProgressPrinter
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
    "apexlang": "  APEXLANG EXPORT",
    "checksum": "  APP CHECKSUM",
    "rest": "  REST SERVICES",
    "files": "  APPLICATION FILES",
    "files_ws": "  WORKSPACE FILES",
}

# The APEX release that introduced `APEX_EXPORT.c_type_apexlang` and folded
# READABLE_YAML into it as a deprecated alias.
APEXLANG_MIN_APEX_RELEASE = "26.1"

def _skipped_by_apex_release(action: str, apex_version: str | None) -> bool:
    """Whether this instance's APEX release rules the format out.

    Two one-way gates, both reading the release the connection block already
    probed. An unknown release gates nothing in either direction.
    """
    if action == "apexlang":
        return not supports_apexlang(apex_version)
    if action == "readable":
        return readable_yaml_removed(apex_version)
    return False


def _print_apexlang_skip_row(apex_version: str | None) -> None:
    """Complete an APEXLANG row with a skip status instead of running it.

    A pre-26.1 instance has no `APEXLANG` export type, so an `-all` run has to
    say why the format produced nothing rather than fail the whole export.
    """
    label = ACTION_HEADERS["apexlang"].strip()
    found = apex_version or "unknown"
    printer = FixedWidthProgressPrinter()
    printer.begin(label)
    printer.status(label, f"SKIPPED | needs APEX {APEXLANG_MIN_APEX_RELEASE}, found {found}")


class ApexExportRunner(ApexCollectionWriterMixin, ApexWatermarkMixin):
    EXPORT_START_QUERY    = queries.EXPORT_START_QUERY
    EXPORT_FULL_QUERY     = queries.EXPORT_FULL_QUERY
    EXPORT_SPLIT_QUERY    = queries.EXPORT_SPLIT_QUERY
    EXPORT_READABLE_QUERY = queries.EXPORT_READABLE_QUERY
    EXPORT_EMBEDDED_QUERY = queries.EXPORT_EMBEDDED_QUERY
    EXPORT_APEXLANG_QUERY = queries.EXPORT_APEXLANG_QUERY
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
                if request.deep:
                    component_filters = deep_component_filters(
                        request.root,
                        application.app_id,
                        request.page_selection,
                        request.component_filters,
                    )
                    deep_rows = deep_db_object_rows(
                        request.root,
                        application.app_id,
                        request.page_selection,
                    )
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
            # REST services belong to the schema, not to an application: the
            # export writes `apex/workspace/rest/`, a path with no app id in it.
            # Running it inside the loop above meant a schema with no APEX
            # application exported nothing at all — silently, exit 0 — and a
            # schema with N applications ran the identical schema-wide export N
            # times over the same files (ADT #190).
            if request.actions.get("rest") and not request.recent_report_only:
                _print_schema_export_header(schema)
                self._run_schema_action(
                    reporter,
                    timers,
                    timers_file,
                    "rest",
                    lambda gateway=gateway, resolver=resolver: self._write_rest_export(
                        gateway, resolver, request.config
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
        deep_rows: list[dict[str, Any]],
    ) -> None:
        for action, sql in (
            ("full", self.EXPORT_FULL_QUERY),
            ("split", self.EXPORT_SPLIT_QUERY),
            ("readable", self.EXPORT_READABLE_QUERY),
            ("embedded", self.EXPORT_EMBEDDED_QUERY),
            ("apexlang", self.EXPORT_APEXLANG_QUERY),
            ("checksum", self.EXPORT_CHECKSUM_QUERY),
        ):
            if not request.actions.get(action):
                continue
            if _skipped_by_apex_release(action, request.apex_version):
                # Only the pre-26.1 `apexlang` miss is announced: the user asked
                # for a format this instance cannot make. The 26.1 `readable`
                # skip is deliberately silent (Jan, 2026-07-27).
                if action == "apexlang":
                    _print_apexlang_skip_row(request.apex_version)
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

    def _run_schema_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        """Run a schema-level action, timed under the workspace slot.

        `apex_timers.yaml` is keyed by app id; app `0` is already the workspace
        slot (`-files_ws` writes workspace files as app 0), so a schema-level
        action reuses it rather than borrowing whichever application happened to
        be last in the loop.
        """
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, 0, action) or FALLBACK_TARGET_SECONDS,
            operation,
        )
        _update_timer(timers, 0, action, elapsed)
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


__all__ = [name for name in globals() if not name.startswith("__")]
