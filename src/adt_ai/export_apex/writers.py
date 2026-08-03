"""File-persistence half of the APEX export runner.

``ApexCollectionWriterMixin`` owns every write the runner performs: collection
payloads, static files, page/region comment YAML, and the REST export. The
query constants stay class attributes on ``ApexExportRunner`` (tests override
them there), reached through ``self``.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.filters import ApexExplicitFilter
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.metadata import _merge_app_groups
from adt_ai.export_apex.partial import _component_row
from adt_ai.export_apex.postprocess import (
    _blob_bytes,
    _payload_for,
    _skip_collection_file,
    _strip_app_prefix,
    _target_path,
)
from adt_ai.export_apex.recent import RecentComponentFilter
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
from adt_ai.shared.row_values import row_value
from adt_ai.shared.yaml_io import store_yaml_mapping


@dataclass(frozen=True)
class CollectionWriteResult:
    rows: list[dict[str, Any]]


class ApexCollectionWriterMixin:
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
        if action == "apexlang":
            # An APEXlang folder is only meaningful as a complete snapshot of the
            # app: a component deleted in APEX must not survive as a stale `.apx`
            # here. Nothing else lives under `apexlang/`, so recreating just that
            # subtree leaves every sibling export folder untouched.
            shutil.rmtree(resolver.apexlang_root(application), ignore_errors=True)
        for row in gateway.fetch_all(self.FETCH_FILES_QUERY):  # type: ignore[attr-defined]
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
        for row in gateway.fetch_all(self.APEX_FILES_QUERY, {"app_id": app_id}):  # type: ignore[attr-defined]
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
        for row in gateway.fetch_all(self.PAGE_COMMENTS_QUERY, {"app_id": application.app_id}):  # type: ignore[attr-defined]
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
            self.PAGE_REGION_COMMENTS_QUERY, {"app_id": application.app_id}  # type: ignore[attr-defined]
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
