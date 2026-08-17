"""Orchestration for the ut3 module: discover ``_UT`` suites, run them, judge them.

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
from dataclasses import dataclass, field, replace

from adt_ai.shared.db import QueryGateway
from adt_ai.shared.sql_like import matches_sql_like
from adt_ai.ut3 import queries
from adt_ai.ut3.coverage import build_coverage_report
from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    RESULT_SKIPPED,
    SKIP_INVALID,
    SKIP_NOT_A_SUITE,
    CoverageReport,
    SuitePackage,
    SuiteTest,
    SuiteTiming,
    TestOutcome,
)
from adt_ai.ut3.junit import in_declaration_order, parse_junit, row_line, unreported
from adt_ai.ut3.naming import UtNaming

# The one annotation type that is a runnable test; the rest of what
# get_suites_info returns describes the tree around them (UT_SUITE,
# UT_SUITE_CONTEXT, UT_LOGICAL_SUITE).
_TEST_ITEM_TYPE = "UT_TEST"
_SUITE_ITEM_TYPE = "UT_SUITE"


@dataclass(frozen=True)
class Ut3Request:
    """One ut3 run.

    ``owner`` is the schema under test. Which schema holds its test packages is
    ``naming.owner_for(owner)``, the same value by default, and a different one
    when ``ut_owner`` is configured. The two are kept apart everywhere below:
    suites are discovered and executed in the test schema, coverage is measured
    in the schema under test.
    """

    owner    : str
    names    : tuple[str, ...] = ()
    refresh  : bool = False
    naming   : UtNaming = field(default_factory=UtNaming)


@dataclass(frozen=True)
class Ut3Result:
    packages : tuple[SuitePackage, ...] = field(default_factory=tuple)
    outcomes : tuple[TestOutcome, ...] = field(default_factory=tuple)
    # Always a report, never None: every run measures coverage since card `#291`,
    # so "was it requested" is no longer a question a caller can ask. An empty
    # report means the run measured nothing, and renders as blank cells.
    coverage : CoverageReport = field(default_factory=CoverageReport)
    timings  : tuple[SuiteTiming, ...] = field(default_factory=tuple)
    # `ut_module` is configured, so the renderer should print the module
    # roll-up. A switch rather than a derived "does anything carry a module",
    # for the reason `CoverageReport.modules` gives.
    modules  : bool = False

    def seconds_for(self, package: str) -> float | None:
        """How long that suite took, or None if it never ran.

        None rather than 0.0 for the absent case: a suite that was skipped and a
        suite that finished instantly are different facts, and the renderer is
        the one that decides how each looks.
        """
        for timing in self.timings:
            if timing.package == package:
                return timing.seconds
        return None

    @property
    def passed(self) -> int:
        return self._count(RESULT_PASSED)

    @property
    def failed(self) -> int:
        return self._count(RESULT_FAILED)

    @property
    def errored(self) -> int:
        return self._count(RESULT_ERRORED)

    @property
    def skipped_tests(self) -> int:
        return self._count(RESULT_SKIPPED)

    @property
    def tests_run(self) -> int:
        return len(self.outcomes)

    @property
    def success(self) -> bool:
        # A zero-test run is a failure, never an empty pass, see the module
        # docstring. A package that could not run is **not** counted here: Jan's
        # instruction is that `ut3` ignores a `_UT` package that is INVALID or
        # holds no parsed test, and a command that ignores something cannot also
        # fail the run over it. A schema whose every `_UT` package is unrunnable
        # still fails, but through the empty-outcomes clause above.
        if not self.outcomes:
            return False
        return not (self.failed or self.errored)

    def _count(self, result: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.result == result)


class Ut3Reporter:
    """Streaming hooks so the console can print a phase's label before it blocks.

    The no-op base keeps non-console callers (and every test that does not care)
    unchanged; the CLI swaps in a console reporter.

    **Every hook that opens something has a partner that closes it**, and the
    pairs exist because what sits between them blocks. A run reaches the
    database in four waits, the annotation cache rebuild, discovery, each suite,
    and the coverage measurement, and the reader is entitled to know which one
    they are in. `#359` was the coverage one: it spent 7.4 seconds of a 14
    second run behind the last test row with nothing on screen at all.
    """

    def refreshing(self, owner: str) -> None:
        """`-refresh` only: utPLSQL is about to reparse the schema's annotations.

        The slowest thing this command can be asked to do, and opt-in, so it
        owns a section a normal run never prints.
        """
        return None

    def refreshed(self) -> None:
        return None

    def discovering(self, owner: str) -> None:
        """The dictionary and the annotation cache are about to be read.

        The one wait that is the same in every mode, and each mode announces it
        with the header it was going to print anyway: `UNIT TESTS SUITES:` under
        `-verbose`, `RUNNING TESTS FOR <PATTERNS>:` by default. Neither needs
        anything discovery returns (only the rows under them do), so the header
        leads and the table or the bar fills in behind it (`#379`).
        """
        return None

    def measuring_coverage(self, result: Ut3Result) -> None:
        """The suites are done and the run is about to read what they reached.

        It carries the finished run because the console's answer is to lay down
        everything it can already render, the problem stanzas and the
        `SUMMARY PER SUITE:` header, so the read happens under the header of the
        table it fills. Until `#379` the bar was simply left open through it,
        which is a row reading `100%  0:00:00` standing in for a wait that had
        not started: 9.9 seconds of a 19.3 second run.
        """
        return None

    def coverage_measured(self, coverage: CoverageReport) -> None:
        return None

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        """Every matched suite, before the first one runs.

        The listing has to reach the terminal *before* execution starts, not
        after: a suite run is the slow part, and a reader who is shown what will
        run only once it has finished was never shown it at all.
        """
        return None

    def suite_begin(self, package: SuitePackage) -> None:
        return None

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        return None


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
        # symptom was `-coverage -name ICT_INT%` running the whole schema and
        # taking the same 38 seconds as `-name ICT%` while appearing to filter.
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
        # The run so far, so the `finally` below can hand the reporter something
        # renderable even when a suite raised on the way here.
        result = Ut3Result(packages=packages, modules=naming.modules_enabled)
        try:
            for package in packages:
                if not package.runnable:
                    continue
                self.reporter.suite_begin(package)
                # Wall clock around the whole call, so the figure covers the
                # round trip and the suite's own setup, see `SuiteTiming`.
                started_at = time.monotonic()
                suite_outcomes = self._run_suite(ut_owner, package)
                timings.append(
                    SuiteTiming(package=package.name, seconds=time.monotonic() - started_at)
                )
                outcomes.extend(suite_outcomes)
                self.reporter.suite_end(package, suite_outcomes)
            result = replace(result, outcomes=tuple(outcomes), timings=tuple(timings))
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
        coverage = build_coverage_report(
            self.gateway,
            owner,
            coverage_run_id,
            packages,
            naming.pattern,
        )
        self.reporter.coverage_measured(coverage)
        return replace(result, coverage=coverage)

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

        tests_by_package: dict[str, list[SuiteTest]] = {}
        suite_description: dict[str, str] = {}
        for item in items:
            package = str(item.get("OBJECT_NAME") or "").upper()
            item_type = str(item.get("ITEM_TYPE") or "").upper()
            if item_type == _SUITE_ITEM_TYPE:
                suite_description.setdefault(package, str(item.get("ITEM_DESCRIPTION") or ""))
            if item_type != _TEST_ITEM_TYPE:
                continue
            tests_by_package.setdefault(package, []).append(
                SuiteTest(
                    package         = package,
                    name            = str(item.get("ITEM_NAME") or ""),
                    description     = str(item.get("ITEM_DESCRIPTION") or ""),
                    line            = item.get("ITEM_LINE_NO"),
                    path            = str(item.get("PATH") or ""),
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
                    description = suite_description.get(name.upper(), ""),
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
        order.setdefault((package, procedure), int(subprogram))
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
        # settles which; either way the package is not a suite and `ut3` says
        # nothing about it.
        return SKIP_NOT_A_SUITE
    return ""


__all__ = [name for name in globals() if not name.startswith("__")]
