"""Turning utPLSQL's JUnit report into ``TestOutcome`` records.

Split out of ``runner.py``, which had grown past the repo's 20 KB per-file
context budget (``tests/contracts/test_context_file_size.py``). The seam is a
real one rather than a size cut: everything here reads the reporter's document
and knows nothing about gateways, discovery, or coverage, so ``runner`` calls it
exactly once — from ``_run_suite``, on the rows one ``ut.run`` returned.

The recurring theme is that **the report is utPLSQL's, not a contract**. What it
calls a test is a description as often as a name, and the order it emits cases in
is whatever order it walked the tree. Both are translated back into the schema's
own vocabulary here so nothing downstream has to know the reporter exists.
"""

from __future__ import annotations

from xml.etree import ElementTree

from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    RESULT_SKIPPED,
    SuitePackage,
    TestOutcome,
)


def row_line(row: dict[str, object]) -> str:
    # The reporter returns a single unnamed column; take whatever it is called
    # rather than pinning the alias, so a driver that upper-cases or renames it
    # cannot silently yield an empty document.
    for value in row.values():
        return "" if value is None else str(value)
    return ""


def parse_junit(package: SuitePackage, document: str) -> tuple[TestOutcome, ...]:
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


def in_declaration_order(
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


def unreported(package: SuitePackage, document: str) -> tuple[TestOutcome, ...]:
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


__all__ = [name for name in globals() if not name.startswith("__")]
