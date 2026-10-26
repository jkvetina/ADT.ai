"""What each module found, as `-json` diagnostics (ADT #1070).

One recorder per kind of finding, beside one another, because the same kinds
reach the screen from more than one command: an APEXlang compile error is
`validate`'s answer and also why `patch -deploy` refused, and a live-application
scan finding is `validate -scan`'s answer and also a deploy's verification. A
second copy per command is how the screen grew eight refusal shapes, so the
JSON gets one recorder per shape from the start.

Every function records into `shared/run_report` and is a no-op outside a `-json`
run, so a call site never tests the flag.

The `patch` engine's own types (`ApexScanReport`, `DeploymentRunResult`) are
read by attribute and named only for the type checker: this module ships in
every release, and a module-scope import of `adt_ai.patch` fails every command
of one built without `patch` (ADT #895, `tests/contracts/
test_partial_release_imports.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adt_ai.shared import run_report
from adt_ai.shared.apexlang_line_endings import PrecheckIssue
from adt_ai.shared.codescan_settings import rule_selected
from adt_ai.validate.codescan_report import Finding
from adt_ai.validate.report import EMPTY, NOT_FOUND, UNRECOGNISED, CompileMessage
from adt_ai.validate.runner import FolderOutcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from adt_ai.patch.apex_scan import ApexScanReport
    from adt_ai.patch.models import DatabasePatchResult, DeploymentRunResult

#: The Oracle code a message leads with, which is the tool's own name for it.
_ORACLE_CODE = re.compile(r"\b(?:ORA|PLS|SP2|PLW)-\d{4,5}\b")

#: What a file opening on a BOM is, the precheck's own words. Not `_MESSAGE`:
#: that suffix names a refusal headline (`tests/contracts/test_error_headlines.py`).
BOM_FINDING = "starts with a UTF-8 byte order mark, which codescan reads as clean"


def compile_messages(
    tree     : Path,
    messages : Iterable[CompileMessage],
    severity : str,
    *,
    app      : int | None = None,
) -> None:
    """The APEXlang compiler's records, each pointing at the file it names."""
    for message in messages:
        run_report.diagnostic(
            severity,
            message.type or "COMPILE",
            message.message,
            file   = tree / message.file if message.file else tree,
            line   = message.line,
            column = message.column,
            app    = app,
        )


def validate_folders(folders: Sequence[FolderOutcome]) -> None:
    """Every compiled tree's warnings and errors, and the trees that had none to read."""
    folder_diagnostics(folders)
    run_report.result(
        folders  = len(folders),
        failed   = sum(1 for folder in folders if folder.report.failed),
        errors   = sum(len(folder.report.errors) for folder in folders),
        warnings = sum(len(folder.report.warnings) for folder in folders),
    )


def folder_diagnostics(folders: Sequence[FolderOutcome]) -> None:
    """`validate_folders` without its counts, for `patch`'s refused trees.

    `patch` names its own `result` keys, so the compile gate's refusal records
    the compiler's lines and leaves the counts to the verb (ADT #1070).
    """
    for folder in folders:
        target, report = folder.target, folder.report
        compile_messages(target.path, report.warnings, "warning", app=target.app_id)
        compile_messages(target.path, report.errors, "error", app=target.app_id)
        if report.errors:
            continue
        if report.outcome == UNRECOGNISED:
            run_report.diagnostic(
                "error", UNRECOGNISED, report.raw.strip(), file=target.path, app=target.app_id,
            )
        elif report.outcome == EMPTY:
            run_report.diagnostic(
                "error", "NO_APEXLANG_FILES", "no APEXlang files, export it again",
                file=target.path, app=target.app_id,
            )
        elif report.outcome == NOT_FOUND:
            run_report.diagnostic(
                "error", NOT_FOUND, "folder not found", file=target.path, app=target.app_id,
            )


def notes(entries: Iterable[str | PrecheckIssue]) -> None:
    """A run's `NOTES:` and precheck rows. Each still fails the run it sits in."""
    for entry in entries:
        if isinstance(entry, PrecheckIssue):
            run_report.diagnostic("warning", "PRECHECK", entry.description, file=entry.folder)
        else:
            run_report.diagnostic("warning", "NOTE", entry)


def oracle_code(text: str, default: str) -> str:
    """The first `ORA-`/`PLS-`/`SP2-` code in ``text``, ``default`` when it names none."""
    match = _ORACLE_CODE.search(text)
    return match.group(0) if match else default


# ------------------------------------------------- live-application scans


