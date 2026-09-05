"""Rendering for `patch -deploy`: the progress table, the listing, the stanzas.

Split out of ``commands_patch_deploy.py`` when that module crossed the 20 KB
context guard (ADT #273), the same seam that file was itself created on. What
stays behind is connection work: gateway factories and the connection block.
Everything here is pure output, takes no gateway, and opens no database, so the
console shape can be changed without reading a line of connection code.

ADT #434 split it once more at the same guard: the column geometry went to
``patch_deploy_layout`` and the live reporter to ``patch_deploy_reporter``, both
re-exported below so nothing that already imports them from here has to move.
"""

from __future__ import annotations

# ruff: noqa: F401 - re-exports keep the pre-split import path working.
import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    COMMIT_HASH_LENGTH,
    PatchError,
    PatchRequest,
    PatchRunner,
    PatchWorkspace,
    folder_commit_entries,
    preview_rows_from,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli.context import _display
from adt_ai.cli.context_errors import _project_relative
from adt_ai.cli.patch_deploy_layout import (
    DEPLOY_COLUMNS,
    DEPLOY_NUMERIC,
    DEPLOY_STATUSES,
    _deployment_layout,
    _deployment_min_widths,
    _deployment_row_values,
    _deployment_rows,
    _files_cell,
    _timer_cell,
)
from adt_ai.cli.patch_deploy_reporter import ConsoleDeployReporter
from adt_ai.cli.patch_preview_render import RELEVANT_COMMITS_HEADER
from adt_ai.export_db.render import _commit_stdout
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult, ViewMismatch
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.object_list import print_object_rows


def _deploy_preview_records(
    args: argparse.Namespace,
    root: Path,
    ref: str | None,
    scan_limit: int,
) -> list[CommitRecord]:
    """The same commit scan a bare `patch` runs, never fatal under `-deploy`.

    Without `-deploy` an empty scan is a real failure and raises `NO COMMITS
    FOUND`, because the run exists to build a patch from those commits. Under
    `-deploy` the patch already exists on disk: the scan is there to describe it,
    so a repo that is not a git checkout, a missing git, or a scan that no longer
    reaches the patch degrades to the folder's own header rather than refusing to
    deploy a reviewed artifact.

    ``ref`` and ``scan_limit`` are passed in rather than read off ``args``: since
    ADT #350 the name can arrive on `-deploy` or on `-create`, and since #351 the
    scan depth is a config key, so neither is a field on the namespace any more.
    """
    try:
        return PatchRunner().run(
            PatchRequest(
                root         = root,
                commit_limit = scan_limit,
                patch_code   = ref,
                search_terms = args.search,
            )
        )
    except (subprocess.CalledProcessError, OSError):
        return []


def print_deploy_patch_contents(
    workspace: PatchWorkspace,
    config: dict[str, object],
    args: argparse.Namespace,
    root: Path,
    ref: str | None = None,
    scan_limit: int = 100,
) -> None:
    """The patch listing `-deploy` owes before it touches a database.

    Old ADT printed the patch on every `-patch <code>` run, deploy or not:
    `show_matching_commits` (patch.py:230) ran first, then `create_patch` printed
    `PATCH OVERVIEW:` (patch.py:291), and only then did `deploy_patch` open
    connections (patch.py:296). ADT.ai printed nothing at all between the banner
    and the connection block. Jan, 2026-08-10: "I want it the same way as if you
    would run just -patch, so header, patch, connect, deploy."
    """
    try:
        folder, _plan = workspace.deployment_plan(config, ref=ref)
    except PatchError:
        # Say nothing and let `deploy_patch` raise: it fails on the same lookup
        # with the message written for it, and a half-printed listing above that
        # error would only add noise.
        return
    # No `Patch folder: <path>` line. `#273` added one here in the same commit
    # `#274` deleted `Next patch folder:` from the preview, the same fact, the
    # same position, one word of difference (Jan, 2026-08-10: "NOBODY ASKED YOU
    # FOR THIS, ACTUALLY I ALREADY TOLD YOU TO REMOVE IT"). `#255`'s requirement
    # is met without it: the section header below names the RESOLVED folder, not
    # the string that selected it, so a prefix match onto the wrong patch is
    # still visible in the run.
    #
    # The folder's own `-- COMMITS:` header first, and a re-scan only when the
    # folder records none (ADT #417). Jan, 2026-08-20: under `-deploy` "you dont
    # list recent commits, just commits which are part of the patch you are
    # deploying". The header is the patch's own record of what went into it,
    # written when it was built; the scan is a re-derivation from a commit window
    # that has moved since, so the two can disagree and only one of them is a
    # fact about the artifact that is about to run. The scan stays as the
    # fallback for a folder built before the header took its current shape, which
    # is the case the fallback was already there for, pointed the other way.
    entries = folder_commit_entries(folder) or [
        (record.number, record.commit_hash[:COMMIT_HASH_LENGTH], record.summary)
        for record in reversed(_deploy_preview_records(args, root, ref, scan_limit))
    ]
    # One empty line, the same gap `-create` gives it (ADT #451). Both callers
    # move together or the two screens disagree about one header, which is the
    # drift `#443` closed between these commands.
    print_adt_header(RELEVANT_COMMITS_HEADER)
    print_adt_table(preview_rows_from(entries), columns=["#", "MESSAGE"])


