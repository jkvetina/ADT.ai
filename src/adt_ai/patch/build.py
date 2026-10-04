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
the ordering rule the four gates below share: `require_forced_refresh` (a folder
already deployed), `require_fresh_full_app_exports` (an APEX full export older
than its own components), `_reject_unresolved_merges` (a file still carrying
conflict markers) and `check_patch_trees` (an APEXlang tree the compiler refuses,
ADT #964). A refusal therefore leaves no folder, no scripts and no snapshots
behind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_drift import DriftReads, changed_applications
from adt_ai.patch.apex_import import AppTarget, one_target_refusal
from adt_ai.patch.apex_validate import ApexlangValidation, check_patch_trees
from adt_ai.patch.content import CONTENT_MODE_COMMITTED, CONTENT_MODE_LOCAL, file_present
from adt_ai.patch.create import (
    _patch_files,
    _write_generated_patch_scripts,
    _write_patch_files,
)
from adt_ai.patch.deploy_driver import write_deploy_driver
from adt_ai.patch.files import _reject_unresolved_merges, _snapshot_link
from adt_ai.patch.full_app import require_fresh_full_app_exports, resolve_full_app_ids
from adt_ai.patch.hashes import write_patch_hashes
from adt_ai.patch.immutables import never_recreated
from adt_ai.patch.layout import apex_app_id, ensure_deploy_log_folder, is_apexlang_path
from adt_ai.patch.models import DatabasePatchResult, PartialPatchReport, PatchError
from adt_ai.patch.report import build_reports
from adt_ai.patch.scripts import collect_patch_scripts, reset_patch_scripts
from adt_ai.patch.snapshots import _write_snapshots
from adt_ai.patch.staleness import export_freshness, require_forced_refresh
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_uncommitted import every_uncommitted_path

HASH_STAMP_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class PatchBuildStages:
    """Optional callbacks so a caller can print `-create`'s screen AS it builds (ADT #988).

    Jan measured the silence this replaces on a real repo: `VALIDATING APEXLANG
    APPS:` closes, then nothing draws for ~6.7s while `export_freshness`,
    `changed_applications`, the generated scripts, the writes and the snapshots
    all run, and the whole rest of the screen -- the warning, both commit
    tables, every per-schema block and the run-scoped warnings -- lands at once
    when `create_database_patch` finally returns. *"YOU WERE DONE with
    validation, you were supposed to print the warning, then relevant commits,
    then deleted objects and other regions as you go."*

    Two hooks, one per point in the build that already has something worth
    showing before the whole thing is over: `validated()` fires the moment
    `check_patch_trees` returns, with the folder and whether `files` resolved
    to anything at all -- the two facts the commit tables need and the ONLY
    two, because neither commit table reads the folder off disk any more
    (ADT #988's own finding: the folder does not exist yet at this point, not
    even for a re-create -- its old content is still what `_write_patch_files`
    is about to overwrite). `reported()` fires once `_write_patch_files` and
    `build_reports` have run, with the per-schema blocks, and BEFORE
    `_write_snapshots` -- the one read still to come that a run-scoped warning
    (`print_undecodable_files`) needs.

    `None` (the default on `build_database_patch`) is today's behaviour for
    every caller but the CLI: nothing printed until the call returns its
    `DatabasePatchResult`, exactly as before this card. The two hooks are
    themselves optional so a caller wanting only one stage is not made to stub
    the other; see `cli/patch_build.py::build_database_patch` for the CLI's own
    closures, and `cli/patch_create_render.py` for what each one prints.
    """

    validated: Callable[[Path, bool], None] | None = None
    reported: Callable[[PartialPatchReport], None] | None = None


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
    gateway_factory: Callable[[str], Any] | None = None,
    files_ws: bool = False,
    hash_tables: Mapping[str, str] | None = None,
    target_app_id: AppTarget | None = None,
    signature_gateway_factory: Callable[[str, str], Any] | None = None,
    validation: ApexlangValidation | None = None,
    stages: PatchBuildStages | None = None,
) -> DatabasePatchResult:
    """Write ``folder`` and report what went into it.

    ``stages`` is the CLI's own hook into a build already in progress (ADT
    #988): ``None`` for every caller but the CLI, which is every caller in this
    codebase but `cli/patch_build.py`, so behaviour is unchanged for all of
    them. See `PatchBuildStages`.

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

    ``target_env`` no longer shapes anything written here (#924 F33): the
    install scripts are the same for every target and `-deploy -target`
    resolves their environment-specific lines. It stays in the signature
    because `-create -target` still names the connection its table ALTERs use,
    which reaches this build through ``gateway_factory``.

    ``target_app_id`` is `-app <id>`'s target half (ADT #935): an APEXlang
    application's `init` and `end` scripts, their SPOOL and so their logs are
    named for the id the import lands on, and `DEPLOY.sql` says between them
    which application is imported as which. One id over two APEXlang
    applications is refused before anything is written, as `-deploy` refuses it.

    ``signature_gateway_factory`` is the drift warning's own connection (ADT
    #962), separate from ``gateway_factory``: the warning reads the export's
    recorded environment, never `-target`, so it needs an environment on top of
    a schema where the ALTER connection above it needs only the latter.

    ``validation`` is the compile gate (ADT #964): the tree of every APEXlang
    application the patch ships goes through `validate`'s compiler before
    anything is written, and a refused one raises ``ApexlangValidationError``.
    `None` compiles nothing, which is every caller but the CLI.
    """
    del target_env
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
    selection = _patch_files(
        root, records, config,
        full_app_ids = full_app_ids,
        files_ws     = files_ws,
        content_mode = content_mode,
    )
    files = selection.files
    if several := one_target_refusal(target_app_id, sorted({
        app_id for path in files
        if is_apexlang_path(path, config) and (app_id := apex_app_id(path, config)) is not None
    })):
        raise PatchError(several)
    # `-files_ws` files no selected commit touched, and the commit each ships from
    # in the committed mode (ADT #812). Every reader of bytes below is handed it.
    pinned = selection.pinned
    _reject_unresolved_merges(root, files)
    # ADT #964: an APEXlang tree the compiler refuses stops the build here, ahead
    # of the ALTER connection and the `mkdir`, so a refusal leaves no folder. Jan:
    # *"It should be validated during patch creation and before you deploy."*
    #
    # The drift warning's live read (ADT #957) runs INSIDE each application's
    # validation row, between its compile and its close (ADT #988, Jan: the row
    # stays open through both, and its clock covers both). Read here, after
    # the rows had closed, it ran under a screen of finished rows.
    drift = DriftReads(root, signature_gateway_factory)
    apex_notes = check_patch_trees(root, config, files, validation, after_compile=drift.read)
    # The moment validation is over, ahead of every read below: the compile
    # gate's own warning already printed itself here (`PatchValidateReporter`
    # no longer holds it), and this is where a streaming caller prints the two
    # commit tables that answer "what is in this patch" (ADT #988). `folder` is
    # what the hook needs and everything it did not have before this call: a
    # fresh `-create` mints it inside `next_folder`, so no earlier point in the
    # CLI knows it either. `bool(files)` says whether anything is about to be
    # written at all: `change_summary_comment` (`patch/summary.py`) writes
    # every one of `records` into every schema script it writes at least one
    # of, unfiltered by which record contributed which file, so a caller
    # deriving the commit tables from `records` alone needs only ONE more fact
    # -- will a schema script exist at all -- to match what a finished build
    # would show. A `files`-less patch (every selected commit resolves no
    # patchable path, `tests/cli/test_patch_create_report_output.py::
    # test_create_lists_a_matching_but_unshippable_commit_as_outstanding`)
    # writes no schema and so carries no commit anywhere; a caller reading
    # `True` here is safe to treat every one of `records` as shipped.
    if stages is not None and stages.validated is not None:
        stages.validated(folder, bool(files))
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
    # The `-deploy` signature gate asked early, as a warning (ADT #957): Jan,
    # *"that is a bit late ... so he can rebase before the deployment fail"*.
    # Answered from the reads made inside the validation rows above (ADT #988),
    # and only from them: an application with no row had no tree to ship. Only
    # a caller that compiles nothing (``validation`` None, no console) still
    # reads here.
    changed_apps = changed_applications(
        root, config, files, signature_gateway_factory,
        already_read=drift.answers if validation is not None else None,
    )
    present_files = {
        path: file_present(
            root, path, config, mode=content_mode, records=records,
            pinned_ref=pinned.get(path),
        )
        for path in files
    }
    folder.mkdir(parents=True, exist_ok=True)
    if config.get("patch_spooling", True):
        # The install script written below opens with a SPOOL into this folder,
        # so it is part of the patch, not deploy-time residue: a hand-run in
        # SQLcl finds it on disk (ADT #270). The environment-free one, because
        # the script names no target (#924 F33); `-deploy` seeds its own.
        ensure_deploy_log_folder(folder, config, None)
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
        # The tables the baseline stored (ADT #857): the version the target
        # holds even when no commit ever did, which is a hand hotfix.
        hash_tables   = hash_tables,
        window        = window,
        # The ALTER half asks Oracle what changed (ADT #753). One connection per
        # schema, opened only if a table in it actually has two versions to
        # compare, so a patch carrying no table opens none.
        gateway_factory = gateway_factory,
    )
    # Only now, past the one step that connects (ADT #923): run before it, a
    # re-create whose ALTER could not reach the database had already deleted
    # the previous build's install scripts and snapshots, and stopped there.
    _reset_generated_artifacts(folder, config)
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
        content_mode = content_mode,
        present_files = present_files,
        # A table or sequence the target already holds is never created again
        # (ADT #830); the same two bases the ALTER writers compared against.
        never_recreated = never_recreated(
            root, files, records, config,
            content_mode  = content_mode,
            hash_previous = hash_previous,
        ),
        # This run's own ALTERs, generated and claimed (ADT #969): every TABLE
        # among them stays linked but commented out. Each runs from its own
        # slot, never reordered (ADT #990).
        generated = generated,
        target_app_id = target_app_id,
    )
    # After every install script is on disk, so it sees exactly what the folder
    # holds; a re-create keeps a person's order unless `-force` (ADT #850).
    deploy_file = write_deploy_driver(folder, config, force=force)
    # Computed here, ahead of `_write_snapshots`, rather than inline in the
    # `return` below (ADT #988): both read only what is already on disk by this
    # point -- `build_reports` reads `sql_files` and `generated`, and
    # `_repo_uncommitted` reads git and this run's own generated paths, neither
    # a snapshot -- so a streaming caller's `reported()` hook fires with the
    # per-schema blocks while the snapshot copy still has ~4s left to run
    # (measured on a real repo), and the `return` below spends the same values
    # rather than asking the same two questions twice.
    reports = build_reports(
        root,
        files,
        sql_files,
        records,
        window if window is not None else records,
        config,
        mode      = content_mode,
        generated = generated,
        present_files = present_files,
    )
    uncommitted = _repo_uncommitted(
        root, folder, set(generated.paths), mode=content_mode
    )
    if stages is not None and stages.reported is not None:
        stages.reported(
            PartialPatchReport(folder=folder, reports=reports, uncommitted=uncommitted)
        )
    undecodable = _write_snapshots(
        root,
        folder,
        files,
        config,
        patch_code   = patch_code,
        content_mode = content_mode,
        records      = records,
        pinned       = pinned,
    )
    # Written LAST and only for a hash-built patch (ADT #447). Two jobs: the
    # folder says what it carried, and its presence is the marker `-deploy` reads
    # to decide whether advancing the baseline is this patch's to do. A
    # commit-built patch writes none and advances nothing, which is how the two
    # modes stay apart without a second flag (Jan, 2026-08-21: "User should not
    # be mixing these modes").
    if hash_shipped is not None:
        carried = set(files)
        write_patch_hashes(
            folder,
            {file: value for file, value in hash_shipped.items() if file in carried},
            hash_commits or {},
            patch_code = patch_code,
            stamp      = datetime.now().strftime(HASH_STAMP_FORMAT),
            # What the patch carried that the baseline held and it does not
            # ship is what it deleted, recorded so the deploy that lands it
            # takes the path out of the baseline (ADT #983).
            deleted    = sorted(
                file for file in carried
                if file in (hash_previous or {}) and file not in hash_shipped
            ),
        )
    return DatabasePatchResult(
        folder            = folder,
        sql_files         = sql_files,
        deploy_file       = deploy_file,
        files             = files,
        scripts           = scripts,
        unresolved_tables = generated.unresolved_tables,
        refused_tables    = generated.refused_tables,
        claimed_tables    = generated.claimed_tables,
        changed_apps      = changed_apps,
        apex_notes        = apex_notes,
        changed_objects   = freshness.changed,
        unclocked_schemas = freshness.unclocked,
        undecodable_files = undecodable,
        # Computed above, ahead of `_write_snapshots`, and spent here rather
        # than re-derived (ADT #988): the templates and per-patch scripts
        # `reports` carries are read back from the `PROMPT -- TEMPLATE:` /
        # `PROMPT -- SCRIPT:` rows the writer emitted, never re-derived from
        # config a second time (ADT #18's shape), and asking git for
        # `uncommitted` twice could only disagree with the answer a streaming
        # caller already printed if the checkout changed mid-build.
        reports           = reports,
        uncommitted       = uncommitted,
    )


