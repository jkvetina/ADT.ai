"""What the database modules found, as `-json` data (ADT #1070).

`recompile`, `ut`, `export_db`, `export_data`, `export_apex` and `discovery`
each already hold a structured object before they render it: a compile error,
a failed test, an object Oracle refused, the rows a query returned. Every
recorder here takes that object, never the screen, so a console that improves
cannot change what an agent reads.

Five of the six loop over schemas (`run_schema_sections`), and `result` is one
object for the whole run. A count recorded per schema would leave only the last
schema's figure, so counts go through `add_counts` and lists through
`extend_listing`, which read what an earlier schema recorded and add to it. A
diagnostic carries its `schema` for the same reason: two schemas can hold the
same object name.

Every function is a no-op outside a `-json` run, the `shared/run_report`
contract, so a call site never tests the flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from adt_ai.shared import run_report

if TYPE_CHECKING:  # pragma: no cover - type names only, no import-time cost
    from adt_ai.discovery.runner import DiscoveryResult
    from adt_ai.export_apex.inventory import ApexApplication, ApexOwnerCount, ApexWorkspace
    from adt_ai.export_db.failures import ExportObjectsFailedError
    from adt_ai.export_db.group_moves import GroupMovePlan
    from adt_ai.export_db.writer import ObjectWritePlan
    from adt_ai.recompile.contracts import RecompileRequest, RecompileResult
    from adt_ai.ut.inventory import PackageCoverage
    from adt_ai.ut.session import Ut3Result
    from adt_ai.validate.runner import FolderOutcome

#: The Oracle code a message carries, `ORA-31603` or `PLS-00201`: the tool's own
#: name for the failure, which is what `type` is for.
_ORACLE_CODE = re.compile(r"\b(?:ORA|PLS|PLW|SP2)-\d{4,5}\b")


def add_counts(**counts: int) -> None:
    """Add to `result` rather than overwrite it, so a per-schema loop sums."""
    report = run_report.active()
    if report is None:
        return
    run_report.result(**{
        key: report.summary.get(key, 0) + value for key, value in counts.items()
    })


def extend_listing(name: str, items: Sequence[Any]) -> None:
    """`run_report.listing`, appended to: the first entries kept, the total exact."""
    report = run_report.active()
    if report is None:
        return
    kept = list(report.summary.get(name, []))
    total = report.summary.get(f"{name}_total", 0) + len(items)
    run_report.listing(name, [*kept, *items])
    report.summary[f"{name}_total"] = total


def oracle_code(text: str | None, fallback: str) -> str:
    match = _ORACLE_CODE.search(text or "")
    return match.group(0) if match else fallback


# --- recompile -------------------------------------------------------------


def recompile_result(schema: str, request: RecompileRequest, result: RecompileResult) -> None:
    """One schema's pass, whichever mode ran: the report modes return early in the
    runner too, so each fills only its own fields and the rest stay empty."""
    if request.synonyms:
        _synonyms(schema, result)
    elif request.disabled:
        _disabled(schema, result)
    elif request.jobs:
        _jobs(schema, result)
    elif request.vpd:
        _vpd(schema, result)
    elif request.trailing:
        _trailing(schema, result)
    elif request.mview:
        _mviews(schema, result)
    else:
        _recompiled(schema, result)


def _recompiled(schema: str, result: RecompileResult) -> None:
    # Imported here, not at the top: this module ships in every release, and
    # a module-scope import of one command's code would load it into every
    # other command's run (ADT #895).
    from adt_ai.cli.recompile_reporters import (
        _distinct_compile_errors,
        _extract_compile_error_code,
    )

    for invalid in result.invalid:
        details = [
            detail for detail in result.error_details
            if (detail.object_type, detail.object_name)
            == (invalid.object_type, invalid.object_name)
        ]
        # The same filter the `ERROR - RECOMPILATION FAILED:` stanza applies:
        # cascade rows dropped, a message repeated at several positions kept
        # once at its lowest, so the JSON names what to fix and nothing more.
        errors = _distinct_compile_errors(details)
        for (line, position), message in errors:
            run_report.diagnostic(
                "error",
                _extract_compile_error_code(message) or invalid.error or "COMPILE",
                message,
                schema      = schema,
                object_type = invalid.object_type,
                object_name = invalid.object_name,
                line        = line,
                column      = max(position, 0),
            )
        if not errors:
            # Invalid with no compile error of its own, a dependency's knock-on
            # usually; still a reason the run failed, so still a finding.
            run_report.diagnostic(
                "error",
                invalid.error or "INVALID",
                f"{invalid.object_type} {invalid.object_name} is still invalid",
                schema      = schema,
                object_type = invalid.object_type,
                object_name = invalid.object_name,
            )
    add_counts(
        objects   = sum(row.total for row in result.overview),
        compiled  = len(result.compiled),
        validated = sum(row.validated for row in result.overview),
        invalid   = len(result.invalid),
    )
    extend_listing("overview", [
        {
            "schema"              : schema,
            "object_type"         : row.object_type,
            "total"               : row.total,
            "validated"           : row.validated,
            "invalid"             : row.invalid,
            "missing_identifiers" : row.missing_plscope_identifiers,
            "missing_statements"  : row.missing_plscope_statements,
        }
        for row in result.overview
    ])
    if result.root_causes:
        # The ROOT CAUSES verdict as data: the objects to fix first, then the
        # knock-ons that clear once they compile. Absent on a clean run, as the
        # section is.
        extend_listing("root_causes", [
            {
                "schema"      : schema,
                "object_type" : cause.object_type,
                "object_name" : cause.object_name,
                "root"        : root,
                "cause"       : cause.cause,
                "culprit"     : cause.culprit,
                "blast"       : cause.blast,
                "errors"      : cause.errors,
            }
            for root, causes in (
                (True, result.root_causes.roots), (False, result.root_causes.derived),
            )
            for cause in causes
        ])


def _mviews(schema: str, result: RecompileResult) -> None:
    # A listing's count is its `<name>_total`, so the report modes add only the
    # counts no list already carries: a count and a list under one key would
    # overwrite each other.
    failed = [action for action in result.mview_actions if not action.ok]
    for action in failed:
        run_report.diagnostic(
            "error",
            oracle_code(action.error, action.action),
            action.error or f"{action.action} failed",
            schema      = schema,
            object_type = "MATERIALIZED VIEW",
            object_name = action.object_name,
        )
    add_counts(actions=len(result.mview_actions), failed=len(failed))
    extend_listing("mviews", [{"schema": schema, **asdict(mview)} for mview in result.mviews])


def _synonyms(schema: str, result: RecompileResult) -> None:
    add_counts(invalid=sum(1 for synonym in result.synonyms if synonym.status == "INVALID"))
    extend_listing(
        "synonyms", [{"schema": schema, **asdict(synonym)} for synonym in result.synonyms]
    )


def _disabled(schema: str, result: RecompileResult) -> None:
    extend_listing(
        "disabled", [{"schema": schema, **asdict(item)} for item in result.disabled_objects]
    )


def _jobs(schema: str, result: RecompileResult) -> None:
    failed = [job for job in result.jobs if (job.status or "").upper() == "FAILED"]
    for job in failed:
        # A failed job is today's state of the schema, not this run failing:
        # `-jobs` exits 0 over it, so it is a warning.
        run_report.diagnostic(
            "warning",
            "FAILED",
            job.error or f"{job.job_name} failed",
            schema      = schema,
            object_type = "JOB",
            object_name = job.job_name,
        )
    add_counts(failed=len(failed))
    extend_listing("jobs", [{"schema": schema, **asdict(job)} for job in result.jobs])


def _vpd(schema: str, result: RecompileResult) -> None:
    from adt_ai.recompile.vpd import VpdReport

    # None only from a caller that never read the policies; the screen then
    # renders an empty report, and so does this.
    report = result.vpd or VpdReport()
    # The `WARNING - VPD MISSING FOR <COLUMN>:` rows; the run still exits 0.
    for table in report.missing():
        run_report.diagnostic(
            "warning",
            "VPD_MISSING",
            f"{table} has a {report.column} column and no VPD policy",
            schema      = schema,
            object_type = "TABLE",
            object_name = table,
        )
    protected = report.protected_count()
    add_counts(
        tables         = len(report.tables),
        with_policy    = protected,
        without_policy = len(report.tables) - protected,
    )
    extend_listing(
        "policies", [{"schema": schema, **asdict(item)} for item in report.assignments]
    )


def _trailing(schema: str, result: RecompileResult) -> None:
    failed = [action for action in result.trailing_actions if not action.ok]
    for action in failed:
        run_report.diagnostic(
            "error",
            oracle_code(action.error, "TRAILING"),
            action.error or "trailing whitespace could not be stripped",
            schema      = schema,
            object_type = action.object_type,
            object_name = action.object_name,
        )
    add_counts(
        candidates = len(result.trailing),
        updated    = sum(1 for action in result.trailing_actions if action.ok),
        failed     = len(failed),
    )
    extend_listing("trailing", [
        {"schema": schema, **asdict(action)} for action in result.trailing_actions
    ])


# --- ut --------------------------------------------------------------------


def ut_result(
    schema    : str,
    result    : Ut3Result,
    *,
    coverage  : float | None,
    threshold : float | None,
    below     : Sequence[PackageCoverage],
) -> None:
    """One schema's tests: each failure, the counts, the figure and the gate."""
    for outcome in result.outcomes:
        if outcome.ok:
            continue
        run_report.diagnostic(
            "error",
            outcome.result,
            f"{outcome.test}: {outcome.message}" if outcome.message else outcome.test,
            schema      = schema,
            object_type = "PACKAGE",
            object_name = outcome.package,
        )
    for package in below:
        run_report.diagnostic(
            "error",
            "COVERAGE_GATE",
            f"coverage {package.percent} is below the {threshold:g} gate",
            schema      = schema,
            object_type = package.type,
            object_name = package.name,
        )
    add_counts(
        tests_run = result.tests_run,
        passed    = result.passed,
        failed    = result.failed,
        errored   = result.errored,
        skipped   = result.skipped_tests,
    )
    report = run_report.active()
    if report is None:
        return
    # Keyed by schema: a percentage does not add up across schemas, and the
    # figure an agent compares is the one the schema's own row printed.
    figures = dict(report.summary.get("coverage", {}))
    figures[schema] = coverage
    run_report.result(coverage=figures)
    if threshold is not None:
        run_report.result(
            gate        = threshold,
            gate_passed = report.summary.get("gate_passed", True) and not below,
        )


