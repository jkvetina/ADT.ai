"""Turning utPLSQL's JUnit report into ``TestOutcome`` records.

Split out of ``runner.py``, which had grown past the repo's 20 KB per-file
context budget (``tests/contracts/test_context_file_size.py``). The seam is a
real one rather than a size cut: everything here reads the reporter's document
and knows nothing about gateways, discovery, or coverage, so ``runner`` calls it
exactly once, from ``_run_suite``, on the rows one ``ut.run`` returned.

The recurring theme is that **the report is utPLSQL's, not a contract**. What it
calls a test is a description as often as a name, and the order it emits cases in
is whatever order it walked the tree. Both are translated back into the schema's
own vocabulary here so nothing downstream has to know the reporter exists.

Not a contract in the safety sense either: the document arrives from whatever
schema ``ut.run`` was pointed at, which is routinely one ADT.ai does not own, so
``_declares_a_dtd`` refuses a document carrying a DTD (``#706``).
"""

from __future__ import annotations

from xml.etree import ElementTree
from xml.parsers import expat

from adt_ai.ut.inventory import (
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


class _ForbiddenDoctype(Exception):
    """A DTD was declared. Raised out of an expat handler, caught in `parse_junit`."""


class _PrologEnded(Exception):
    """The root element started, so there was no doctype. Control flow, not an error."""


def _declares_a_dtd(document: str) -> bool:
    """Whether `document`'s prolog declares a DTD (`#706`).

    Python's own documentation says the stdlib XML parser is not hardened
    against a hostile document, and this one arrives from whatever schema
    `ut.run` was pointed at rather than from a repository ADT.ai owns -- narrow,
    because it takes a schema that can already run PL/SQL, and not nothing,
    because that is not always the same party.

    **Every attack behind that warning needs an entity declaration, and an
    entity declaration needs a DTD.** Unbounded expansion (billion laughs), the
    quadratic blowup, and retrieval of an external entity all begin in the
    doctype's subset, so refusing the doctype closes the class rather than one
    instance of it. The refusal lands on the DECLARATION, before the subset is
    read, which is what also covers an external subset -- its entities live in a
    file the parser would have to fetch in order to see them.

    **Expat rather than a text scan**, because "does this document declare a
    doctype" is a lexical question and `<!DOCTYPE` inside a comment or a CDATA
    section is not one. Expat's own lexer answers it, and the pass stops at the
    root element's start tag, so it reads the prolog and nothing else.

    `defusedxml` reaches the same handler through `ElementTree.XMLParser`, which
    is why this does not: CPython's C accelerator exposes no expat handle on
    that object (measured on 3.14 -- `XMLParser` has no `parser` attribute), so
    the documented route is unavailable here whether or not the dependency is
    taken. A new runtime dependency is a supply-chain decision in any case, and
    this needs none.

    An unparsable document answers `False` and is left to `ElementTree` below,
    so the real `ParseError` still comes from the one place that used to raise
    it rather than from two.
    """
    parser = expat.ParserCreate()

    def _doctype(name: str, system_id: object, public_id: object, has_subset: bool) -> None:
        raise _ForbiddenDoctype(name)

    def _root(name: str, attributes: object) -> None:
        raise _PrologEnded

    parser.StartDoctypeDeclHandler = _doctype
    parser.StartElementHandler = _root
    try:
        parser.Parse(document, True)
    except _ForbiddenDoctype:
        return True
    except (_PrologEnded, expat.ExpatError):
        pass
    return False


def parse_junit(package: SuitePackage, document: str) -> tuple[TestOutcome, ...]:
    if not document:
        return ()
    # A refused document is answered exactly like an unparsable one, which sends
    # `_run_suite` to `unreported`: one ERROR per discovered test, carrying the
    # document as the message. A report that cannot be trusted is not a pass,
    # and the operator gets to see what arrived.
    if _declares_a_dtd(document):
        return ()
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return ()
    outcomes = []
    # Which discovered tests a case has already claimed, by position in
    # `package.tests`, which is their identity here. It spans the whole document
    # because the collision it prevents is between two cases (`#670`).
    resolved: set[int] = set()
    for case in root.iter("testcase"):
        result, message = _case_result(case)
        outcomes.append(
            TestOutcome(
                package = package.name,
                test    = _known_test_name(package, case.get("name") or "", resolved),
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
    """One ERROR outcome per discovered test when the run reported nothing.

    A suite that produced no parsable result did not pass, it did not run. The
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


def _known_test_name(package: SuitePackage, reported: str, resolved: set[int]) -> str:
    """Resolve whatever the reporter called a test back to its procedure name.

    **A JUnit `testcase name` is not an identifier.** utPLSQL puts the `%test`
    *description* there whenever the annotation carries one, `%test(fails on
    purpose)` reports as `fails on purpose`, and falls back to the procedure
    name only for an undescribed test. Printing the reported value verbatim
    therefore prints prose in a column of identifiers, and prose that cannot be
    grepped for in the package source, which is the one thing a reader does
    with a failing test's name.

    `ut_runner.get_suites_info` holds both spellings for every discovered test,
    so the reported value is matched against the name *and* the description and
    the **procedure name** is what comes back. The fallback stays the reported
    string: a test utPLSQL ran but discovery never saw is still worth printing
    under whatever it called itself.

    **A description is not unique, so a match is CONSUMED** (`#670`). Nothing
    stops two `%test` annotations in one package from carrying the same text,
    and copy-paste makes it ordinary. Scanning the whole list per case resolved
    both of them to whichever declares first, so the results block printed that
    one name twice, with the second case's verdict, and the other test vanished:
    a red run could read green under a name that had passed. `resolved` holds
    the positions already claimed, so the second case takes the next test with
    that description and only a genuinely exhausted list falls back to the
    reported string. Cases arrive in the reporter's order and the list is in
    declaration order, so the pairing is a guess where the descriptions collide,
    but it is a guess that keeps every test on screen exactly once.
    """
    for index, test in enumerate(package.tests):
        if index not in resolved and test.name.upper() == reported.upper():
            resolved.add(index)
            return test.name
    for index, test in enumerate(package.tests):
        if (
            index not in resolved
            and test.description
            and test.description.upper() == reported.upper()
        ):
            resolved.add(index)
            return test.name
    return reported


__all__ = [name for name in globals() if not name.startswith("__")]
