from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved runner helpers.
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.actions import (
    ACTION_HEADERS,
    APEXLANG_MIN_APEX_RELEASE,
    TEXT_ACTIONS,
    ApexActionTimingMixin,
    open_segment_bar,
    print_apexlang_skip_row,
    skipped_by_apex_release,
)
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
    _store_application_checksum,
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
    _checksum_value,
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
)
from adt_ai.export_apex.recent import (
    RecentComponentFilter,
    _changes_since_label,
    _page_id_from_export_path,
    _print_application_export_header,
    _print_recent_components,
    _print_schema_export_header,
    _recent_component_filter,
    _recent_since,
    _reports_recent_changes,
    _used_on_pages,
    open_application_section,
    print_recent_changes,
    recent_components,
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
    _schema_block,
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
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.dates import is_sub_day_window
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.shared.recent_state import (
    is_bare_recent,
    may_advance,
    read_db_now,
)
from adt_ai.shared.row_values import row_value

GatewayFactory = Callable[[str], QueryGateway]


class ApexExportRunner(ApexCollectionWriterMixin, ApexWatermarkMixin, ApexActionTimingMixin):
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
        # One store for the whole run: every ETA read, every ETA write and the
        # developer merge below all address `config/internal/apex.db`, and
        # reopening it per action would pay the connect cost once per exported
        # format per application.
        store = request.apex_store or ApexStore.load(request.root)
        timers = store.timers()
        if not request.recent_report_only:
            _store_application_metadata(
                request.root,
                [
                    application
                    for schema in request.schemas
                    for application in request.applications.get(schema, [])
                ],
            )
        for schema in request.schemas:
            resolver = base_resolver.for_schema(schema)
            if not request.recent_report_only:
                for stale in resolver.stale_checksum_files():
                    stale.unlink()
            gateway = self.gateway_factory(schema)
            # **Read on first use, which is under an application's own header**
            # (`#372`). Read eagerly it was the run's first silent wait, a
            # schema-level query with nothing above it but the connection
            # block's closing blank. Every consumer sits in the loop below, so
            # first use lands after that application's section title. A schema
            # with no application reaches none, and exports nothing either.
            cached_developers: dict[str, Mapping[str, str]] | None = None

            def workspace_developers(
                gateway: QueryGateway = gateway,
            ) -> Mapping[str, Mapping[str, str]]:
                nonlocal cached_developers
                if cached_developers is None:
                    developer_rows = gateway.fetch_all(self.WORKSPACE_DEVELOPERS_QUERY)
                    cached_developers = merge_workspace_developers(
                        workspace_developers_from_mapping(store.developers()),
                        _workspace_developers_from_rows(developer_rows),
                    )
                    if not request.recent_report_only:
                        _store_workspace_developers(request.root, developer_rows)
                return cached_developers

            applications = request.applications.get(schema, [])
            # `-compact`: one bar for this whole schema segment, budgeted from
            # the stored time of every pair it is about to run.
            segment_bar = open_segment_bar(request, applications, timers, schema)
            segment_reporter = segment_bar or reporter
            for index, application in enumerate(applications):
                # `-rest` and `-files_ws` write workspace artifacts, paths with
                # no app id in them, so they belong to the schema, not to an
                # application, and must run exactly once however many
                # applications the schema has. The first one carries them, so
                # the rows still read among that block's other export rows.
                schema_slice = index == 0
                cutoff = self._listing_cutoff(request, application)
                # A sub-day window's title carries the instant it starts at, and
                # that instant belongs to the database clock (`#340`), so this
                # one `SELECT ... FROM dual` is the only read that has to come
                # before the header rather than under it. Every other window
                # knows its title from the request alone.
                sub_day = is_sub_day_window(request.recent_days)
                header_now = read_db_now(gateway) if sub_day else None
                # **The application's first section title goes up before its
                # first read** (`#372`); which title that is, and the one run
                # that can open on neither, are in `open_application_section`.
                opened = open_application_section(
                    request,
                    application,
                    cutoff,
                    header_now,
                    exporting_header = segment_bar is None,
                )
                # Database clock BEFORE the component listing: anything changed
                # mid-export stays at or after it and is re-selected next run. A
                # sub-day run already asked above, so the round trip happens once.
                candidate = (
                    (header_now if sub_day else read_db_now(gateway))
                    if is_watermarking(request)
                    else None
                )
                if request.recent_report_only:
                    recent_rows = recent_components(
                        gateway, application, request, workspace_developers(), cutoff
                    )
                    if recent_rows is not None:
                        print_recent_changes(
                            application,
                            request.recent_days,
                            recent_author_label(request),
                            recent_rows,
                            cutoff,
                            header_now,
                            header_printed = opened == "changes",
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
                recent_rows = recent_components(
                    gateway, application, request, workspace_developers(), cutoff
                )
                recent_filter = _recent_component_filter(recent_rows)
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
                if recent_rows is not None:
                    print_recent_changes(
                        application,
                        request.recent_days,
                        recent_author_label(request),
                        recent_rows,
                        cutoff,
                        header_now,
                        header_printed = opened == "changes",
                    )
                # The segment bar already covers every application, so a block
                # here would be the rows `-compact` exists to replace; and
                # `opened` says the title already went up ahead of the reads.
                if (
                    any(request.actions.values())
                    and segment_bar is None
                    and opened != "exporting"
                ):
                    _print_application_export_header(application)
                self._run_text_actions(
                    gateway,
                    resolver,
                    application,
                    request,
                    enrichments,
                    workspace_developers(),
                    segment_reporter,
                    timers,
                    store,
                    recent_filter,
                    explicit_filter,
                    page_names,
                    deep_rows,
                )
                if request.actions.get("files"):
                    self._run_action(
                        segment_reporter,
                        timers,
                        store,
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
                if request.actions.get("files_ws") and schema_slice:
                    self._run_schema_action(
                        segment_reporter,
                        timers,
                        store,
                        "files_ws",
                        lambda gateway=gateway, application=application, resolver=resolver: (
                            self._write_static_files(gateway, resolver, application, 0)
                        ),
                    )
                if request.actions.get("rest") and schema_slice:
                    self._run_schema_action(
                        segment_reporter,
                        timers,
                        store,
                        "rest",
                        lambda gateway=gateway, resolver=resolver: self._write_rest_export(
                            gateway, resolver, request.config
                        ),
                    )
                self._store_checksum(gateway, request, application)
                # Reached only when every requested format wrote successfully, so
                # an app that raised mid-export keeps its previous watermarks.
                self._advance_watermarks(request, application, candidate)
            # A schema hosting no APEX application still owns its workspace
            # artifacts, and there is no application block for their rows to sit
            # under, so this is the one case that keeps a `SCHEMA <name>,
            # EXPORTING:` header. Skipping the schema entirely here is what made
            # `-rest` finish printing nothing at all (ADT #190); `-files_ws` had
            # the same hole and never got that fix.
            schema_only = self._schema_only_actions(request, applications, gateway, resolver)
            if schema_only and segment_bar is None:
                _print_schema_export_header(schema)
            for action, operation in schema_only:
                self._run_schema_action(
                    segment_reporter, timers, store, action, operation
                )
            # Only where every action wrote: one that raised already completed
            # its row with `FAILED`, and a 100% redraw would overwrite it.
            if segment_bar is not None:
                segment_bar.close()

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
        store: ApexStore,
        recent_filter: RecentComponentFilter,
        explicit_filter: ApexExplicitFilter,
        page_names: dict[int, str],
        deep_rows: list[dict[str, Any]],
    ) -> None:
        # Shared `TEXT_ACTIONS` order, never a second list here: the compact
        # bar's budget walks the same tuple (see `actions.planned_actions`).
        for action in TEXT_ACTIONS:
            sql = getattr(self, f"EXPORT_{action.upper()}_QUERY")
            if not request.actions.get(action):
                continue
            if skipped_by_apex_release(action, request.apex_version):
                # The pre-26.1 `apexlang` miss is announced only to the user who
                # named that format, the line answers "where is my APEXlang
                # export?", a question `-all` never asked, so under `-all` it was
                # a notice about something nobody requested (ADT #235). The 26.1
                # `readable` skip is silent always (Jan, 2026-07-27).
                if action == "apexlang" and action in request.explicit_actions:
                    print_apexlang_skip_row()
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
                    store,
                    application,
                    action,
                    operation,
                )

    def _store_checksum(
        self,
        gateway: QueryGateway,
        request: ApexExportRequest,
        application: ApexApplication,
    ) -> None:
        """Cache the application's ID-independent SHA-256 fingerprint.

        APEX computes it over the whole application, so nothing about the run
        narrows it: a `-page` or `-recent` export records the same value a full
        one does. It is collected rather than exported, so it prints no row of
        its own, the same way the workspace developer list and the rest of the
        application metadata are read (ADT #343).

        `#360` gave it a row and `#372` took it back: `#343` had dropped that
        row deliberately, so restoring it was a regression wearing a fix.
        """
        gateway.execute(self.EXPORT_CHECKSUM_QUERY, {"app_id": application.app_id})
        _store_application_checksum(
            request.root,
            application.app_id,
            _checksum_value(gateway.fetch_all(self.FETCH_FILES_QUERY)),
        )


__all__ = [name for name in globals() if not name.startswith("__")]
