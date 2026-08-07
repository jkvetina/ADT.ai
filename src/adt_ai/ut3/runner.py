"""Orchestration for the ut3 module: discover ``_UT`` suites, run them, judge them.

The whole module exists because **utPLSQL does not raise when a test fails**.
``ut.run`` reports the failure and returns normally, so a caller that only
watches for an exception sees a clean run. Three shapes of dishonest green are
therefore treated as failures here, not as nothing-to-report:

* tests failed or errored — the obvious one;
* the reporter produced nothing parsable, or no test cases at all — the run did
  not complete, and "no failures" is not "passed";
* nothing matched at all — a suite that stops compiling stops being discovered,
  and an empty green run is exactly what that looks like from the outside.

Everything is driven through the ordinary query gateway, never the read-only
one: running a test writes to utPLSQL's own output buffer, and a
``SET TRANSACTION READ ONLY`` session makes the reporter's data producer fail to
start (ORA-20215) rather than reporting anything.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from adt_ai.shared.db import QueryGateway
from adt_ai.shared.sql_like import matches_sql_like
from adt_ai.ut3 import queries
from adt_ai.ut3.inventory import (
    BLOCKED_NATIVE,
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    RESULT_SKIPPED,
    SKIP_INVALID,
    SKIP_NOT_A_SUITE,
    CoverageReport,
    PackageCoverage,
    SuitePackage,
    SuiteTest,
    SuiteTiming,
    TestOutcome,
)
from adt_ai.ut3.junit import in_declaration_order, parse_junit, row_line, unreported
from adt_ai.ut3.queries.suites import UT_PACKAGE_SUFFIX

# The one annotation type that is a runnable test; the rest of what
# get_suites_info returns describes the tree around them (UT_SUITE,
# UT_SUITE_CONTEXT, UT_LOGICAL_SUITE).
_TEST_ITEM_TYPE = "UT_TEST"
_SUITE_ITEM_TYPE = "UT_SUITE"


@dataclass(frozen=True)
class Ut3Request:
    owner    : str
    names    : tuple[str, ...] = ()
    refresh  : bool = False
    coverage : bool = False


@dataclass(frozen=True)
class Ut3Result:
    packages : tuple[SuitePackage, ...] = field(default_factory=tuple)
    outcomes : tuple[TestOutcome, ...] = field(default_factory=tuple)
    # None when coverage was never requested — distinct from an empty report,
    # which means it was requested and nothing came back.
    coverage : CoverageReport | None = None
    timings  : tuple[SuiteTiming, ...] = field(default_factory=tuple)

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
        # A zero-test run is a failure, never an empty pass — see the module
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
    """Streaming hooks so the console can print a suite's row before it blocks.

    The no-op base keeps non-console callers (and every test that does not care)
    unchanged; the CLI swaps in a console reporter.
    """

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
        if request.refresh:
            self.gateway.execute(
                queries.REBUILD_ANNOTATION_CACHE_STATEMENT,
                {"owner": owner},
            )

        # **`-name` selects the suites to run, in every mode.** It used to be
        # dropped here under `-coverage` and applied only to the printed rows, on
        # the argument that coverage of a package can come from any suite and
        # narrowing the run would under-report it. That argument is sound and it
        # lost anyway (Jan, 2026-08-07): a flag that silently means two different
        # things is the worse defect, and the symptom was that
        # `-coverage -name ICT_INT%` ran the whole schema and took the same 38
        # seconds as `-name ICT%` while appearing to filter. The under-report is
        # the accepted cost — a package reached only by a suite the pattern
        # excludes now reads lower than the truth.
        packages = self._discover(owner, request.names)
        self.reporter.discovered(packages)

        # The run id is generated here, not read back from the database, because
        # `coverage_start` takes it as an IN parameter. Reading back "the newest
        # coverage run" instead would pick up a concurrent session's rows.
        coverage_run_id = uuid.uuid4().hex.upper() if request.coverage else ""
        if coverage_run_id:
            self.gateway.execute(
                queries.COVERAGE_START_STATEMENT,
                {"coverage_run_id": coverage_run_id},
            )

        outcomes: list[TestOutcome] = []
        timings: list[SuiteTiming] = []
        try:
            for package in packages:
                if not package.runnable:
                    continue
                self.reporter.suite_begin(package)
                # Wall clock around the whole call, so the figure covers the
                # round trip and the suite's own setup — see `SuiteTiming`.
                started_at = time.monotonic()
                suite_outcomes = self._run_suite(owner, package)
                timings.append(
                    SuiteTiming(package=package.name, seconds=time.monotonic() - started_at)
                )
                outcomes.extend(suite_outcomes)
                self.reporter.suite_end(package, suite_outcomes)
        finally:
            # Coverage instrumentation lives on the session, not on the call, so
            # an exception between start and stop would keep profiling every
            # later statement this connection runs.
            if coverage_run_id:
                self.gateway.execute(queries.COVERAGE_STOP_STATEMENT)

        coverage = (
            self._coverage(owner, coverage_run_id, packages, tuple(outcomes), request.names)
            if coverage_run_id
            else None
        )
        return Ut3Result(
            packages = packages,
            outcomes = tuple(outcomes),
            coverage = coverage,
            timings  = tuple(timings),
        )

    def _coverage(
        self,
        owner: str,
        coverage_run_id: str,
        packages: tuple[SuitePackage, ...],
        outcomes: tuple[TestOutcome, ...],
        names: tuple[str, ...],
    ) -> CoverageReport:
        """Every package in the schema, with what the run measured about it.

        The schema listing leads and the coverage rows are joined onto it, never
        the other way round: coverage data only describes packages that were
        executed, so a coverage-first report silently omits the untested package
        the reader opened the report to find.

        ``names`` filters this listing with the same patterns that already chose
        the suites in ``run`` — the flag means one thing in both modes, so the
        report lists the packages the reader named and the run executed the
        suites they named.
        """
        measured = {
            str(row.get("PACKAGE_NAME") or "").upper(): row
            for row in self.gateway.fetch_all(
                queries.PACKAGE_COVERAGE_QUERY,
                {"coverage_run_id": coverage_run_id, "owner": owner},
            )
        }
        blocked = _blocked_reasons(
            self.gateway.fetch_all(queries.PACKAGE_COMPILE_SETTINGS_QUERY)
        )
        verdicts = _verdicts_by_target(outcomes)

        listed = []
        for row in self.gateway.fetch_all(queries.SCHEMA_PACKAGES_QUERY):
            name = str(row.get("OBJECT_NAME") or "")
            if names and not any(matches_sql_like(name, pattern) for pattern in names):
                continue
            found = measured.get(name.upper(), {})
            verdict = verdicts.get(name.upper(), {})
            blocks_total = int(found.get("BLOCKS_TOTAL") or 0)
            listed.append(
                PackageCoverage(
                    name           = name,
                    lines          = int(row.get("LINES") or 0),
                    passed         = verdict.get(RESULT_PASSED, 0),
                    failed         = verdict.get(RESULT_FAILED, 0),
                    errored        = verdict.get(RESULT_ERRORED, 0),
                    blocks_total   = blocks_total,
                    blocks_covered = int(found.get("BLOCKS_COVERED") or 0),
                    lines_covered  = int(found.get("LINES_COVERED") or 0),
                    # A reason explains silence. Oracle having collected blocks
                    # for this package is the answer to "could it be measured",
                    # so a prerequisite that predicts otherwise loses to the data
                    # rather than overwriting it.
                    blocked_reason = "" if blocks_total else blocked.get(name.upper(), ""),
                )
            )
        return CoverageReport(packages=tuple(listed))

    def _discover(self, owner: str, names: tuple[str, ...]) -> tuple[SuitePackage, ...]:
        # Packages come back A-Z from the dictionary and stay that way; tests are
        # re-sorted below into the order the package spec declares them.
        rows = self.gateway.fetch_all(queries.SUITE_PACKAGES_QUERY)
        items = self.gateway.fetch_all(queries.SUITE_ITEMS_QUERY, {"owner": owner})
        declaration_order = _declaration_order(
            self.gateway.fetch_all(queries.PACKAGE_PROCEDURES_QUERY)
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


def _verdicts_by_target(outcomes: tuple[TestOutcome, ...]) -> dict[str, dict[str, int]]:
    """``A_UT``'s verdicts are reported on package ``A``'s row.

    **This is a naming-convention lookup, not an execution fact.** Block coverage
    records which blocks ran, never which test ran them, so no data source can
    say "this test covered that package". A suite that exercises a package other
    than its namesake therefore contributes blocks to that package's coverage and
    no verdicts to its row. That asymmetry is honest — the blocks were measured,
    the attribution was inferred — and collapsing it would mean inventing an
    owner for every test.

    ``SKIPPED`` has no column, so a ``%disabled`` test lands in none of the
    three; its own result row in a plain run still reads ``SKIPPED``.
    """
    verdicts: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        name = outcome.package.upper()
        if not name.endswith(UT_PACKAGE_SUFFIX):
            continue
        target = name[: -len(UT_PACKAGE_SUFFIX)]
        counts = verdicts.setdefault(target, {})
        counts[outcome.result] = counts.get(outcome.result, 0) + 1
    return verdicts


def _blocked_reasons(rows: list[dict[str, object]]) -> dict[str, str]:
    """Compile settings that explain why Oracle collected nothing for a package.

    Only ``PLSQL_CODE_TYPE = NATIVE`` qualifies: native compilation strips the
    instrumentation, so the unit produces no ``dbmspcc_blocks`` rows and the
    report would otherwise show a bare `-` indistinguishable from untested code.

    ``PLSQL_OPTIMIZE_LEVEL > 1`` was here until a live run disproved it — see
    ``inventory.BLOCKED_NATIVE``. The caller applies whatever comes back only to
    packages with no measurement, so this map can never suppress a real figure.
    """
    reasons: dict[str, str] = {}
    for row in rows:
        name = str(row.get("PACKAGE_NAME") or "").upper()
        if not name:
            continue
        if str(row.get("PLSQL_CODE_TYPE") or "").upper() == "NATIVE":
            reasons[name] = BLOCKED_NATIVE
    return reasons


def _declaration_order(rows: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    """``(package, procedure) -> subprogram id`` for every ``_UT`` package.

    One query for the whole schema rather than one per package: the `_UT` set is
    small, and the discovery pass already reads the dictionary and the annotation
    cache whole for the same reason.
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

    A test the dictionary has no row for sorts last rather than first — an
    unknown position is not position zero — and ties keep the annotation cache's
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