def print_deployment_table(
    results: Sequence[DeploymentResult], plan: Sequence[DeploymentPlanItem]
) -> None:
    """The whole table at once, the fallback for a run that streamed nothing.

    A SKIPPED deploy never enters the script loop, and a caller that passes no
    reporter never drives one, so the finished table is still the right output
    there. It goes through the same layout helper as the streamed render, which
    is what stops the two from drifting apart.
    """
    rows = _deployment_rows(results)
    layout = _deployment_layout(rows, plan)
    print()
    print(layout.header_line())
    print(layout.separator_line())
    for row in rows:
        print(layout.row_line([row[column] for column in DEPLOY_COLUMNS]))
    print()
    _commit_stdout()

def _print_view_mismatches(mismatches: Sequence[ViewMismatch]) -> None:
    """Old ADT's warning, advice and table, not one line of prose (ADT #278).

    `patch.py:2604-2606` printed `WARNING: VIEW COLUMNS MISMATCHED!`, the single
    line of advice that says how to fix it, and one row per mismatched COLUMN.
    ADT.ai printed `View column mismatches:` with a bare
    `  - schema.view: [...] != [...]` line per view: no section header (SOP
    §Console output contract requires every titled section to go through
    `print_adt_header`), no advice, and two Python list reprs where the answer is
    which column moved.

    `SCHEMA` is the one addition to old ADT's column set. Old ADT verified a
    single schema per run; ADT.ai verifies every schema in the deploy plan, so
    without it two rows for the same view name in different schemas are
    indistinguishable.

    One deliberate divergence, declared rather than made silently: old ADT's title
    was `WARNING: VIEW COLUMNS MISMATCHED!`, and ADT.ai's own header contract ends
    every titled section with a colon (`tests/contracts/test_header_alignment.py`).
    The `!` loses; the advice line below carries the urgency that word did.
    """
    rows: list[dict[str, object]] = []
    for mismatch in mismatches:
        actual = list(mismatch["actual"])
        expected = list(mismatch["expected"])
        for index in range(max(len(actual), len(expected))):
            actual_name = actual[index] if index < len(actual) else ""
            expected_name = expected[index] if index < len(expected) else ""
            if actual_name == expected_name:
                continue
            rows.append(
                {
                    "SCHEMA": mismatch["schema"],
                    "VIEW_NAME": mismatch["view"],
                    # Position, not a dictionary COLUMN_ID: `_view_column_names`
                    # already sorted by COLUMN_ID and dropped it, and Oracle
                    # numbers view columns 1..n contiguously, so the two agree.
                    "ID": index + 1,
                    "COLUMN_NAME": actual_name,
                    "EXPECTED": expected_name,
                }
            )
    if not rows:
        return
    print_adt_header("WARNING - VIEW COLUMNS MISMATCHED:")
    print("  use column aliases inside of the query, not on the header")
    print_adt_table(rows)

def _print_still_invalid_objects(still_invalid: Sequence[tuple[str, str, str]]) -> None:
    """`INVALID OBJECTS:`, the half of the recompile the run never reported.

    The post-deploy pass issues one `ALTER ... COMPILE` per invalid object and
    nothing else. A compile that fails leaves the object invalid and raises
    nothing, so a deploy whose package bodies never came back green still printed
    `SUCCESS` and closed, and `docs/patch_deploy.md` described the recompile as
    part of the outcome (`#658`). This is that outcome.

    `INVALID OBJECTS:` is `recompile`'s own header for the same list, deliberately
    reused: `console.md` §Header shape treats one header spelled two ways by two
    commands as the defect, not the reuse. The name carries its schema because a
    deploy verifies every schema in the plan in one section, the same reason
    `WARNING - VIEW COLUMNS MISMATCHED:` above it grew a `SCHEMA` column.

    Silent when everything came back valid, which is the ordinary run: a section
    saying "nothing is wrong" on every deploy is a section people stop reading.
    """
    if not still_invalid:
        return
    print_adt_header("INVALID OBJECTS:")
    print_object_rows(
        (object_type, f"{schema}.{object_name}")
        for schema, object_type, object_name in still_invalid
    )
    print()