# --- export_db -------------------------------------------------------------


def export_db_plans(
    schema : str,
    plans  : Sequence[ObjectWritePlan],
    failed : ExportObjectsFailedError | None,
) -> None:
    """What one schema's export did with each object, and what Oracle refused."""
    failures = failed.failures if failed is not None else []
    for failure in failures:
        database_object = failure.database_object
        run_report.diagnostic(
            "error",
            oracle_code(str(failure.error), type(failure.error).__name__),
            str(failure.error),
            schema      = database_object.schema or schema,
            object_type = database_object.object_type,
            object_name = database_object.name,
        )
    # A plan is one file, not one object: a table's `.fix` sidecar and each
    # schema's grant files are plans of their own, so the count says `files`.
    actions = [plan.action for plan in plans]
    add_counts(
        files     = len(plans),
        created   = actions.count("create"),
        updated   = actions.count("update"),
        unchanged = actions.count("unchanged"),
        hashed    = actions.count("hashed"),
        failed    = len(failures),
    )


def export_baseline(counts: Mapping[str, int]) -> None:
    """The `-baseline` table: what the measurement changed in the baseline file."""
    run_report.result(baseline={status.lower(): value for status, value in counts.items()})


def group_moves(plan: GroupMovePlan, *, force: bool, show_unmatched: bool) -> None:
    """`export_db -groups` and `export_data -groups`: the plan, and what it moved.

    One recorder for both because both print through `execute_group_move`.
    `export_data` plans one schema at a time, so the counts and lists add up.
    A collision aborts the move, nothing moves, and the run exits 2.
    """
    for collision in plan.collisions:
        run_report.diagnostic(
            "error",
            "COLLISION",
            "duplicate object names would collide: "
            + ", ".join(str(path) for path in collision.paths),
            object_type = collision.object_type,
            object_name = collision.name,
        )
    applied = force and not plan.collisions
    run_report.result(mode="apply" if force else "preview")
    add_counts(
        planned    = len(plan.moves),
        moved      = len(plan.moves) if applied else 0,
        collisions = len(plan.collisions),
    )
    extend_listing("moves", [
        {
            "object_type" : move.object_type,
            "group"       : move.dest.parent.name,
            "source"      : str(move.source),
            "dest"        : str(move.dest),
        }
        for move in plan.moves
    ])
    # The `WARNING - UNMATCHED FILES` rows, only where the screen lists them:
    # a run that named its groups did not ask about the rest.
    if show_unmatched:
        extend_listing("unmatched", sorted(str(source) for _type, source in plan.unmatched))


