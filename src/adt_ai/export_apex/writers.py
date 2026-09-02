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
from adt_ai.export_apex.recent import WHOLE_APP_ACTIONS, RecentComponentFilter
from adt_ai.export_apex.rest import (
    _cleanup_sqlcl,
    _matches_prefix,
    _plsql_block,
    _rest_export_completed,
    _rest_export_error,
    _rest_module_name,
    _rest_prefixes,
    _schema_block,
    _schema_definition,
    _split_rest_modules,
    rest_timeout_seconds,
)
from adt_ai.shared import text_files
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value
from adt_ai.shared.yaml_io import store_yaml_mapping


@dataclass(frozen=True)
class CollectionWriteResult:
    rows: list[dict[str, Any]]


def _prune_folder(root: Path, keep: set[Path], suffixes: frozenset[str] | None = None) -> None:
    """Delete everything under ``root`` this export did not write.

    The replacement for clearing the folder up front: same end state, minus the
    delete-and-rewrite that gave every surviving file a fresh mtime. Empty
    folders go too, so a component type that lost its last member leaves no
    directory behind either.

    ``suffixes`` narrows the sweep to the extensions the action owns, for a root
    two formats share: `readable/` lands its `.yaml` under the same
    `application/` tree the split export fills with `.sql`, so a split prune that
    took every file would delete the readable export beside it.
    """
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            if path not in keep and (suffixes is None or path.suffix in suffixes):
                path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


# Every extension `-apexlang` can put under `apexlang/`, and therefore every one
# it may delete from there. Read off the writer: the export block collects only
# CLOB members and drops the `shared-components/static-files/` payloads, so what
# lands is the `.apx` source plus the `.json` metadata beside it
# (`.apex/apexlang.json`, `deployments/*.json`) -- the member list a live 26.1
# probe returned (apps 800 and 808, 2026-07-27) and the one
# `test_runner_apexlang.py` pins.
#
# It used to be `None`, meaning "every file", and that swept a developer's own
# `NOTES.md` out of the folder on the next unfiltered run; reproduced live on ADT
# #670. A stale member of some extension APEX has not shipped yet surviving a
# sweep is the cheaper failure by a wide margin.
APEXLANG_SUFFIXES = frozenset({".apx", ".json"})

# The same question for `embedded_code/`, which carried the same `None` and so
# the same defect (ADT #670). APEX's `EMBEDDED_CODE` export is an application's
# embedded JavaScript, CSS and PL/SQL, and this writer copies each member name
# verbatim: `postprocess._embedded_relative` only strips the `embedded_code/`
# prefix and rewrites `pages/p` to `pages/page_`, so the suffix on disk is the
# suffix APEX emitted. `.sql` and `.js` are the two this suite's own fixtures
# pin; `.css` is the third member of that triple, listed so a renamed stylesheet
# does not survive forever.
#
# Conservative in the same direction as the APEXlang set above: a stale member
# of an extension APEX has not shipped yet outliving a sweep costs one diff,
# where sweeping every file costs a developer whatever they kept in the folder.
EMBEDDED_SUFFIXES = frozenset({".sql", ".js", ".css"})

# Each action's prunable root, and the extensions it owns there. `full` writes
# one file, `readable` shares `application/` with `split` and is not swept.
_PRUNE_ROOTS: dict[str, tuple[str, frozenset[str] | None]] = {
    "apexlang": ("", APEXLANG_SUFFIXES),
    "split"   : ("application", frozenset({".sql"})),
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
            written.add(target)
        # A narrowed run wrote a subset on purpose, so pruning would delete every
        # page it was told not to touch. `apexlang` is in `WHOLE_APP_ACTIONS` and
        # neither filter narrows it, which is why it prunes either way.
        unfiltered = action in WHOLE_APP_ACTIONS or (
            recent_filter.selects_whole_app() and explicit_filter.selects_whole_app()
        )
        prune_target = _prune_root(resolver, application, action) if unfiltered else None
        if prune_target is not None:
            _prune_folder(prune_target[0], written, prune_target[1])
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
        elif application is None:  # pragma: no cover - callers pair the two
            raise ValueError(
                f"export_apex: static files for application {app_id} "
                "were requested without the application to write them under"
            )
        else:
            target_for = partial(resolver.application_file, application)
        for row in gateway.fetch_all(self.APEX_FILES_QUERY, {"app_id": app_id}):  # type: ignore[attr-defined]
            file_name = str(row_value(row, "FILENAME") or "")
            payload = _blob_bytes(row_value(row, "BLOB_CONTENT"))
            target = target_for(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Through the shared writer, so a static file whose bytes have not
            # moved keeps its mtime like every other exported artifact (`#593`).
            text_files.write_bytes(target, payload)

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
        output = gateway.sqlcl_request(
            "SET LINESIZE 200;\nrest export;",
            root,
            timeout_seconds = rest_timeout_seconds(config),
        )
        lines = _cleanup_sqlcl(output)
        preamble, modules, trailer = _split_rest_modules(lines)
        # Both checks run before the first write, and both run whatever the split
        # found. Scanning for the diagnostic only when the split found NO module
        # meant an export that broke on module N, after 1..N-1 printed cleanly,
        # reported success: SQLcl still exits 0, and the split flushed the
        # truncated block as a module whose name came off the failed
        # `DEFINE_MODULE` text, then wrote it verbatim to `<module>.sql`
        # (ADT #670). Nothing is written for a failed run, the clean modules
        # included: the run reports failure, so half a schema's REST definitions
        # on disk would be a repository nobody can trust.
        error = _rest_export_error(lines)
        if error is not None:
            # The headline is the first diagnostic, but the transcript comes
            # with it: the line a regex picked is regularly the symptom and
            # the cause is some other line in the same output (ADT #232).
            raise RuntimeError(
                f"SQLcl rest export failed: {error}\nFull SQLcl output:\n"
                + "\n".join(lines).strip()
            )
        if modules and not _rest_export_completed(lines):
            raise RuntimeError(
                "SQLcl rest export ended before its closing COMMIT;, so the "
                "modules it printed may be incomplete\nFull SQLcl output:\n"
                + "\n".join(lines).strip()
            )
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
            text_files.write_text(
                target, _schema_block(_schema_definition(preamble, trailer))
            )