def apex_scans(
    reports : Sequence[ApexScanReport],
    *,
    waived  : bool = False,
    listing : str  = "scans",
) -> None:
    """What `APEX_APP_OBJECT_DEPENDENCY.SCAN` answered, for `validate -scan` and `-deploy`.

    Every component it could not compile is its own finding, typed by the ORA
    code its message leads with. A scan that verified nothing (`FAILED`,
    `EMPTY`) is one error carrying the reason the row prints, because a quiet
    row there is not a pass (ADT #701); `UNSUPPORTED` fails nothing, so it is a
    warning. ``waived`` is `-continue` waiving a failing scan (`#749`): the
    findings stand, and they no longer fail the run.
    """
    severity = "warning" if waived else "error"
    for report in reports:
        for finding in report.findings:
            error = " ".join(str(finding.error_message or "").split())
            run_report.diagnostic(
                severity,
                oracle_code(error, report.status),
                f"{finding.property_name}: {error}",
                object_type = finding.component_type,
                object_name = finding.component_name,
                app         = report.app_id,
                page        = finding.page_id,
            )
        if not report.findings and report.reason:
            run_report.diagnostic(
                severity if report.failed else "warning",
                report.status,
                report.reason,
                app  = report.app_id,
                page = report.page_id,
            )
    run_report.listing(listing, [_scan_row(report) for report in reports])


def _scan_row(report: ApexScanReport) -> dict[str, Any]:
    """One scan as the row `SCANNING APPLICATIONS:` prints it, page and log when it has them."""
    row: dict[str, Any] = {
        "app"      : report.app_id,
        "status"   : report.status,
        "analyzed" : report.analyzed,
    }
    if report.page_id is not None:
        row["page"] = report.page_id
    if report.log_path:
        row["log"] = report.log_path
    return row


def validate_scan(
    reports : Sequence[ApexScanReport],
    tabs    : Sequence[tuple[int, str, str]],
) -> None:
    """`validate -scan`'s answer: the scans, the Static IDs 26.2 refuses, the counts."""
    apex_scans(reports)
    for app_id, component_type, static_id in tabs:
        # Its own type, since the compile scan cannot see it (ADT #1065).
        run_report.diagnostic(
            "error",
            "STATIC_ID_TAB",
            "Static ID holds a Tab, which APEX 26.2 refuses to save",
            object_type = component_type,
            object_name = static_id,
            app         = app_id,
        )
    run_report.result(
        mode           = "scan",
        failed         = sum(1 for report in reports if report.failed),
        errors         = sum(len(report.findings) for report in reports),
        static_id_tabs = len(tabs),
    )


# ------------------------------------------------------------------ codescan


def codescan_tree(
    tree     : Path,
    listed   : Sequence[Finding],
    *,
    severity : str,
    known    : Sequence[Finding] = (),
    blame    : Mapping[str, str] | None = None,
) -> None:
    """One tree's codescan findings: what the gate listed, then what its baseline holds.

    ``severity`` is the listed findings': an error when they fail the run, a
    warning under `codescan_fail_on: none` or `patch_codescan: warn`. A known
    finding is always a warning, the problem the baseline already accepted.
    ``blame`` names who changed a new finding's file last (ADT #1023), as its hint.
    """
    for finding in listed:
        _codescan_finding(tree, finding, severity, (blame or {}).get(finding.file))
    for finding in known:
        _codescan_finding(tree, finding, "warning")


def known_findings(
    findings : Iterable[Finding],
    listed   : Sequence[Finding],
    rules    : tuple[str, ...],
) -> list[Finding]:
    """What the baseline already holds: in the `codescan_rules` profile, not listed.

    By identity rather than equality: two findings differing only by position
    are one baseline key, and `compare` decides which of them is the new one.
    """
    shown = {id(finding) for finding in listed}
    return [
        finding for finding in findings
        if rule_selected(finding.rule, rules) and id(finding) not in shown
    ]


def _codescan_finding(
    tree     : Path,
    finding  : Finding,
    severity : str,
    hint     : str | None = None,
) -> None:
    run_report.diagnostic(
        severity,
        finding.rule,
        finding.message,
        file        = tree / finding.file,
        line        = finding.line,
        column      = finding.column,
        # A component-level finding names its component and no position.
        object_name = finding.target or None,
        hint        = hint,
    )


def codescan_boms(tree: Path, files: Iterable[str], severity: str = "error") -> None:
    """Each file the precheck found opening on a BOM, which fails the run it sits in."""
    for path in files:
        run_report.diagnostic(severity, "BOM", BOM_FINDING, file=tree / path)


def codescan_unread(tree: Path, raw: str, severity: str = "error") -> None:
    """SQLcl's own words for output the parser could not read, never swallowed."""
    run_report.diagnostic(severity, UNRECOGNISED, raw.strip(), file=tree)


# --------------------------------------------------------------------- patch


def refused_trees(folders: Sequence[FolderOutcome], mode: str) -> None:
    """The trees `patch`'s compile gate refused, the compiler's own lines."""
    folder_diagnostics(folders)
    run_report.result(mode=mode, failed=len(folders))


