"""File-persistence half of the APEX export runner.

``ApexCollectionWriterMixin`` owns every write the runner performs: collection
payloads, static files, page/region comment YAML, and the REST export. The
query constants stay class attributes on ``ApexExportRunner`` (tests override
them there), reached through ``self``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
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
from adt_ai.export_apex.prune import (
    APEXLANG_SUFFIXES,
    EMBEDDED_SUFFIXES,
    OWNED_SUFFIXES,
    SQL_SUFFIXES,
    YAML_SUFFIXES,
    prune_folder,
    prune_plugin_payloads,
    sweepable,
)
from adt_ai.export_apex.recent import WHOLE_APP_ACTIONS, RecentComponentFilter
from adt_ai.export_apex.rest import export_rest
from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import REST_SCHEMA_DEFINITION
from adt_ai.shared.apex_payloads import link_payloads
from adt_ai.shared.apexlang_line_endings import lf_bytes
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value
from adt_ai.shared.yaml_io import store_yaml_mapping


@dataclass(frozen=True)
class CollectionWriteResult:
    rows: list[dict[str, Any]]


# Each action's prunable root, and the extensions it owns there (`prune.py`).
# `full` writes one file, `readable` shares `application/` with `split` and is
# not swept.
_PRUNE_ROOTS: dict[str, tuple[str, frozenset[str] | None]] = {
    "apexlang": ("", APEXLANG_SUFFIXES),
    "split"   : ("application", SQL_SUFFIXES),
    "embedded": ("embedded_code", EMBEDDED_SUFFIXES),
}


def _prune_root(
    resolver: ApexFileResolver,
    application: ApexApplication,
    action: str,
) -> tuple[Path, frozenset[str] | None] | None:
    entry = _PRUNE_ROOTS.get(action)
    if entry is None:
        return None
    folder, suffixes = entry
    if action == "apexlang":
        return resolver.apexlang_root(application), suffixes
    return resolver.app_root(application) / folder, suffixes


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
        # A format's folder is only meaningful as a complete snapshot of the app:
        # a page or component deleted in APEX must not survive as a stale file
        # here. That used to be a `shutil.rmtree` before the first write, which
        # deleted every unchanged member a moment before writing it back, so the
        # whole tree took a fresh mtime on every export (`#593`). Pruning what
        # this run did not write reaches the same folder by a route the
        # unchanged-skip can see.
        #
        # Old ADT cleared `application/` and `embedded_code/` the same way; ADT.ai
        # did it for `apexlang/` alone, so a deleted page survived a split
        # re-export as a stale `.sql` (`#655`). Each action sweeps only the root
        # and extensions it owns, so the sibling formats are untouched.
        written: set[Path] = set()
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
            blob = row_value(row, "BLOB_CONTENT") if action == "apexlang" else None
            if blob is not None:
                # A plugin or theme static file (ADT #930), written byte for byte:
                # no line endings, no enrichment, the file APEX holds.
                text_files.write_bytes(target, _blob_bytes(blob))
                written.add(target)
                continue
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
            if action == "apexlang":
                # LF whatever `file_crlf` says: SQLcl's APEXlang compiler reads
                # nothing else (ADT #928, #936).
                text_files.write_bytes(target, lf_bytes(content))
            else:
                text_files.write_text(target, content)
            written.add(target)
        # A narrowed run wrote a subset on purpose, so pruning would delete every
        # page it was told not to touch. `apexlang` is in `WHOLE_APP_ACTIONS` and
        # neither filter narrows it, which is why it prunes either way.
        unfiltered = action in WHOLE_APP_ACTIONS or (
            recent_filter.selects_whole_app() and explicit_filter.selects_whole_app()
        )
        prune_target = _prune_root(resolver, application, action) if unfiltered else None
        if prune_target is not None:
            prune_folder(prune_target[0], written, prune_target[1])
            if action == "apexlang":
                prune_plugin_payloads(prune_target[0], written)
        return CollectionWriteResult(rows)

    def _write_static_files(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication | None,
        app_id: int,
    ) -> None:
        """Static files for one application, or the workspace's own at `app_id` 0.

        `application` is unread on the workspace path and is `None` there: a
        schema owns its workspace files whether or not it hosts an application.
        """
        # Resolved once rather than per row, which is also what lets the
        # pairing above be stated in one place: `app_id` 0 is the workspace's
        # own files and reads no application, and every other id belongs to
        # the application the caller took it off.
        if app_id == 0:
            target_for = resolver.workspace_file
            homes: tuple[Path, ...] = (resolver.workspace_root(), resolver.rest_root())
        elif application is None:  # pragma: no cover - callers pair the two
            raise ValueError(
                f"export_apex: static files for application {app_id} "
                "were requested without the application to write them under"
            )
        else:
            target_for = partial(resolver.application_file, application)
            app_root = resolver.app_root(application)
            homes = (app_root, *(app_root / folder for folder in OWNED_SUFFIXES))
        written: set[Path] = set()
        for row in gateway.fetch_all(self.APEX_FILES_QUERY, {"app_id": app_id}):  # type: ignore[attr-defined]
            file_name = str(row_value(row, "FILENAME") or "")
            payload = _blob_bytes(row_value(row, "BLOB_CONTENT"))
            target = target_for(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Through the shared writer, so a static file whose bytes have not
            # moved keeps its mtime like every other exported artifact (`#593`).
            text_files.write_bytes(target, payload)
            written.add(target)
        # Old ADT emptied this folder before every export of it (ADT #923). A
        # completed read is every file APEX holds, so one it did not return was
        # deleted there. Swept before the links below, so the APEXlang tree does
        # not link the deleted file back in either.
        if sweepable(target_for(""), *homes):
            prune_folder(target_for(""), written)
        if application is not None:
            # ADT #765: an `-apexlang` tree beside these payloads is validatable and
            # importable as it sits, so the links are reconciled as part of writing
            # them rather than assembled later by whoever happens to read the tree.
            # A no-op when this application exports no APEXlang.
            link_payloads(
                resolver.apexlang_root(application),
                resolver.application_file(application, ""),
            )

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
        written: set[Path] = set()
        for page_id, payload in comments.items():
            target = comments_root / f"p{page_id:05d}.yaml"
            store_yaml_mapping(target, payload)
            written.add(target)
        # A page whose comments were all cleared returns no row, so its file is
        # the one this loop never reaches (ADT #923). Only a whole-app read may
        # sweep: a filtered one read the pages it was told to, on purpose.
        if recent_filter.selects_whole_app() and explicit_filter.selects_whole_app():
            prune_folder(comments_root, written, YAML_SUFFIXES)
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
        rest_root = resolver.rest_root()
        rest_root.mkdir(parents=True, exist_ok=True)
        # `export_rest` raises before returning anything on a failed run, so
        # nothing is written for one, the clean modules included: the run
        # reports failure, and half a schema's REST definitions on disk would be
        # a repository nobody can trust (ADT #670).
        export = export_rest(gateway, root, config)
        written: set[Path] = set()
        for name, text in export.modules.items():
            target = resolver.rest_export(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(target, text)
            written.add(target)
        if export.schema_definition is not None:
            target = resolver.rest_export(REST_SCHEMA_DEFINITION)
            target.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(target, export.schema_definition)
            written.add(target)
        # Old ADT emptied the REST folder before every export (ADT #923). Only a
        # transcript that reached its closing `COMMIT;` proves its modules are all
        # the schema publishes, `-rest` writes nothing but `.sql`, and a folder
        # another export also writes into is never swept.
        homes = (root, resolver.workspace_root(), resolver.workspace_file(""))
        if export.completed and sweepable(rest_root, *homes):
            prune_folder(rest_root, written, SQL_SUFFIXES)
