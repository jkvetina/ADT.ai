"""Orchestration for the ut module: discover ``_UT`` suites, run them, judge them.

The whole module exists because **utPLSQL does not raise when a test fails**.
``ut.run`` reports the failure and returns normally, so a caller that only
watches for an exception sees a clean run. Three shapes of dishonest green are
therefore treated as failures here, not as nothing-to-report:

* tests failed or errored, the obvious one;
* the reporter produced nothing parsable, or no test cases at all, the run did
  not complete, and "no failures" is not "passed";
* nothing matched at all, a suite that stops compiling stops being discovered,
  and an empty green run is exactly what that looks like from the outside.

Everything is driven through the ordinary query gateway, never the read-only
one: running a test writes to utPLSQL's own output buffer, and a
``SET TRANSACTION READ ONLY`` session makes the reporter's data producer fail to
start (ORA-20215) rather than reporting anything.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

from adt_ai.shared.db import QueryGateway
from adt_ai.shared.session_scope import require_database_session
from adt_ai.shared.sql_like import matches_sql_like
from adt_ai.ut import queries
from adt_ai.ut.coverage import build_coverage_report
from adt_ai.ut.inventory import (
    SKIP_INVALID,
    SKIP_NOT_A_SUITE,
    SuitePackage,
    SuiteTest,
    SuiteTiming,
    TestOutcome,
)
from adt_ai.ut.junit import in_declaration_order, parse_junit, row_line, unreported
from adt_ai.ut.naming import UtNaming

# The records a run is described by moved to `ut/session.py` under `#436`, when
# this module crossed the repo's 20 KB per-file context budget. Re-exported
# rather than swept through every import site, so `from adt_ai.ut.runner import
# Ut3Request, Ut3Runner` still resolves; the same facade shape
# `export_db/normalizers.py` keeps over `object_normalizers/`.
from adt_ai.ut.session import Ut3Reporter, Ut3Request, Ut3Result

# The one annotation type that is a runnable test; the rest of what
# get_suites_info returns describes the tree around them (UT_SUITE,
# UT_SUITE_CONTEXT, UT_LOGICAL_SUITE).
_TEST_ITEM_TYPE = "UT_TEST"


class Ut3Runner:
    def __init__(
        self,
        gateway: QueryGateway,
        reporter: Ut3Reporter | None = None,
    ) -> None:
        self.gateway = gateway
        self.reporter = reporter or Ut3Reporter()

    def run(self, request: Ut3Request) -> Ut3Result:
        owner = request.owner.upper()
        naming = request.naming
        # The schema the suites live in, which is the schema under test unless
        # `ut_owner` says otherwise. utPLSQL's annotation cache is keyed by it
        # and `ut.run` takes an owner-qualified path, so getting this wrong is
        # not a narrower run, it is a run that finds nothing.
        ut_owner = naming.owner_for(owner)
        if request.refresh:
            self.reporter.refreshing(ut_owner)
            self.gateway.execute(
                queries.REBUILD_ANNOTATION_CACHE_STATEMENT,
                {"owner": ut_owner},
            )
            self.reporter.refreshed()

        # **`-name` selects the suites to run.** It narrows the run itself, not
        # just the printed rows, and the coverage figures follow from whatever
        # ran, so a filtered run costs less than an unfiltered one and reports
        # less coverage for a package reached only by an excluded suite. That
        # under-report is the accepted cost (Jan, 2026-08-07): a flag that
        # silently means two different things is the worse defect, and the
        # symptom was `-coverage -name APP_INT%` running the whole schema and
        # taking the same 38 seconds as `-name APP%` while appearing to filter.
        self.reporter.discovering(ut_owner)
        packages = self._discover(ut_owner, request.names, naming)

        # **A run with nothing to execute measures nothing**, so it starts no
        # profiler and stops none (`#372`). Coverage is scoped to the packages
        # the run's suites test, and a run whose every suite is unrunnable has
        # no target, so `build_coverage_report` already returned an empty report
        # from those two round trips. They were also the only work such a run
        # did after discovery, with a screen that had nothing left to say, which
        # is what the console guard reported: the honest fix is not to announce
        # them but not to make them.
        measures = any(package.runnable for package in packages)
        # The run id is generated here, not read back from the database, because
        # `coverage_start` takes it as an IN parameter. Reading back "the newest
        # coverage run" instead would pick up a concurrent session's rows.
        coverage_run_id = uuid.uuid4().hex.upper()
        if measures:
            # The profiler is started here, fed by the test run, and read after
            # it, so all three have to land in one database session. A transport
            # that cannot promise that would report `0.0` for every package at
            # exit 0, which reads exactly like untested code (ADT #449).
            require_database_session(self.gateway, "ut coverage")
            self.gateway.execute(
                queries.COVERAGE_START_STATEMENT,
                {"coverage_run_id": coverage_run_id},
            )
        # **Instrumentation starts inside the discovery phase, and the phase
        # closes after it.** The two are one wait from the reader's side, and a
        # run that matched no suite draws no bar, so closing the phase first
        # would leave this call behind a blank line with nothing on screen, the
        # `#359` defect in its smallest form.
        self.reporter.discovered(packages)

        outcomes: list[TestOutcome] = []
        timings: list[SuiteTiming] = []
        # The run so far, **re-folded after every suite** rather than once after
        # the loop (`#670`). The comment that stood here claimed the `finally`
        # below could hand the reporter something renderable when a suite raised,
        # and it could not: `outcomes` and `timings` reached `result` only after
        # the last suite, so an exception on suite k discarded suites 1..k-1,
        # every one of which had already run and been measured. Folding per suite
        # costs one tuple copy each and makes the claim true.
        result = Ut3Result(packages=packages, modules=naming.modules_enabled)
        try:
            for package in packages:
                if not package.runnable:
                    continue
                self.reporter.suite_begin(package)
                # Wall clock around the whole call, so the figure covers the
                # round trip and the suite's own setup, see `SuiteTiming`.
                started_at = time.monotonic()
                try:
                    suite_outcomes = self._run_suite(ut_owner, package)
                except Exception as exc:
                    # **A suite that raises is one red suite, not a lost run.**
                    # utPLSQL does not raise for a failing test, so anything
                    # arriving here is the suite failing to run at all: an ORA on
                    # the producer side, a dropped connection, a `%beforeall`
                    # that blew up. That is the "reported nothing parsable" state
                    # the module docstring already counts as a failure, and
                    # `unreported` is its established shape, one ERROR outcome
                    # per discovered test carrying the cause. Letting it
                    # propagate reported nothing about ANY suite, this one
                    # included, and threw away results the reader had already
                    # sat through.
                    suite_outcomes = unreported(package, f"the suite did not run: {exc}")
                timings.append(
                    SuiteTiming(package=package.name, seconds=time.monotonic() - started_at)
                )
                outcomes.extend(suite_outcomes)
                result = replace(result, outcomes=tuple(outcomes), timings=tuple(timings))
                self.reporter.suite_end(package, suite_outcomes)
            # **Everything the run can already say is said before the three
            # round trips that end and read the profiler.** They are one wait
            # from the reader's side and the console announces it with the
            # header of the table it produces, so this call is inside the `try`
            # rather than the `finally`: on the way out through an exception the
            # error screen is what the reader gets, and a report header standing
            # above it would be a table that never arrives (`#379`).
            self.reporter.measuring_coverage(result)
        finally:
            # Coverage instrumentation lives on the session, not on the call, so
            # an exception between start and stop would keep profiling every
            # later statement this connection runs.
            if measures:
                self.gateway.execute(queries.COVERAGE_STOP_STATEMENT)

        # `owner`, not `ut_owner`: coverage measures the code under test, and the
        # schema holding the suites is a separate question.
        #
        # **The suites come back too.** `ut_match` derives a target name and only
        # the schema's own package list can say whether that name is a package,
        # so the pairing is finished in there and the corrected suites replace
        # the ones discovery guessed (`#436`). Everything downstream reads a
        # suite's target (the `COVERAGE` cell, the module roll-up's line count,
        # the change table), so re-pointing it in one place re-points all of them.
        outcome = build_coverage_report(
            self.gateway,
            owner,
            coverage_run_id,
            packages,
            naming.pattern,
        )
        result = replace(result, packages=outcome.packages, coverage=outcome.report)
        self.reporter.coverage_measured(outcome.report, outcome.packages)
        return result

    def _discover(
        self,
        owner: str,
        names: tuple[str, ...],
        naming: UtNaming,
    ) -> tuple[SuitePackage, ...]:
        # Packages come back A-Z from the dictionary and stay that way; tests are
        # re-sorted below into the order the package spec declares them.
        #
        # The dictionary returns the schema's **test** packages, not its
        # packages: `ut_pattern` is applied by the query, so nothing arrives
        # here to be thrown away.
        rows = self.gateway.fetch_all(
            queries.SUITE_PACKAGES_QUERY, naming.discovery_binds(owner)
        )
        items = self.gateway.fetch_all(queries.SUITE_ITEMS_QUERY, {"owner": owner})
        declaration_order = _declaration_order(
            self.gateway.fetch_all(
                queries.PACKAGE_PROCEDURES_QUERY, naming.selection_binds(owner)
            )
        )

        # `UT_SUITE` rows carried the `%suite` description into
        # `SuitePackage.description` until `#670`; nothing printed it, and the
        # field went with the capture. The tree rows are still skipped one line
        # below, which is all this loop ever needed them for.
        tests_by_package: dict[str, list[SuiteTest]] = {}
        for item in items:
            package = str(item.get("OBJECT_NAME") or "").upper()
            if str(item.get("ITEM_TYPE") or "").upper() != _TEST_ITEM_TYPE:
                continue
            tests_by_package.setdefault(package, []).append(
                SuiteTest(
                    package         = package,
                    name            = str(item.get("ITEM_NAME") or ""),
                    description     = str(item.get("ITEM_DESCRIPTION") or ""),
                    line            = item.get("ITEM_LINE_NO"),
                    disabled        = bool(item.get("DISABLED_FLAG")),
                    disabled_reason = str(item.get("DISABLED_REASON") or ""),
                )
            )

        packages = []
        for row in rows:
            name = str(row.get("OBJECT_NAME") or "")
            if names and not any(matches_sql_like(name, pattern) for pattern in names):
                continue
            status = str(row.get("STATUS") or "")
            tests = _in_spec_order(
                tests_by_package.get(name.upper(), ()),
                declaration_order,
            )
            packages.append(
                SuitePackage(
                    name        = name,
                    status      = status,
                    tests       = tests,
                    skip_reason = _skip_reason(status, tests),
                    # Both derived by Oracle in the discovery query, from
                    # `ut_match` and `ut_module`. Re-deriving either here would
                    # be a second regex engine free to disagree with the one
                    # that selected the row.
                    target      = str(row.get("TARGET_NAME") or ""),
                    module      = str(row.get("MODULE_NAME") or ""),
                )
            )
        return tuple(packages)

    def _run_suite(self, owner: str, package: SuitePackage) -> tuple[TestOutcome, ...]:
        rows = self.gateway.fetch_all(
            queries.RUN_SUITE_QUERY,
            {"path": f"{owner}.{package.name}"},
        )
        document = "\n".join(row_line(row) for row in rows).strip()
        outcomes = parse_junit(package, document)
        if outcomes:
            return in_declaration_order(package, outcomes)
        return unreported(package, document)


def _declaration_order(rows: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    """``(package, procedure) -> subprogram id`` for the suite schema's packages.

    One query for the whole schema rather than one per package: the set is
    small, and the discovery pass already reads the dictionary and the annotation
    cache whole for the same reason. Unfiltered by name because the pattern that
    would filter it lives in Python now, an extra package's procedures in the
    map are never looked up.
    """
    order: dict[tuple[str, str], int] = {}
    for row in rows:
        package = str(row.get("OBJECT_NAME") or "").upper()
        procedure = str(row.get("PROCEDURE_NAME") or "").upper()
        subprogram = row.get("SUBPROGRAM_ID")
        if not package or not procedure or subprogram is None:
            continue
        order.setdefault((package, procedure), int(str(subprogram)))
    return order


def _in_spec_order(
    tests: list[SuiteTest] | tuple[SuiteTest, ...],
    declaration_order: dict[tuple[str, str], int],
) -> tuple[SuiteTest, ...]:
    """Sort a package's tests the way its specification declares them.

    A test the dictionary has no row for sorts last rather than first, an
    unknown position is not position zero, and ties keep the annotation cache's
    own line order, which is the best remaining guess.
    """
    unknown = len(declaration_order) + 1
    return tuple(
        sorted(
            tests,
            key=lambda test: (
                declaration_order.get((test.package.upper(), test.name.upper()), unknown),
                test.line if test.line is not None else 0,
            ),
        )
    )


def _skip_reason(status: str, tests: tuple[SuiteTest, ...]) -> str:
    if status.upper() != "VALID":
        return SKIP_INVALID
    if not tests:
        # No parsed `%test` for a `_UT` package: either the annotation is
        # missing, or the annotation cache predates the package. `-refresh`
        # settles which; either way the package is not a suite and `ut` says
        # nothing about it.
        return SKIP_NOT_A_SUITE
    return ""


__all__ = [
    "QueryGateway",
    "SKIP_INVALID",
    "SKIP_NOT_A_SUITE",
    "SuitePackage",
    "SuiteTest",
    "SuiteTiming",
    "TestOutcome",
    "Ut3Reporter",
    "Ut3Request",
    "Ut3Result",
    "Ut3Runner",
    "UtNaming",
    "_TEST_ITEM_TYPE",
    "_declaration_order",
    "_in_spec_order",
    "_skip_reason",
    "annotations",
    "build_coverage_report",
    "in_declaration_order",
    "matches_sql_like",
    "parse_junit",
    "queries",
    "replace",
    "require_database_session",
    "row_line",
    "time",
    "unreported",
    "uuid",
]