def patch_preview(mode: str, records: Sequence[Any] | None, patch: str | None) -> None:
    """The commits a bare or named `patch` lists, newest as the scan returned them.

    ``records`` is ``None`` under `-hash`, which prints no commit table (ADT
    #447): its answer is `changed_files`, and its carrier record is no commit.
    """
    run_report.result(mode=mode, patch=patch)
    if records is None:
        return
    run_report.listing("commits", [
        {
            "number"  : record.number,
            "id"      : record.id,
            "date"    : record.date,
            "author"  : record.author,
            "summary" : record.summary,
            "files"   : len(record.files) + len(record.deleted),
        }
        for record in records
    ])


def patch_hashed(
    mode     : str,
    baseline : Path,
    files    : int,
    rows     : Iterable[Mapping[str, object]],
) -> None:
    """`-hash`: the baseline it read and each file the working tree disagrees with.

    ``rows`` are `CHANGED FILES:`'s own; a file no commit in reach touched has
    no commit, null here where the screen leaves the cell blank.
    """
    run_report.result(mode=mode, baseline=str(baseline), baseline_files=files)
    run_report.listing("changed_files", [
        {
            "file"   : row["FILE"],
            "status" : row["STATUS"],
            "commit" : None if row["COMMIT"] == "" else row["COMMIT"],
        }
        for row in rows
    ])


def patch_baseline(path: Path, files: int, counts: Mapping[str, int] | None) -> None:
    """`-baseline`: the file written, its size, and what moved since the one it replaced.

    A first baseline has nothing to compare, so it carries the total alone,
    exactly as its table does.
    """
    run_report.result(mode="baseline", baseline=str(path), files=files)
    if counts is not None:
        run_report.result(**{status.lower(): count for status, count in counts.items()})


def patch_folders(rows: Iterable[Mapping[str, object]]) -> None:
    """A `FOLDER | STATUS` table, under the keys every `result` uses."""
    run_report.listing("patch_folders", [
        {"folder": row["FOLDER"], "status": row["STATUS"]} for row in rows
    ])


def patch_created(result: DatabasePatchResult, root: Path) -> None:
    """`-create`'s folder and files, and every run-scoped warning its screen closes on.

    The warnings keep their headers' words as types, so a reader who has seen
    `WARNING - NO TABLE DIFF:` finds `NO_TABLE_DIFF` (ADT #1070).
    """
    run_report.result(mode="create", folder=str(result.folder), scripts=len(result.sql_files))
    run_report.listing("patch_files", list(result.files))
    run_report.listing("uncommitted", list(result.uncommitted))
    for report in result.reports:
        # `WARNING - OUTDATED FILES:`, which prints per schema as the build
        # reports; read here off the same reports, the rule `print_outdated` uses.
        for item in report.files:
            if not item.newer or item.commit_number is None:
                continue
            newer = "; ".join(f"{number}) {summary}" for number, summary in item.newer)
            run_report.diagnostic(
                "warning", "OUTDATED_FILE",
                f"ships commit {item.commit_number}, older than "
                f"{len(item.newer)} newer commit(s): {newer}",
                file   = root / item.path,
                schema = report.schema,
                app    = report.app_id,
            )
    for signatures in result.changed_apps:
        unknown = signatures.verdict == "UNKNOWN"
        run_report.diagnostic(
            "warning",
            "NO_SIGNATURE" if unknown else "APP_CHANGED",
            "no recorded signature to compare the target with"
            if unknown else "changed on the target since your export",
            app = signatures.app_id,
        )
    for drift in result.release_drifts:
        run_report.diagnostic(
            "warning", "APEX_RELEASE",
            f"exported on APEX {drift.tree}, the target runs {drift.target}",
            app = drift.app_id,
        )
    notes(result.apex_notes)
    for stale in result.changed_objects:
        run_report.diagnostic(
            "warning", "OBJECT_CHANGED",
            f"changed in the database at {stale.last_ddl}, after its export at {stale.exported}",
            file        = root / stale.file,
            schema      = stale.schema,
            object_type = stale.object_type,
            object_name = stale.object_name,
        )
    for schema in result.unclocked_schemas:
        run_report.diagnostic(
            "warning", "NO_DATABASE_CLOCK",
            "no recorded database UTC offset, so its exports were not compared",
            schema = schema,
        )
    for path in result.unresolved_tables:
        run_report.diagnostic(
            "warning", "NO_TABLE_BASELINE",
            "no table version in reach, so no ALTER was generated", file=root / path,
        )
    for path, reason in result.refused_tables:
        run_report.diagnostic("warning", "NO_TABLE_DIFF", reason, file=root / path)
    for path, where in result.undecodable_files:
        run_report.diagnostic(
            "warning", "NOT_UTF8", f"neither UTF-8 nor repo_encoding: {where}", file=root / path,
        )
    for path in result.scripts.unknown:
        run_report.diagnostic(
            "warning", "UNKNOWN_SCRIPT",
            "in a slot no patch_map group produces, so it does not ship", file=path,
        )
    for path in result.scripts.ignored:
        run_report.diagnostic(
            "warning", "IGNORED_SCRIPT", "not committed, so it does not ship", file=path,
        )


