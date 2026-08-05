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

from dataclasses import dataclass, field
from xml.etree import ElementTree

from adt_ai.shared.db import QueryGateway
from adt_ai.shared.sql_like import matches_sql_like
from adt_ai.ut3 import queries
from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    RESULT_SKIPPED,
    SKIP_INVALID,
    SKIP_NOT_A_SUITE,
    SuitePackage,
    SuiteTest,
    TestOutcome,
)

# The one annotation type that is a runnable test; the rest of what
# get_suites_info returns describes the tree around them (UT_SUITE,
# UT_SUITE_CONTEXT, UT_LOGICAL_SUITE).
_TEST_ITEM_TYPE = "UT_TEST"
_SUITE_ITEM_TYPE = "UT_SUITE"


@dataclass(frozen=True)
class Ut3Request:
    owner   : str
    names   : tuple[str, ...] = ()
    refresh : bool = False


@dataclass(frozen=True)
class Ut3Result:
    packages : tuple[SuitePackage, ...] = field(default_factory=tuple)
    outcomes : tuple[TestOutcome, ...] = field(default_factory=tuple)

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

        packages = self._discover(owner, request.names)
        self.reporter.discovered(packages)
        outcomes: list[TestOutcome] = []
        for package in packages:
            if not package.runnable:
                continue
            self.reporter.suite_begin(package)
            suite_outcomes = self._run_suite(owner, package)
            outcomes.extend(suite_outcomes)
            self.reporter.suite_end(package, suite_outcomes)
        return Ut3Result(packages=packages, outcomes=tuple(outcomes))

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
        document = "\n".join(_row_line(row) for row in rows).strip()
        outcomes = _parse_junit(package, document)
        if outcomes:
            return _in_declaration_order(package, outcomes)
        return _unreported(package, document)


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


def _in_declaration_order(
    package: SuitePackage,
    outcomes: tuple[TestOutcome, ...],
) -> tuple[TestOutcome, ...]:
    """Print the verdicts in the package's own order, not the reporter's.

    utPLSQL emits `testcase` elements in whatever order it walked the suite tree,
    which is its business and not a contract. Discovery already knows the spec
    order, so the results block, the problem list and the counts all read in the
    one order Jan can follow against the source. A test utPLSQL ran that
    discovery never saw keeps its reported position, at the end.
    """
    position = {test.name.upper(): index for index, test in enumerate(package.tests)}
    unknown = len(position)
    return tuple(sorted(outcomes, key=lambda outcome: position.get(outcome.test.upper(), unknown)))


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


def _row_line(row: dict[str, object]) -> str:
    # The reporter returns a single unnamed column; take whatever it is called
    # rather than pinning the alias, so a driver that upper-cases or renames it
    # cannot silently yield an empty document.
    for value in row.values():
        return "" if value is None else str(value)
    return ""


def _parse_junit(package: SuitePackage, document: str) -> tuple[TestOutcome, ...]:
    if not document:
        return ()
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return ()
    outcomes = []
    for case in root.iter("testcase"):
        result, message = _case_result(case)
        outcomes.append(
            TestOutcome(
                package = package.name,
                test    = _known_test_name(package, case.get("name") or ""),
                result  = result,
                seconds = _seconds(case.get("time")),
                message = message,
            )
        )
    return tuple(outcomes)


def _case_result(case: ElementTree.Element) -> tuple[str, str]:
    for tag, result in (("failure", RESULT_FAILED), ("error", RESULT_ERRORED)):
        node = case.find(tag)
        if node is not None:
            return result, _node_message(node)
    if case.find("skipped") is not None:
        return RESULT_SKIPPED, ""
    return RESULT_PASSED, ""


def _node_message(node: ElementTree.Element) -> str:
    parts = [node.get("message") or "", (node.text or "").strip()]
    return "\n".join(part for part in parts if part)


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _known_test_name(package: SuitePackage, reported: str) -> str:
    """Resolve whatever the reporter called a test back to its procedure name.

    **A JUnit `testcase name` is not an identifier.** utPLSQL puts the `%test`
    *description* there whenever the annotation carries one — `%test(fails on
    purpose)` reports as `fails on purpose` — and falls back to the procedure
    name only for an undescribed test. Printing the reported value verbatim
    therefore prints prose in a column of identifiers, and prose that cannot be
    grepped for in the package source, which is the one thing a reader does
    with a failing test's name.

    `ut_runner.get_suites_info` holds both spellings for every discovered test,
    so the reported value is matched against the name *and* the description and
    the **procedure name** is what comes back. The fallback stays the reported
    string: a test utPLSQL ran but discovery never saw is still worth printing
    under whatever it called itself.
    """
    for test in package.tests:
        if test.name.upper() == reported.upper():
            return test.name
    for test in package.tests:
        if test.description and test.description.upper() == reported.upper():
            return test.name
    return reported


def _unreported(package: SuitePackage, document: str) -> tuple[TestOutcome, ...]:
    """One ERRORED outcome per discovered test when the run reported nothing.

    A suite that produced no parsable result did not pass — it did not run. The
    reporter's raw output rides along as the message, because that is where the
    real cause (an ORA on the producer side, a truncated document) shows up.
    """
    message = document or "the reporter returned no output"
    return tuple(
        TestOutcome(
            package = package.name,
            test    = test.name,
            result  = RESULT_ERRORED,
            message = message,
        )
        for test in package.tests
    )


__all__ = [name for name in globals() if not name.startswith("__")]