def _repo_uncommitted(
    root: Path,
    folder: Path,
    generated_paths: set[str],
    *,
    mode: str,
) -> list[str]:
    """Every file this build's own commit does not yet cover, repo-wide (ADT #967).

    Jan, mid-run: *"if we have uncommitted changes in the repo, it should list
    the files as a file tree ... Looks like you are printing something, but not
    all uncommitted files, why is that?"* The old `WARNING - UNCOMMITTED FILES:`
    asked a narrower question, `SchemaReport.uncommitted`'s own `_uncommitted()`
    (removed by this card) only ever checked the patch's OWN files; this asks
    git about the whole checkout, once, and `cli/patch_create_warnings.py`
    renders whatever comes back as a file tree.

    Two exclusions, both about what THIS build itself just did rather than what
    was already sitting in the checkout dirty: a generated helper this run wrote
    (``generated_paths``, the same set `report.py` excludes from its own,
    narrower listing) and anything under the patch folder this call is in the
    middle of writing -- a brand new `patch/<code>/` is untracked by
    definition, and reporting it here would be the build warning about its own
    output.

    **Silent under `-local`**, the same carve-out the old `_uncommitted` made:
    that mode ships the working tree on purpose, so an uncommitted file there is
    the instruction rather than a surprise.
    """
    if mode == CONTENT_MODE_LOCAL:
        return []
    try:
        folder_prefix = folder.resolve().relative_to(root.resolve()).as_posix() + "/"
    except ValueError:
        # The patch folder sits outside `root` (a caller's own arrangement,
        # never the CLI's): nothing to strip, so every dirty path is reported.
        folder_prefix = None
    return [
        path
        for path in every_uncommitted_path(root)
        if path not in generated_paths
        and (folder_prefix is None or not path.startswith(folder_prefix))
    ]


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
