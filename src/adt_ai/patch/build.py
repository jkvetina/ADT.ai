"""Writing one patch folder: the gates, the files, and what the run reports.

Split out of `runner.py` when ADT #576 pushed that module past the 20 KB context
guard (`tests/contracts/test_context_file_size.py`), the same seam `create.py`
shed to `selection.py` under `#430` and `commit_discovery.py` to
`commit_file_classes.py` under `#429`.

The seam is what each half needs to know. `PatchWorkspace` is about a patch
ROOT: which folders exist, which one a ref names, which one comes next, and what
a deploy does to one. This is about writing a single folder, and the only thing
it needs from the workspace is which folder that is, so the workspace resolves
the name and hands it over.

**Everything that can refuse runs before the first byte is written**, which is
the ordering rule the three gates below share: `require_forced_refresh` (a folder
already deployed), `require_fresh_full_app_exports` (an APEX full export older
than its own components) and `_reject_unresolved_merges` (a file still carrying
conflict markers). A refusal therefore leaves no folder, no scripts and no
snapshots behind.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.content import CONTENT_MODE_COMMITTED, file_present
from adt_ai.patch.create import (
    _patch_files,
    _write_generated_patch_scripts,
    _write_patch_files,
)
from adt_ai.patch.files import _reject_unresolved_merges, _snapshot_link
from adt_ai.patch.full_app import require_fresh_full_app_exports, resolve_full_app_ids
from adt_ai.patch.hashes import write_patch_hashes
from adt_ai.patch.layout import ensure_deploy_log_folder
from adt_ai.patch.models import DatabasePatchResult
from adt_ai.patch.report import build_reports
from adt_ai.patch.scripts import collect_patch_scripts, reset_patch_scripts
from adt_ai.patch.snapshots import _write_snapshots
from adt_ai.patch.staleness import export_freshness, require_forced_refresh
from adt_ai.shared.commit_discovery import CommitRecord

HASH_STAMP_FORMAT = "%Y-%m-%d %H:%M"


def build_database_patch(
    root: Path,
    folder: Path,
    config: dict[str, Any],
    *,
    patch_code: str,
    records: list[CommitRecord],
    target_env: str | None = None,
    full_app_ids: list[int] | None = None,
    content_mode: str = CONTENT_MODE_COMMITTED,
    window: list[CommitRecord] | None = None,
    force: bool = False,
    hash_shipped: Mapping[str, str] | None = None,
    hash_commits: Mapping[str, int] | None = None,
    hash_previous: Mapping[str, str] | None = None,
) -> DatabasePatchResult:
    """Write ``folder`` and report what went into it.

    ``content_mode`` selects which version of each file ships (ADT #280);
    ``window`` is the unfiltered commit window the selection was drawn from,
    which is what `#277`'s newer-commit warning compares against. It defaults to
    ``records``, so an existing caller keeps working, with nothing outside the
    selection to compare against, the warning simply has nothing to say.

    ``force`` is the deployed-patch override (ADT #366). Rewriting a folder that
    has already been deployed is refused without it, and permitted with it, as a
    REFRESH: the install scripts are rebuilt and the deploy logs the folder
    already carries are kept exactly as they are. Its `patch_scripts/` is NOT,
    since ADT #508, because that folder is an input to the build rather than a
    record of one; `reset_patch_scripts` empties it below.
    """
    # Ahead of every write, and ahead of the file selection, so a refusal costs
    # nothing and leaves nothing behind.
    require_forced_refresh(folder, force=force)
    # `-app`'s selection half is resolved once, here, so every reader below is
    # handed the same list and a bare flag means the same thing at all of them
    # (ADT #576, renamed by #592).
    full_app_ids = resolve_full_app_ids(records, config, full_app_ids)
    # Beside the guard above it and ahead of the `mkdir` for the same reason: a
    # full export older than the components it claims to carry is Jan's stop
    # rather than a warning.
    require_fresh_full_app_exports(records, config, full_app_ids)
    if force:
        # A refresh rebuilds the scripts too since ADT #508; see that function.
        reset_patch_scripts(root, folder, config, patch_code=patch_code)
    selection = _patch_files(root, records, config, full_app_ids=full_app_ids)
    files = selection.files
    _reject_unresolved_merges(root, files)
    # A file the database has already moved past is snapshotted as-is, and
    # deploying it reverts the live object (ADT #261). Read here rather than in
    # the CLI so every caller of `create_database_patch` gets the answer, and
    # read from the selected `files` so it describes THIS patch.
    #
    # It refused outright until `#468`; it is carried to the console as
    # `WARNING - OBJECTS CHANGED:` now, on Jan's call (*"it will be just a
    # warning, not a show stopper"*), which is why the build continues below with
    # the answer in hand rather than stopping on it.
    freshness = export_freshness(root, config, files)
    present_files = {
        path: file_present(root, path, config, mode=content_mode, records=records)
        for path in files
    }
    _reset_generated_artifacts(folder, config)
    folder.mkdir(parents=True, exist_ok=True)
    if config.get("patch_spooling", True):
        # The install script written below opens with a SPOOL into this folder,
        # so it is part of the patch, not deploy-time residue: a hand-run in
        # SQLcl gets the same working folder `-deploy` does (ADT #270).
        ensure_deploy_log_folder(folder, config, target_env)
    generated = _write_generated_patch_scripts(
        root,
        files,
        records,
        config,
        patch_code    = patch_code,
        # Hash mode's ALTER base is the version the baseline recorded, not a walk
        # over selected commits it has none of (ADT #447). The window is handed
        # over with it because that is where the recorded version is looked up,
        # by content hash rather than by commit number.
        hash_previous = hash_previous,
        window        = window,
    )
    # The scripts move INTO the patch before the install script is written,
    # because that is where `_script_payload` now reads them from (ADT #309).
    # Recovery runs inside this call, so a re-create that finds the source folder
    # already emptied by the first one still ships its scripts.
    scripts = collect_patch_scripts(
        root,
        folder,
        config,
        patch_code = patch_code,
        records    = records,
        generated  = generated.paths,
    )
    sql_files = _write_patch_files(
        root,
        folder,
        files,
        records,
        config,
        patch_code   = patch_code,
        full_app_ids = full_app_ids,
        target_env   = target_env,
        content_mode = content_mode,
        present_files = present_files,
    )
    _write_snapshots(
        root,
        folder,
        files,
        config,
        patch_code   = patch_code,
        content_mode = content_mode,
        records      = records,
    )
    # Written LAST and only for a hash-built patch (ADT #447). Two jobs: the
    # folder says what it carried, and its presence is the marker `-deploy` reads
    # to decide whether advancing the baseline is this patch's to do. A
    # commit-built patch writes none and advances nothing, which is how the two
    # modes stay apart without a second flag (Jan, 2026-08-21: "User should not
    # be mixing these modes").
    if hash_shipped is not None:
        write_patch_hashes(
            folder,
            {file: value for file, value in hash_shipped.items() if file in set(files)},
            hash_commits or {},
            patch_code = patch_code,
            stamp      = datetime.now().strftime(HASH_STAMP_FORMAT),
        )
    return DatabasePatchResult(
        folder            = folder,
        sql_files         = sql_files,
        files             = files,
        scripts           = scripts,
        unresolved_tables = generated.unresolved_tables,
        changed_objects   = freshness.changed,
        unclocked_schemas = freshness.unclocked,
        # Built last, and off the install scripts already on disk: the templates
        # and per-patch scripts it reports are read back from the
        # `PROMPT -- TEMPLATE:` / `PROMPT -- SCRIPT:` rows the writer emitted,
        # never re-derived from config a second time (ADT #18's shape).
        reports           = build_reports(
            root,
            files,
            sql_files,
            records,
            window if window is not None else records,
            config,
            mode      = content_mode,
            generated = generated,
            present_files = present_files,
        ),
    )


def _reset_generated_artifacts(folder: Path, config: dict[str, Any]) -> None:
    """Rebuild the generated artifact set while retaining scripts and history.

    Root SQL files are also a supported hand-authored surface. Only drivers
    carrying our exact opening header are ours to replace. Their FILE rows name
    the generated snapshots; unrelated files in that folder remain untouched.
    """
    for script in folder.glob("*.sql"):
        rows = script.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(rows) < 3 or rows[0] != "PROMPT --;" or not (
            rows[1].startswith("PROMPT -- PATCH ") and rows[2].startswith("PROMPT -- SCHEMA ")
        ):
            continue
        for row in rows:
            if not row.startswith("PROMPT -- FILE: "):
                continue
            target = folder / _snapshot_link(row.removeprefix("PROMPT -- FILE: "), config)
            # An edited header is data, never authority to delete outside this
            # patch. Resolve symlinks too before trusting the containment.
            if target.resolve().is_relative_to(folder.resolve()) and target.is_file():
                target.unlink()
        script.unlink()
