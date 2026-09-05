from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved helpers.
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.build import HASH_STAMP_FORMAT, build_database_patch
from adt_ai.patch.content import (
    CONTENT_MODE_COMMITTED,
    CONTENT_MODE_HEAD,
    CONTENT_MODE_LOCAL,
    CONTENT_MODE_NOSNAP,
    CONTENT_MODES,
)
from adt_ai.patch.create import (
    _drop_helper_sql,
    _patch_files,
    _refresh_apex_components,
    _refresh_database_files,
    _table_alter_sql,
    _write_generated_patch_scripts,
    _write_patch_files,
)
from adt_ai.patch.deploy import (
    _compile_statement,
    _deployment_app_id,
    _deployment_error_excerpt,
    _deployment_payload,
    _deployment_schema,
    _deployment_succeeded,
    _is_exact_patch_ref,
    _not_deployed_result,
    _recompile_invalid_objects,
    _select_patch_folder,
    _skipped_deployment_result,
    _verify_view_columns,
    _write_deployment_log,
    reset_deployment_spool,
)
from adt_ai.patch.deploy_progress import (
    _countable_file_total,
    _countable_references,
    _deployment_progress,
    linked_references,
)
from adt_ai.patch.deploy_run import run_deployment
from adt_ai.patch.files import (
    ArchiveResult,
    InstallScriptResult,
    _reject_unresolved_merges,
    archive_patch_folders,
    next_patch_folder,
    write_install_script,
)
from adt_ai.patch.full_app import require_fresh_full_app_exports, resolve_full_app_ids
from adt_ai.patch.hashes import write_patch_hashes
from adt_ai.patch.layout import ensure_deploy_log_folder, is_database_path
from adt_ai.patch.models import (
    AlterHelper,
    DatabasePatchResult,
    DeploymentPlanItem,
    DeploymentResult,
    DeploymentRunResult,
    PatchContentsGroup,
    PatchError,
    ProcessedFile,
    RefreshPlan,
    SchemaReport,
)
from adt_ai.patch.preview import (
    COMMIT_HASH_LENGTH,
    PREVIEW_LINE_WIDTH,
    folder_commit_entries,
    folder_preview_rows,
    outstanding_records,
    preview_rows,
    preview_rows_from,
)
from adt_ai.patch.report import build_reports
from adt_ai.patch.scan import PatchRunner
from adt_ai.patch.scripts import PatchScripts, collect_patch_scripts, reset_patch_scripts
from adt_ai.patch.snapshots import _write_snapshots
from adt_ai.patch.staleness import export_freshness, require_forced_refresh
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import (
    CommitRecord,
    GitCommitCache,
    PatchFolder,
    PatchRequest,
    _filter_records,
    discover_patch_folders,
    matches_patch_selector,
    named_patch_refs,
)
from adt_ai.shared.queries import diff_tables as shared_diff_queries
from adt_ai.shared.sql_identifiers import safe_identifier

# Aliases kept for tests that exercise gateway interactions through the runner.
# Re-exported from the shared sweep since ADT #356, so a caller keying a fake
# gateway off this name matches the statement the sweep actually issues.
DIFF_TABLES_QUERY = shared_diff_queries.DIFF_TABLES_QUERY
VIEW_COLUMNS_QUERY = queries.VIEW_COLUMNS_QUERY


