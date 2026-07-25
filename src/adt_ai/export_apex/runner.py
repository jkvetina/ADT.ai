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
