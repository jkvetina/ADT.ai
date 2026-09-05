"""Asking a just-deployed APEX application whether its own SQL still parses.

The gap this closes (ADT #676). A patch deploys an application, every install
script reports SUCCESS, the import reports SUCCESS, and the home page renders
``ORA-00904`` in three regions. Nothing between those two facts ever asked the
application a question: the install script proves the *import* worked, and an
import that writes a region whose query names a column that no longer exists
writes it just as happily as one that does not.

APEX already carries the answer. ``APEX_APP_OBJECT_DEPENDENCY.SCAN`` compiles
every SQL and PL/SQL fragment an application holds -- region sources, LOVs,
processes, validations, computations, dynamic-action bodies, server-side
conditions, column expressions -- and records, per fragment, the objects it
resolved and the error it hit when it could not compile. The second half is the
one nothing in ADT read: a fragment that fails to parse resolves to no object,
so it leaves no dependency row and vanishes from every other ``APEX_USED_*``
view. ``APEX_COMPONENT_ERRORS_QUERY`` reads it directly.

**What this proves and what it does not.** It proves every stored fragment
compiles against the schema as deployed, which is the whole class of "renamed a
column and the page still references it". It does not render a page: an error
that only exists in the query APEX *generates* around a fragment at run time --
the report wrapper, the IR projection -- compiles clean here and still fails in
the browser. A green scan is a necessary condition, never a sufficient one, and
the log says so rather than letting a reader infer otherwise.

Nothing here raises. A scan that cannot run is a report carrying its reason,
the same rule ``apex_deploy.run_apex_imports`` follows beside it: a deploy that
got that far has a table to print, and an exception thrown from a verification
step would take the record of the deploy with it.

**Not raising is not the same as not failing** (ADT #701). Every reason a scan
produced no findings once collapsed into one ``SKIPPED``, and ``SKIPPED`` failed
nothing, so a database that refused mid-scan, and an application nothing ever
looked at, both left the deploy reporting ``SUCCESS`` while the principal
post-deploy check had proved nothing at all. The outcomes are separate now:
``UNSUPPORTED`` is the only one that passes without verifying, and it is the one
an affirmative capability check has to earn.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.dependencies import queries as dependency_queries
from adt_ai.dependencies.component_scan import run_component_scan
from adt_ai.patch import settings
from adt_ai.shared import text_files
from adt_ai.shared.queries.versions import APEX_VERSION_QUERY

#: Printed at the top of every scan log. The scan's reach is wide enough that a
#: clean one reads like a clean bill of health, and it is not one -- see the
#: module docstring. Stated where the reader actually is, not only in the source.
LOG_PREAMBLE = (
    "-- APEX component scan (APEX_APP_OBJECT_DEPENDENCY.SCAN), run after the deploy.",
    "-- Every stored SQL/PL-SQL fragment in the application was compiled against",
    "-- the schema as deployed. This does NOT render a page: an error in the query",
    "-- APEX generates AROUND a fragment (report wrapper, IR projection) compiles",
    "-- clean here and still fails in a browser.",
)


@dataclass(frozen=True)
class ApexScanFinding:
    """One component property the scan could not compile."""

    page_id: int | None
    component_type: str
    component_name: str
    property_name: str
    error_message: str

    def line(self) -> str:
        """The finding as one log row, page first so a reader can go and look."""
        page = f"PAGE {self.page_id}" if self.page_id is not None else "APPLICATION"
        error = " ".join(str(self.error_message or "").split())
        return (
            f"{page} | {self.component_type} | {self.component_name} | "
            f"{self.property_name} | {error}"
        )


#: The application answered, and every fragment it holds compiled.
SCAN_SUCCESS = "SUCCESS"
#: The application answered and named fragments that do not compile.
SCAN_ERROR = "ERROR"
#: The capability check PROVED this instance cannot answer: a release older than
#: 24.2 carries no `error_message` column to read. A fact about the instance
#: rather than about this patch, so it does not fail the deploy, and it is the
#: only outcome allowed to make that claim.
SCAN_UNSUPPORTED = "UNSUPPORTED"
#: The scan was attempted and did not complete: a query, a parse, the helper
#: install, or the database itself. Nothing was verified, so it fails.
SCAN_FAILED = "FAILED"
#: The scan completed and analyzed nothing, with no evidence that nothing is the
#: right answer. Also nothing verified, and it fails for the same reason.
SCAN_EMPTY = "EMPTY"

#: The outcomes that fail a deploy. `UNSUPPORTED` is the only one that does not,
#: and it is the one an affirmative capability check has to earn.
FAILING_OUTCOMES = frozenset({SCAN_ERROR, SCAN_FAILED, SCAN_EMPTY})


@dataclass(frozen=True)
class ApexScanReport:
    """What the scan concluded about one application.

    **Four states, not two** (ADT #701). Every reason a scan produced no
    findings used to collapse into one `SKIPPED`, and `SKIPPED` did not fail a
    deploy, so a database that refused mid-scan, and an application nothing
    ever looked at, both left the run reporting `SUCCESS` with the principal
    post-deploy check having proved exactly nothing. A verification that did not
    happen is not a verification that passed, and only a proven-unsupported
    release gets to be neither.
    """

    app_id: int
    #: Component properties the scan compiled. Zero means the scan produced
    #: nothing, which reads very differently from a scan that ran and found
    #: nothing, so the two are never collapsed into one number.
    analyzed: int = 0
    findings: tuple[ApexScanFinding, ...] = ()
    #: Which of the states above this scan reached.
    outcome: str = SCAN_SUCCESS
    #: Why, in one line, for every outcome that is not a plain success. Printed
    #: under the row and written into the log, so a reader never has to infer it.
    reason: str = ""
    #: Set by the caller once the report has been written down.
    log_path: str = ""

    @property
    def failed(self) -> bool:
        return self.status in FAILING_OUTCOMES

    @property
    def status(self) -> str:
        # Findings outrank the recorded outcome: a report that names components
        # that do not compile is an ERROR whatever else happened around it.
        return SCAN_ERROR if self.findings else self.outcome


@dataclass
class _Collector:
    """The per-application work, kept out of the loop below so it can be read."""

    gateway: Any
    app_id: int
    analyzed: int = 0
    findings: list[ApexScanFinding] = field(default_factory=list)
    #: Pages the application holds, read only when `analyzed` came back zero.
    #: `None` means the read produced no row at all, which is not evidence of
    #: anything and is treated as such.
    pages: int | None = None

    def run(self) -> None:
        # The whole helper lifecycle sits behind one boundary (`#699`): the scan
        # installs `DEPSCAN$<n>#<n>` procedures on the schema and the cleanup
        # that removes them runs in a `finally`, so a scan that raises mid-way
        # leaves none of them behind. A deploy that silently grew helper objects
        # would be a worse bug than the one this feature fixes.
        #
        # The session PL/Scope setting rides along because it is what lets the
        # generated procedures record what they referenced. `dependencies` also
        # calls `plscope.ensure_plscope`, which recompiles every VALID object
        # still missing scope; that is right for an index refresh the user asked
        # for and wrong here, because a verification step must not recompile the
        # schema it is verifying. The session ALTER alone is taken.
        run_component_scan(
            self.gateway,
            self.app_id,
            session_statements = [dependency_queries.PLSCOPE_SESSION_STATEMENT],
        )
        self.analyzed = _first_number(
            self.gateway.fetch_all(
                dependency_queries.APEX_COMPONENT_SCAN_COUNT_QUERY, {"app_id": self.app_id}
            )
        )
        self.findings = [
            ApexScanFinding(
                page_id        = _optional_int(row.get("PAGE_ID")),
                component_type = str(row.get("COMPONENT_TYPE") or ""),
                component_name = str(row.get("COMPONENT_NAME") or ""),
                property_name  = str(row.get("PROPERTY_NAME") or ""),
                error_message  = str(row.get("ERROR_MESSAGE") or ""),
            )
            for row in self.gateway.fetch_all(
                dependency_queries.APEX_COMPONENT_ERRORS_QUERY, {"app_id": self.app_id}
            )
        ]
        # Only when the count came back zero, so the ordinary run costs nothing
        # (ADT #701). A zero has two readings and the count cannot separate them:
        # an application with nothing to analyze, or a verification that did not
        # happen. This read is the one that can settle it, and it is asked of a
        # view the scan did not write.
        if self.analyzed == 0:
            self.pages = _first_number_or_none(
                self.gateway.fetch_all(
                    dependency_queries.APEX_APPLICATION_PAGE_COUNT_QUERY,
                    {"app_id": self.app_id},
                )
            )


def resolve_apex_version(gateway: Any) -> str:
    """The APEX release, asked of the connection the deploy already holds.

    The deploy's own CONNECTING block probes this too, and threading its answer
    down through `commands_patch_deploy` -> `runner` -> `deploy_run` would put a
    signature change in three modules to save one query on the runs that deploy
    an application at all. Asked here instead, on an open connection, and only
    when there is something to scan. Empty on a probe that fails, which
    `supports_apex_used_views` reads as "unknown, try anyway".
    """
    try:
        rows = gateway.fetch_all(APEX_VERSION_QUERY)
    except Exception:  # noqa: BLE001 - an unknown release is not a failed deploy
        return ""
    if not rows:
        return ""
    return str(rows[0].get("VERSION") or rows[0].get("version") or "")


def scan_application(
    gateway: Any,
    app_id: int,
    *,
    apex_version: str | None = None,
) -> ApexScanReport:
    """Scan one application and read back what it could not compile.

    Never raises, and never passes what it did not prove (ADT #701). A release
    the capability check shows is too old is `UNSUPPORTED` and does not fail the
    deploy; a database that refused mid-scan is `FAILED` and does; a scan that
    analyzed nothing is `EMPTY` unless the application is shown to hold nothing
    to analyze. The three used to be one `SKIPPED` that failed nothing, which is
    how a deploy came to report `SUCCESS` on a verification that never ran.

    ``apex_version`` is optional: `None` asks the gateway, which is what the
    deploy does, and an explicit value is how a test pins a release.
    """
    if apex_version is None:
        apex_version = resolve_apex_version(gateway)
    # An unparsable or empty release reads as "unknown, try anyway", so this
    # branch is only taken when the check PROVED the release is older than 24.2.
    # That proof is what buys the outcome that does not fail the deploy.
    if not dependency_queries.supports_apex_used_views(apex_version):
        return ApexScanReport(
            app_id  = app_id,
            outcome = SCAN_UNSUPPORTED,
            reason  = f"APEX {apex_version or 'unknown'} is older than 24.2, which is "
            "the first release whose dictionary reports component scan errors",
        )
    collector = _Collector(gateway, app_id)
    try:
        collector.run()
    except Exception as error:  # noqa: BLE001 - reported as a row, like a script
        return ApexScanReport(
            app_id  = app_id,
            outcome = SCAN_FAILED,
            reason  = f"the scan did not complete, so nothing was verified: {error}",
        )
    if collector.findings or collector.analyzed:
        return ApexScanReport(
            app_id   = app_id,
            analyzed = collector.analyzed,
            findings = tuple(collector.findings),
        )
    return _empty_report(app_id, collector.pages)


def _empty_report(app_id: int, pages: int | None) -> ApexScanReport:
    """A scan that analyzed nothing, judged against what the application holds.

    Zero is accepted only when something OUTSIDE the scan says zero is the whole
    scope, and `apex_application_pages` is that something: an application with no
    page holds no component, so no fragment to compile is the truth rather than a
    verification that did not run. Anything else (pages present, or a page count
    that produced no row and therefore no evidence at all) is `EMPTY` and fails,
    because the alternative is the fail-open shape this replaced.
    """
    if pages == 0:
        return ApexScanReport(
            app_id  = app_id,
            outcome = SCAN_SUCCESS,
            reason  = "the application holds no page, so no component fragment is "
            "the whole of its scope",
        )
    held = "the page count could not be read" if pages is None else f"it holds {pages} page(s)"
    return ApexScanReport(
        app_id  = app_id,
        outcome = SCAN_EMPTY,
        reason  = f"the scan analyzed no component property and {held}, so nothing "
        "about this application was verified",
    )


def scan_log_text(report: ApexScanReport) -> str:
    """The log body for one application's scan."""
    lines = [
        *LOG_PREAMBLE,
        "",
        f"--   APPLICATION      | {report.app_id}",
        f"--   STATUS           | {report.status}",
        f"--   FRAGMENTS SCANNED| {report.analyzed}",
        f"--   ERRORS           | {len(report.findings)}",
        "",
    ]
    # The findings ARE the body when there are any; the reason line follows them
    # rather than replacing them, so a log that could not be written still names
    # what the scan found (`#701` kept them apart for exactly that reason).
    lines.extend(finding.line() for finding in report.findings)
    if report.reason:
        lines.append(f"{report.status}: {report.reason}")
    elif not report.findings:
        lines.append("No component property failed to compile.")
    return "\n".join(lines) + "\n"


def verify_applications(
    app_ids        : Iterable[int],
    gateway_for_app: Callable[[int], Any],
    *,
    apex_version   : str | None,
    log_folder     : Path,
    config         : dict[str, Any],
) -> list[ApexScanReport]:
    """Scan every application this deploy landed, writing one log each.

    ``app_ids`` is already the deduplicated, successfully-deployed set: an
    application installed by a per-app script AND retargeted by an APEXlang
    import is one application and is scanned once. An application whose deploy
    row errored is not in it -- scanning a half-installed application reports
    the half, and the deploy has already failed on its own terms.

    ``gateway_for_app`` hands back a connection the run has ALREADY opened, it
    never opens one. `tests/patch/test_runner_deploy.py` pins that a deploy
    connects to every schema up front and then stops connecting; a verification
    step that reached for the factory again would open a second connection per
    application and break that contract for a gateway it already had.
    """
    return [
        _written(
            scan_application(gateway_for_app(app_id), app_id, apex_version=apex_version),
            log_folder = log_folder,
            config     = config,
        )
        for app_id in sorted(set(app_ids))
    ]


def scanned_app_ids(results: Sequence[Any]) -> list[int]:
    """The applications a finished deploy actually landed.

    Reads the results rather than the plan on purpose: the plan is what the run
    intended, and a run that stopped on script 2 of 5 intended things it never
    did. Both deploy flavours carry `app_id` on their result row -- the per-app
    install script and the APEXlang import -- so one read covers both.
    """
    return sorted(
        {
            int(result.app_id)
            for result in results
            if getattr(result, "app_id", None) and result.status == "SUCCESS"
        }
    )


def _written(report: ApexScanReport, *, log_folder: Path, config: dict[str, Any]) -> ApexScanReport:
    """Write the report beside the deploy's own logs, and record where."""
    try:
        log_folder.mkdir(parents=True, exist_ok=True)
        path = log_folder / settings.apex_scan_log_name(
            config, moment=datetime.now(), app_id=report.app_id
        )
        text_files.write_text(path, scan_log_text(report))
    except OSError as error:
        # An unwritable log folder must not lose the findings themselves: they
        # are already in the report and the console prints them either way. The
        # OUTCOME is left alone too (`#701`): a log nobody could write says
        # nothing about whether the application verified, and folding the two
        # together is what once let a scan carrying findings read as `SKIPPED`.
        lost = f"the log could not be written: {error}"
        return ApexScanReport(
            app_id   = report.app_id,
            analyzed = report.analyzed,
            findings = report.findings,
            outcome  = report.outcome,
            reason   = f"{report.reason}; {lost}" if report.reason else lost,
        )
    return ApexScanReport(
        app_id   = report.app_id,
        analyzed = report.analyzed,
        findings = report.findings,
        outcome  = report.outcome,
        reason   = report.reason,
        log_path = str(path),
    )


def _first_number(rows: Sequence[dict[str, Any]]) -> int:
    """The count query's one column, or 0 when the scan produced no row at all."""
    return _first_number_or_none(rows) or 0


def _first_number_or_none(rows: Sequence[dict[str, Any]]) -> int | None:
    """The same read, keeping "no row at all" apart from a counted zero.

    A `COUNT(*)` always answers exactly one row, so no row is the read itself
    misbehaving rather than a count of nothing, and the empty-scope judgement
    in `_empty_report` turns on precisely that difference (`#701`).
    """
    if not rows:
        return None
    return int(next(iter(rows[0].values())) or 0)


def _optional_int(value: Any) -> int | None:
    """`PAGE_ID`, which is genuinely NULL for an application-level component."""
    return None if value is None else int(value)


__all__ = [
    "FAILING_OUTCOMES",
    "SCAN_EMPTY",
    "SCAN_ERROR",
    "SCAN_FAILED",
    "SCAN_SUCCESS",
    "SCAN_UNSUPPORTED",
    "ApexScanFinding",
    "ApexScanReport",
    "scan_application",
    "scan_log_text",
    "scanned_app_ids",
    "verify_applications",
]