# --- export_apex -----------------------------------------------------------


def _application(application: ApexApplication) -> dict[str, Any]:
    return {
        "schema"     : application.owner,
        "workspace"  : application.workspace,
        "app_id"     : application.app_id,
        "alias"      : application.app_alias,
        "name"       : application.app_name,
        "pages"      : application.pages,
        "updated_at" : application.updated_at,
    }


def apex_applications(applications: Sequence[ApexApplication]) -> None:
    """The count is `applications_total`, so a schema with none still records."""
    extend_listing("applications", [_application(item) for item in applications])


def apex_workspaces(workspaces: Sequence[ApexWorkspace]) -> None:
    run_report.listing("workspaces", [asdict(workspace) for workspace in workspaces])


def apex_owner_counts(owner_counts: Sequence[ApexOwnerCount]) -> None:
    run_report.listing("owners", [asdict(owner_count) for owner_count in owner_counts])


def apex_workspace_not_found(workspace: str, source: str) -> None:
    run_report.diagnostic(
        "warning",
        "WORKSPACE_NOT_FOUND",
        f"{workspace} ({source}) matches no workspace on this instance",
    )


def apex_validation(folders: Iterable[FolderOutcome], notes: Iterable[Any]) -> None:
    """`-apexlang`'s compile, every message a warning: it never fails the export."""
    from adt_ai.cli import json_findings

    for folder in folders:
        target, report = folder.target, folder.report
        json_findings.compile_messages(target.path, report.errors, "warning", app=target.app_id)
        json_findings.compile_messages(target.path, report.warnings, "warning", app=target.app_id)
    json_findings.notes(notes)