class PatchWorkspace:
    """The project's patch tree, and every operation that reads or writes it.

    ``config`` is optional so a test can build a workspace over a bare
    ``tmp_path``, which is what most of the suite does. Production always passes
    the loaded config, because ``patch_root`` is a project's own answer since ADT
    #430 and a workspace that guessed it would look in the wrong folder.
    """

    def __init__(self, root: Path, config: dict[str, Any] | None = None) -> None:
        self.root = root
        self.config = config or {}
        self.patch_root = settings.patch_root(root, self.config)

    def discover(self, ref: str | None = None) -> list[PatchFolder]:
        return discover_patch_folders(
            self.patch_root, ref=ref, folder_re=settings.patch_folder_re(self.config)
        )

    def resolve(self, ref: str | None) -> PatchFolder | None:
        """The existing folder ``ref`` names exactly, or ``None`` for a new code.

        Jan settled `#267` by pointing at what he had already asked for: `-patch`
        supports the **id, the folder name and the card name**, and polices nothing
        else about a code's shape. That makes resolution (not validation) the
        whole job, and it is the same exactness `#255` gave the deploy path, so both
        go through `_is_exact_patch_ref` rather than growing a second rule.

        ``None`` is the ordinary pre-create case: a code naming no folder yet is a
        new patch, not a miss.
        """
        if not ref:
            return None
        matches = [
            folder
            for folder in self.discover(ref=ref)
            if _is_exact_patch_ref(folder, ref)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return None
        candidates = ", ".join(folder.folder for folder in matches)
        raise PatchError(
            f"{ref!r} matches more than one patch folder: {candidates} "
            "- name one of them exactly"
        )

    def next_folder(self, patch_code: str, *, today: date | None = None) -> Path:
        return next_patch_folder(self.patch_root, patch_code, today=today, config=self.config)

    def create_install_script(self, config: dict[str, Any]) -> list[InstallScriptResult]:
        return write_install_script(self.root, config)

    def refresh_plan(
        self,
        ref: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> RefreshPlan:
        folders = self.discover(ref=ref)
        folder = folders[0] if folders else None
        files = folder.files if folder else []
        config = config or {}
        groups = self._patch_contents_groups(folder, config)
        return RefreshPlan(
            folder          = folder.folder if folder else None,
            # The union of the groups, so the flat list and the grouped one are
            # one answer rather than two derivations of it (ADT #443). Falls back
            # to the folder's own `-- FILES:` header only when no install script
            # could be read, which is a folder that cannot be deployed either.
            database_files  = (
                sorted({file for group in groups for file in group.files})
                if groups
                else _refresh_database_files(files, config)
            ),
            apex_components = _refresh_apex_components(files, config),
            groups          = groups,
        )

    def _patch_contents_groups(
        self,
        folder: PatchFolder | None,
        config: dict[str, Any],
    ) -> list[PatchContentsGroup]:
        """One group per install script, its files in the order it links them.

        Read off the generated scripts rather than re-derived from config, the
        same read-the-artifact rule ADT #417 applied to the commit list: the
        script is what a deploy will actually run, so anything else on screen is
        a second opinion about the patch that can disagree with it.

        A script that links no database file contributes no group rather than an
        empty header, and a folder whose scripts cannot be read at all yields no
        groups, which is the caller's signal to fall back.
        """
        if folder is None:
            return []
        groups: list[PatchContentsGroup] = []
        for sql_path in sorted(folder.path.glob("*.sql")):
            try:
                text = sql_path.read_text(encoding="utf-8", errors="replace")
            # defensive: `discover()` above already read every file in this same folder through
            # `parse_patch_folder` with no guard, so a file that fails here would already have
            # failed discovery first
            except OSError:  # pragma: no cover
                continue
            files = [
                path
                for label, path in linked_references(text, self.root, folder.path, config)
                if label == "FILE" and is_database_path(path, config)
            ]
            if not files:
                continue
            groups.append(
                PatchContentsGroup(
                    schema = _deployment_schema(sql_path.name, config),
                    app_id = _deployment_app_id(sql_path.name, config),
                    files  = files,
                )
            )
        return groups

    def archive_patches(
        self,
        config: dict[str, Any],
        *,
        refs: list[str] | None = None,
    ) -> ArchiveResult:
        # A ref is the patch's card number (ADT #268) or a SQL LIKE pattern over
        # the folder's own names (ADT #346).
        #
        # **No ref at all selects NOTHING** (ADT #513). Jan, 2026-08-24: *"When
        # no name was passed (just -archive), then dont archive anything"*. It
        # meant every folder, old ADT's own reading, and the two answers are the
        # whole `patch/` tree apart, so the safe one is the right default and a
        # sweep is asked for by pattern (`-archive %`). The guard sits in the
        # SELECTION rather than at the console, so no caller reaches the sweep by
        # skipping a screen.
        #
        # Selecting nothing is not an error (ADT #355): a command asked to
        # archive what is not there has done what it was asked, and failing would
        # make `-archive` unusable in any script sweeping a legitimately empty
        # pattern. Jan, 2026-08-15: it "should not be treated as error".
        requested = named_patch_refs(refs)
        if not requested:
            return ArchiveResult(folders=[], archive_paths=[], script_archives=[])
        selected = [
            folder
            for folder in self.discover()
            if any(matches_patch_selector(folder, ref) for ref in requested)
        ]
        if not selected:
            return ArchiveResult(folders=[], archive_paths=[], script_archives=[])
        return archive_patch_folders(self.root, config, selected)

    def deployment_plan(
        self,
        config: dict[str, Any],
        *,
        ref: str | None = None,
    ) -> tuple[PatchFolder, list[DeploymentPlanItem]]:
        folder = _select_patch_folder(self.discover(ref=ref), ref)
        plan = [
            DeploymentPlanItem(
                order   = index,
                file    = sql_path.name,
                schema  = _deployment_schema(sql_path.name, config),
                app_id  = _deployment_app_id(sql_path.name, config),
                files   = _countable_file_total(
                    sql_path.read_text(encoding="utf-8", errors="replace"),
                    self.root,
                    folder.path,
                    config,
                ),
                commits = len(folder.commits),
                path    = sql_path,
            )
            for index, sql_path in enumerate(sorted(folder.path.glob("*.sql")), start=1)
        ]
        if not plan:
            raise PatchError(f"patch folder has no deployable SQL files: {folder.folder}")
        return folder, plan

    def deploy_patch(
        self,
        config: dict[str, Any],
        *,
        ref: str | None,
        target_env: str,
        gateway_factory: Callable[[str], Any],
        dev_gateway_factory: Callable[[str], Any] | None = None,
        force: bool = False,
        continue_on_error: bool = False,
        reporter: Any = None,
        apex_target: Any = None,
        apex_version: str | None = None,
        apex_account: str = "",
    ) -> DeploymentRunResult:
        """Deploy every script in the patch folder, reporting progress as it goes.

        The loop itself is ``patch/deploy_run.run_deployment`` since ADT #434 split
        this module at the 20 KB context guard; this stays as the entry point
        every caller and test already knows, and the reporter protocol,
        ``apex_target``, ``apex_version`` and ``apex_account`` are all
        documented there.
        """
        return run_deployment(
            self,
            config,
            ref                 = ref,
            target_env          = target_env,
            gateway_factory     = gateway_factory,
            dev_gateway_factory = dev_gateway_factory,
            force               = force,
            continue_on_error   = continue_on_error,
            reporter            = reporter,
            apex_target         = apex_target,
            apex_version        = apex_version,
            apex_account        = apex_account,
        )

    # `delete_diff_tables` lived here until ADT #356. The sweep is now
    # `shared/diff_tables.drop_diff_tables`, called from the `diff` producer,
    # `export_db` and the connecting `patch` run, and a second implementation
    # reachable from nowhere is how two answers to "which tables are these"
    # start drifting apart.

    def create_database_patch(
        self,
        config: dict[str, Any],
        *,
        patch_code: str,
        records: list[CommitRecord],
        target_env: str | None = None,
        full_app_ids: list[int] | None = None,
        today: date | None = None,
        folder: Path | None = None,
        content_mode: str = CONTENT_MODE_COMMITTED,
        window: list[CommitRecord] | None = None,
        force: bool = False,
        hash_shipped: Mapping[str, str] | None = None,
        hash_commits: Mapping[str, int] | None = None,
        hash_previous: Mapping[str, str] | None = None,
    ) -> DatabasePatchResult:
        """Build the patch folder and report what went into it.

        The build is `patch/build.py` since ADT #576; what a workspace owns is
        the one decision that needs a patch ROOT, which folder is being written,
        and every argument below travels through unchanged.
        """
        # An explicit folder is one the caller already RESOLVED against disk, so a
        # re-create rewrites that exact folder (ADT #289, Jan's call). Without one
        # the code mints its own name, which `next_folder` already makes a rewrite
        # for a same-day same-code patch (`#266`), the two paths agree, they just
        # learn the folder differently.
        return build_database_patch(
            self.root,
            folder or self.next_folder(patch_code, today=today),
            config,
            patch_code    = patch_code,
            records       = records,
            target_env    = target_env,
            full_app_ids  = full_app_ids,
            content_mode  = content_mode,
            window        = window,
            force         = force,
            hash_shipped  = hash_shipped,
            hash_commits  = hash_commits,
            hash_previous = hash_previous,
        )

__all__ = [
    "AlterHelper",
    "Any",
    "ArchiveResult",
    "COMMIT_HASH_LENGTH",
    "CONTENT_MODES",
    "CONTENT_MODE_COMMITTED",
    "CONTENT_MODE_HEAD",
    "CONTENT_MODE_LOCAL",
    "CONTENT_MODE_NOSNAP",
    "Callable",
    "CommitRecord",
    "DIFF_TABLES_QUERY",
    "DatabasePatchResult",
    "DeploymentPlanItem",
    "DeploymentResult",
    "DeploymentRunResult",
    "GitCommitCache",
    "HASH_STAMP_FORMAT",
    "InstallScriptResult",
    "Mapping",
    "PREVIEW_LINE_WIDTH",
    "PatchContentsGroup",
    "PatchError",
    "PatchFolder",
    "PatchRequest",
    "PatchRunner",
    "PatchScripts",
    "PatchWorkspace",
    "Path",
    "ProcessedFile",
    "RefreshPlan",
    "SchemaReport",
    "VIEW_COLUMNS_QUERY",
    "_compile_statement",
    "_countable_file_total",
    "_countable_references",
    "_deployment_app_id",
    "_deployment_error_excerpt",
    "_deployment_payload",
    "_deployment_progress",
    "_deployment_schema",
    "_deployment_succeeded",
    "_drop_helper_sql",
    "_filter_records",
    "_is_exact_patch_ref",
    "_not_deployed_result",
    "_patch_files",
    "_recompile_invalid_objects",
    "_refresh_apex_components",
    "_refresh_database_files",
    "_reject_unresolved_merges",
    "_select_patch_folder",
    "_skipped_deployment_result",
    "_table_alter_sql",
    "_verify_view_columns",
    "_write_deployment_log",
    "_write_generated_patch_scripts",
    "_write_patch_files",
    "_write_snapshots",
    "annotations",
    "archive_patch_folders",
    "build_database_patch",
    "build_reports",
    "collect_patch_scripts",
    "date",
    "datetime",
    "discover_patch_folders",
    "ensure_deploy_log_folder",
    "export_freshness",
    "folder_commit_entries",
    "folder_preview_rows",
    "is_database_path",
    "linked_references",
    "matches_patch_selector",
    "named_patch_refs",
    "next_patch_folder",
    "outstanding_records",
    "preview_rows",
    "preview_rows_from",
    "queries",
    "require_forced_refresh",
    "require_fresh_full_app_exports",
    "reset_deployment_spool",
    "reset_patch_scripts",
    "resolve_full_app_ids",
    "run_deployment",
    "safe_identifier",
    "settings",
    "shared_diff_queries",
    "text_files",
    "time",
    "write_install_script",
    "write_patch_hashes",
]