def _print_deployment_errors(results: Sequence[DeploymentResult]) -> None:
    """One stanza per failed script: what SQLcl refused, and where the rest is.

    ADT #272. Before this, an ERROR row was the whole report, the transcript was
    captured, written to the log, and dropped from the console, so the command
    said a deploy failed and named neither the error nor the file holding it.

    A stanza, never a seventh column: the text is the answer here, and free-text
    prose in a table cell destroys the layout at 80 columns (SOP §Console output
    contract). `-continue` can fail several scripts, so every ERROR row gets one.
    """
    for result in results:
        if getattr(result, "status", "") != "ERROR":
            continue
        print_adt_header("DEPLOYMENT ERROR:", result.file)
        excerpt = getattr(result, "error_excerpt", ()) or ()
        for line in excerpt:
            print(f"  {line}")
        if not excerpt:
            # The run failed on a missing success marker with nothing that parses
            # as an error, the log is the only place left to look, so say so
            # rather than printing an empty section.
            print("  no error text in the SQLcl output, read the full log")
        log_path = getattr(result, "log_path", None)
        if log_path is not None:
            print()
            print(f"  LOG: {_display(log_path)}")

def _print_apex_scans(reports: Sequence[Any], root: Path) -> None:
    """`VERIFYING APPLICATIONS:`, what the post-deploy scan found (`#676`).

    Unlike every other section in this file, a clean result still prints its row.
    The whole point of the feature is that "the deploy said SUCCESS" stopped
    being the last word, so a reader has to be able to see that the question WAS
    asked; silence here would be indistinguishable from the behaviour this
    replaced, which is exactly the thing that let a broken application ship.

    One row per application, its findings under it. The findings ARE the answer,
    so they are stanza lines rather than a table column, the same call
    `_print_deployment_errors` makes for the same reason: an `ORA-` message in a
    cell destroys the layout at 80 columns.
    """
    if not reports:
        return
    # Not "VERIFYING DEPLOYED APPLICATIONS:". `DEPLOYED` is a word Jan struck
    # from this command's output (2026-08-10, the invented column), and
    # `tests/cli/test_patch_deploy_progress.py` guards the whole run's text for
    # it, not just the table header. The guard is right and the header moved.
    print_adt_header("VERIFYING APPLICATIONS:")
    for report in reports:
        summary = (
            f"{len(report.findings)} error(s) in {report.analyzed} fragments"
            if report.findings
            else f"{report.analyzed} fragments, no errors"
        )
        print(f"  APP {report.app_id} | {report.status} | {summary}")
        # The reason under the row, for every outcome that is not a plain
        # success (`#701`): `FAILED`, `EMPTY` and `UNSUPPORTED` all print a row
        # that looks quiet, and the line under it is what says which of the
        # three the reader is looking at.
        if report.reason:
            print(f"    {report.reason}")
        for finding in report.findings:
            print(f"    {finding.line()}")
        if report.log_path:
            print(f"    LOG: {_project_relative(Path(report.log_path), root)}")
    print()


def _print_apex_notes(notes: Sequence[str]) -> None:
    """Applications `-app` shipped and could not import, under `validate`'s header.

    The same `NOTES:` section and the same wording rule as
    `cli/commands_validate.py`: the note names the export that would let the
    import happen, because "no APEXlang tree for app 100" says what is missing
    and not how to get it. Nothing prints when there is nothing to say, so a run
    with every tree in place grows no output at all.
    """
    if not notes:
        return
    print_adt_header("NOTES:")
    for note in notes:
        print(f"  {note}")
    print()

__all__ = [
    "COMMIT_HASH_LENGTH",
    "CommitRecord",
    "ConsoleDeployReporter",
    "DEPLOY_COLUMNS",
    "DEPLOY_NUMERIC",
    "DEPLOY_STATUSES",
    "DeploymentPlanItem",
    "DeploymentResult",
    "PatchError",
    "PatchRequest",
    "PatchRunner",
    "PatchWorkspace",
    "Path",
    "RELEVANT_COMMITS_HEADER",
    "Sequence",
    "ViewMismatch",
    "_commit_stdout",
    "_deploy_preview_records",
    "_deployment_layout",
    "_deployment_min_widths",
    "_deployment_row_values",
    "_deployment_rows",
    "_display",
    "_files_cell",
    "_print_deployment_errors",
    "_print_still_invalid_objects",
    "_print_view_mismatches",
    "_timer_cell",
    "annotations",
    "argparse",
    "folder_commit_entries",
    "preview_rows_from",
    "print_adt_header",
    "print_adt_table",
    "print_deploy_patch_contents",
    "print_deployment_table",
    "print_object_rows",
    "subprocess",
]