def _app_id(app_id: str | int) -> str | int:
    # The id as every other diagnostic's `app` carries it, a number.
    return int(app_id) if str(app_id).isdigit() else app_id


def apex_apps_not_found(app_ids: Iterable[str]) -> None:
    for app_id in app_ids:
        run_report.diagnostic(
            "warning", "APP_NOT_FOUND", f"APP {app_id} was not found", app=_app_id(app_id),
        )


def apex_owners_not_configured(owners: Iterable[tuple[str, str, Sequence[str]]]) -> None:
    for app_id, owner, reached_through in owners:
        run_report.diagnostic(
            "warning",
            "SCHEMA_NOT_CONFIGURED",
            f"APP {app_id} is owned by {owner}, reachable through "
            + ", ".join(sorted(reached_through)),
            schema = owner,
            app    = _app_id(app_id),
        )


# --- discovery -------------------------------------------------------------


def discovery_result(result: DiscoveryResult) -> None:
    """Each query's rows are the command's answer, so they ship as data.

    The rows are the ones the screen and the report show, already cut to
    `-limit`; `row_count` is what the query returned before that cut.
    """
    queries: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        entry: dict[str, Any] = {"index": outcome.index, "label": outcome.label}
        if outcome.ok:
            entry["row_count"] = outcome.row_count
            entry["columns"] = list(outcome.columns)
            entry["rows"] = [list(row) for row in outcome.rows]
        else:
            entry["error"] = outcome.error
            # Discovery exits 0 over a failed query, the error lands in the
            # report instead, but for the agent that sent it the query IS the
            # question, so a failure is an error and not a warning.
            run_report.diagnostic(
                "error",
                oracle_code(outcome.error, "QUERY_FAILED"),
                f"{outcome.label}: {outcome.error}",
            )
        queries.append(entry)
    run_report.result(ok=result.ok_count, failed=result.error_count)
    run_report.listing("queries", queries)