#: What a revert did, when its own reason is empty.
_REVERTS = {
    "RESTORED" : ("info", "imported back from the backup taken before the deploy"),
    "FAILED"   : ("error", "the backup could not be imported back"),
}


def patch_deployed(result: DeploymentRunResult) -> None:
    """`-deploy`'s outcome: each failed script, the objects left broken, the scans.

    A failed script's file is the install script in the patch folder; the
    row the APEX import adds is a step rather than a file, so it is named as
    the object instead. The full SQLcl transcript is the log the hint names.
    """
    folder = result.folder.path
    for script in result.results:
        if script.status != "ERROR":
            continue
        excerpt = "\n".join(script.error_excerpt) or (
            "no error text in the SQLcl output, read the full log"
        )
        on_disk = (folder / script.file).is_file()
        run_report.diagnostic(
            "error",
            oracle_code(excerpt, script.status),
            excerpt,
            file        = folder / script.file if on_disk else None,
            object_name = None if on_disk else script.file,
            schema      = script.schema or None,
            app         = script.app_id,
            hint        = f"full log: {script.log_path}" if script.log_path else None,
        )
    for view in result.view_mismatches:
        run_report.diagnostic(
            "warning", "VIEW_MISMATCH",
            f"columns differ from DEV: expected {', '.join(view['expected'])}, "
            f"got {', '.join(view['actual'])}",
            schema = view["schema"], object_type = "VIEW", object_name = view["view"],
        )
    for schema, object_type, name in result.still_invalid:
        # A warning: the read is schema-wide, so it never fails the run (`#658`).
        run_report.diagnostic(
            "warning", "INVALID", "still invalid after the recompile",
            schema = schema, object_type = object_type, object_name = name,
        )
    apex_scans(result.apex_scans, waived=result.scan_waived, listing="apex_scans")
    for revert in result.apex_reverts:
        severity, message = _REVERTS.get(revert.outcome, ("warning", "no revert ran"))
        run_report.diagnostic(
            severity, f"REVERT_{revert.outcome}", revert.reason or message,
            app  = revert.app_id,
            hint = f"report: {revert.log_path}" if revert.log_path else None,
        )
    notes(result.apex_notes)
    run_report.result(
        mode              = "deploy",
        status            = result.status,
        folder            = str(folder),
        failed            = sum(1 for script in result.results if script.status == "ERROR"),
        recompiled        = len(result.recompiled),
        still_invalid     = len(result.still_invalid),
        view_mismatches   = len(result.view_mismatches),
        apex_scans_failed = sum(1 for report in result.apex_scans if report.failed),
        reverts           = len(result.apex_reverts),
        scan_waived       = result.scan_waived,
    )
    run_report.listing("scripts", [
        {
            "order"          : script.order,
            "file"           : script.file,
            "schema"         : script.schema,
            "app"            : script.app_id,
            "status"         : script.status,
            "deployed"       : script.deployed,
            "deployed_total" : script.deployed_total,
            "seconds"        : script.seconds,
            "log"            : str(script.log_path) if script.log_path else None,
        }
        for script in result.results
    ])


def patch_dropped(
    rows    : Sequence[Mapping[str, object]],
    dropped : str,
    failure : BaseException | None = None,
) -> None:
    """`-drop`'s table, and an error for each application the dictionary still holds.

    ``failure`` is what a drop raised mid-run. The command re-raises it for the
    shared screen, so its words are carried here rather than lost to the row.
    """
    for row in rows:
        if row["STATUS"] == dropped:
            continue
        if row.get("RECEIPT") is not None:
            reason = "application still exists after the drop"
        else:
            # No receipt is the drop that raised.
            reason = str(failure) if failure else "the drop stopped before it finished"
        run_report.diagnostic(
            "error",
            str(row["STATUS"]),
            reason,
            object_name = row["ALIAS"],
            app         = row["APPLICATION"],
        )
    run_report.result(
        mode    = "drop",
        dropped = sum(1 for row in rows if row["STATUS"] == dropped),
        failed  = sum(1 for row in rows if row["STATUS"] != dropped),
    )
    run_report.listing("applications", [
        {
            "app"    : row["APPLICATION"],
            "alias"  : row["ALIAS"],
            "source" : row["SOURCE"],
            "status" : row["STATUS"],
            "log"    : str(row["RECEIPT"]) if row.get("RECEIPT") is not None else None,
        }
        for row in rows
    ])
