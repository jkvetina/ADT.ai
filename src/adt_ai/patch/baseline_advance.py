"""Which shipped files a deploy actually landed, for the hash baseline (ADT #447).

`-deploy` advances the target's baseline for a hash-built patch, and each rule
here is a way that record could otherwise start lying. The caller, the CLI's
`advance_baseline`, owns when the question is asked and what the console says;
this owns the answer and the write.

**A database file lands with its own install script.** `install_script_name`
re-derives the script the build linked it from, rather than trusting a second
recorded copy of that grouping, so a `-continue` run that landed one schema and
failed another advances the first schema only: advancing the whole patch would
mark the failed schema's objects live, and the next hash patch would leave them
out.

**An APEXlang file lands with the import, never with a script** (ADT #923). The
build links such an application as `<SCHEMA>.<APP>.init.sql` + `.end.sql`
around the SQLcl import (ADT #735), and the `end` half runs whatever the import
did: without `-app` there is no import at all, under `-continue` it runs after a
refused one, a failing scan reverts the application behind it, and `-app 100727`
lands the tree on a sandbox and leaves the application as it was. Each of the
four recorded the new tree as deployed, and the next `-create -hash` then left
the application out for good. So the `end` half is necessary and not enough:
the application's `> BUILDING APP` row has to have SUCCEEDED in place, and the
application must be neither reverted nor failed by its scan.

**"In place" is read off the import's own row.** Its `app_id` is the id the tree
landed on (`ApexImportItem.target_id`), which is the application's own id under
a bare `-app` and under `-app` naming that same id, and another id under a
retarget, whose sandbox the baseline does not track. A scan that never ran
holds nothing back, the way it fails no deploy (`deploy_verify_scan` off, a
release that cannot scan). A failing one holds the tree back even when
`-continue` waived it: the application is broken as landed, so the next hash
patch ships it again rather than leaving it out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_deploy import BUILDING_APP_ROW
from adt_ai.patch.baseline_tables import working_tree_tables, write_baseline_tables
from adt_ai.patch.hashes import merge_into_baseline, read_patch_hashes, resolve_baseline_path
from adt_ai.patch.layout import apex_app_id, is_apexlang_path
from adt_ai.patch.selection import apex_owner_schemas, install_script_name


def merge_landed_files(
    root       : Path,
    config     : dict[str, Any],
    folder     : Path,
    results    : Iterable[Any],
    *,
    target_env : str,
    stamp      : str,
    scans      : Iterable[Any] = (),
    reverts    : Iterable[Any] = (),
) -> tuple[Path, int] | None:
    """Merge what the patch in ``folder`` landed into ``target_env``'s baseline.

    Answers the baseline's path and how many hashes moved, or ``None`` when the
    run advances nothing: a commit-built patch carries no `hashes.log` (Jan,
    2026-08-21: *"For normal patches you dont touch it."*), and a run that landed
    none of the files it shipped has nothing to record.
    """
    shipped, commits = read_patch_hashes(folder)
    if not shipped:
        return None
    advancing = landed_files(root, shipped, results, config, scans=scans, reverts=reverts)
    if not advancing:
        return None
    path = resolve_baseline_path(root, config, target_env, None)
    _written, advanced = merge_into_baseline(
        path,
        advancing,
        {file: number for file, number in commits.items() if file in advancing},
        target_env = target_env,
        stamp      = stamp,
    )
    # The tables this deploy landed move with their lines (ADT #857), read off
    # the working tree only where it still holds the bytes that shipped. A
    # scope claiming nothing, because a deploy advances and never removes.
    write_baseline_tables(
        path, working_tree_tables(root, config, advancing), covered=lambda file: False
    )
    return path, advanced


def landed_files(
    root    : Path,
    shipped : Mapping[str, str],
    results : Iterable[Any],
    config  : dict[str, Any],
    *,
    scans   : Iterable[Any] = (),
    reverts : Iterable[Any] = (),
) -> dict[str, str]:
    """The ``shipped`` files this deploy landed, with the hash each shipped at.

    ``results`` are the deploy's rows, install scripts and imports together;
    ``scans`` and ``reverts`` are the run's `ApexScanReport`s and `ApexRevert`s.
    """
    succeeded = [row for row in results if getattr(row, "status", "") == "SUCCESS"]
    scripts = {getattr(row, "file", "") for row in succeeded}
    applications = {
        row.app_id for row in succeeded
        if getattr(row, "file", "") == BUILDING_APP_ROW and row.app_id is not None
    }
    applications -= {report.app_id for report in scans if report.failed}
    applications -= {revert.app_id for revert in reverts}
    # Read once rather than per file: the lookup is a sqlite read.
    owners = apex_owner_schemas(root)
    return {
        file: value
        for file, value in shipped.items()
        if install_script_name(file, config, owners) in scripts
        and (not is_apexlang_path(file, config) or apex_app_id(file, config) in applications)
    }


__all__ = ["landed_files", "merge_landed_files"]
