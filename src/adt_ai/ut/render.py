"""Console rendering for the ut module.

Four sections, in the order a run produces them: the suites that matched, what
the run is doing while it does it, the detail for anything that did not pass,
and the per-suite tally. The first two belong to a mode. `-verbose` prints the
suites roll-up and then a verdict per test; a default run prints neither and
watches one dotted progress bar instead (`progress.py`); `-silent` prints none
of them. The last two print in every mode.

**The run-wide roll-ups are next door in `rollup.py`**, split off at the same
context budget as everything else here: `SUMMARY PER MODULE:`, and the single
`RESULTS:` row `-compact` puts in place of both tallies. Their unit is the run,
where every shape in this file is a suite or a test, and they share one row
builder so the short form and the long one cannot disagree. `-compact` reaches
neither the third section nor the gate below it, so the flag makes a green run
short rather than a red one unreadable, the line `-silent` already draws.

Three shapes carry all four. The suites roll-up and the tally are tables because
every value in them is bounded, a package name and counts. The verdicts are
fixed-width status rows grouped under their package, because a run prints one
block per suite as that suite completes and a table cannot be emitted in
pieces. The third shape, everything free-text a run produces, as a status-led
per-record stanza, is `problems.py`, split out at the 20 KB context budget:
tables and rows here, stanzas there.

**Nothing here knows what time it is.** Every function below turns a finished
value into text, so a caller can render the same run twice and get the same
bytes. What speaks *while* the run is still working, the phase labels and the
streaming reporter that lays them down before each blocking call, is
`reporter.py`, split out at the same budget and along that seam.

A test package that cannot run (INVALID, or holding no parsed `%test`) is in
none of the four sections. It is not a suite, and `ut` reports suites.

How a single cell looks is `cells.py`, so the two tables cannot disagree about
what a zero or an absent measurement renders as; the indents both content
sections lay text on are `layout.py`, so they cannot drift apart.
"""

from __future__ import annotations

from adt_ai.shared.announce import mark_announced
from adt_ai.shared.progress import FixedWidthProgressPrinter, print_adt_header
from adt_ai.shared.tables import print_adt_table
from adt_ai.ut.cells import (
    SUMMARY_NUMERIC,
    count_cell,
    coverage_cell,
    percent_cell,
    seconds_cell,
    test_status_cell,
)
from adt_ai.ut.grouping import count, outcomes_by_package
from adt_ai.ut.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    PackageCoverage,
    SuitePackage,
    SuiteTest,
    TestOutcome,
)
from adt_ai.ut.layout import DETAIL_INDENT, HEADING_INDENT
from adt_ai.ut.runner import Ut3Result

_SUITE_COLUMNS = ("SUITE_PACKAGE", "TESTS")

# **The two roll-ups are one pair, named for what each groups.** They carry the
# same columns over the same run and differ only in how the rows are cut, so the
# headings are what tell them apart, and a heading that named the run's `-name`
# filter instead would leave that difference to be worked out from a column. The
# filter is stated once, by the section the run happened under (`progress.py`).
SUITES_TITLE = "UNIT TESTS SUITES:"
SUMMARY_TITLE = "SUMMARY PER SUITE:"

# **`COVERAGE` closes the row, after `TIMER`.** Jan's 2026-08-11 shape: the
# verdicts say whether the suite is green, `TIMER` what it cost, and `COVERAGE`
# how much of the code it actually reached, the three questions in the order a
# reader asks them.
_SUMMARY_COLUMNS = ("SUITE_PACKAGE", "PASS", "FAIL", "ERROR", "TIMER", "COVERAGE")


def print_suites(packages: tuple[SuitePackage, ...]) -> None:
    """The matched suites, one row each, printed before anything runs.

    **`-verbose` output only** (Jan, card `#348`): a default run watches the bar
    and this table said, at ninety rows on `APP_OWNER`, what the bar's `N TESTS`
    label says in one. The mode that asks for a row per test is the mode that
    wants the list of what will produce them.

    **Within that mode the header and the column row always print, even with
    nothing to list.** An empty table is a complete answer, it says the command
    looked and found no suite, and it says it in the same shape as a run that
    found ten. The section this replaced printed a four-line troubleshooting
    lecture instead, which buried the one fact the reader wanted behind advice
    they had not asked for. The failure is still carried, by the exit code and by
    `SUMMARY PER SUITE:`, which is what carries it on a default run.

    **Only runnable suites are listed.** A package that is INVALID, or that
    utPLSQL parsed no test for, contributes no row and no stanza anywhere else
    either: Jan's instruction is that `ut` ignores it. It has no test count, so
    a table of suites and their counts has nothing to say about it, and naming it
    below under a heading that reads `ERRORS & FAILURES:` claims a failure the
    command has decided not to have.

    Packages print in the dictionary's A-Z order, which the discovery query
    already imposes.

    **The header and the rows are separable**, because the header is what the
    reader looks at while discovery blocks and the rows are what discovery
    returns (`#379`). The streaming reporter prints the two halves either side
    of that read; this pair is what every other caller wants.
    """
    print_suites_header()
    print_suites_rows(packages)


def print_suites_header() -> None:
    """`UNIT TESTS SUITES:` alone, so it can lead the read that fills it."""
    print_adt_header(SUITES_TITLE)


def print_suites_rows(packages: tuple[SuitePackage, ...]) -> None:
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "TESTS"         : len(package.tests),
            }
            for package in packages
            if package.runnable
        ],
        columns=list(_SUITE_COLUMNS),
    )


def print_results_header() -> None:
    # The blank belongs to the header, not to the first package block: a suite
    # block is opened by its own name and closed by a blank line, so the section
    # is spaced like every table without the first block being a special case.
    print_adt_header("TEST RESULTS:")
    print()


def print_package_heading(package: SuitePackage) -> None:
    """The package name alone, flushed, before that suite's work starts.

    Only the heading can be streamed ahead of the work, utPLSQL runs a whole
    suite in one `ut.run` call, so no individual test result exists until the
    call returns. Printing the heading first is what puts the visible pause on
    the suite being run instead of on the section header above it.
    """
    print(f"{HEADING_INDENT}{package.name}", flush=True)
    # It ends its own line, so the cursor cannot tell it apart from a result
    # row, and it has to say what it is: this heading names the suite that is
    # about to run and is the only thing on screen while it does (`#360`).
    mark_announced()


def print_package_results(package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
    """One block per suite: the package heading, then a row per test."""
    print_package_heading(package)
    print_test_rows(outcomes, tests=package.tests)


def print_test_rows(
    outcomes: tuple[TestOutcome, ...],
    close_block: bool = True,
    tests: tuple[SuiteTest, ...] = (),
) -> None:
    """The one row builder both the streamed and the batch render go through.

    A passing row's right-hand text is its elapsed seconds, not the word
    `PASS`; `FAIL`/`ERROR`/`SKIP` still print their status word. See
    `cells.test_status_cell`.

    ``close_block=False`` withholds the blank line that ends the suite's block,
    for the streaming reporter, which owes that blank before the *next* package
    heading and at the end of the run instead. Same bytes, later: the blank is
    what retires the heading's claim on the screen, and the coverage read runs
    after the last block (`#372`, `#359`).

    ``tests`` is the suite's discovered tests, and it earns its place with one
    line: a `%disabled(<reason>)` test prints its reason under its own `SKIP`
    row (`#670`). Discovery has fetched that reason since the command was
    written and no section had ever shown it, so a row said a test did not run
    and left the reader to open the package and find out why. It is a
    continuation line rather than a cell because a reason is prose, and prose in
    a fixed-width column is what SOP §Console output contract calls free text in
    a table column. Omit ``tests`` and the rows print exactly as before, which
    is what a caller holding only outcomes gets.
    """
    reasons = {
        test.name.upper(): test.disabled_reason
        for test in tests
        if test.disabled and test.disabled_reason
    }
    cells = [test_status_cell(outcome) for outcome in outcomes]
    # **The block's own widest value, not the printer's default reservation.**
    # A streamed label is on screen before its value exists, so the room the
    # value will need is reserved rather than measured (`#436`); reserving the
    # generic eleven columns here would take eight of them off every test name
    # to hold `0.0`. The whole block is known before the first row prints, so
    # the reservation can be exact and the rows still stack.
    printer = FixedWidthProgressPrinter(
        indent      = DETAIL_INDENT,
        value_width = max((len(cell) for cell in cells), default=1),
    )
    for outcome, cell in zip(outcomes, cells, strict=True):
        # begin() lays the label down and status() completes that same row; the
        # pair is the only way to get a labelled fixed-width line.
        printer.begin(outcome.test)
        printer.status(outcome.test, cell)
        reason = reasons.get(outcome.test.upper())
        if reason:
            # Two spaces past the row it explains, so it reads as that row's
            # continuation and never as another test.
            print(f"{DETAIL_INDENT}  {reason}")
    if close_block:
        print()


def print_results(result: Ut3Result) -> None:
    """Batch fallback for a run that never drove the streaming reporter.

    It renders through the same block and row builders, so the two paths are
    byte-for-byte identical; the console always streams, so this is what prints
    the section when nothing ran at all.
    """
    print_results_header()
    for package in result.packages:
        outcomes = tuple(
            outcome for outcome in result.outcomes if outcome.package == package.name
        )
        if outcomes:
            print_package_results(package, outcomes)


def print_coverage_gate(packages: tuple[PackageCoverage, ...], threshold: float) -> None:
    """The packages that failed `-gate`, worst first, printed after the report.

    **The report prints in full first and this closes it.** A gate that replaced
    the numbers with a verdict would be unusable: the reason a package is under
    the bar is in the tables above, and the exit code is what a pipeline reads.

    `PACKAGE` names the code, not the suite that tests it. The figure is a
    property of the package, two suites testing one package print one row here
    and two in `SUMMARY PER SUITE:`, and the package is what the reader has to go and
    cover.
    """
    if not packages:
        return
    print_adt_header(f"COVERAGE BELOW {threshold:.1f}:")
    print_adt_table(
        [
            {"PACKAGE": package.name, "COVERAGE": percent_cell(package.percent)}
            for package in packages
        ],
        columns = ["PACKAGE", "COVERAGE"],
        numeric = ("COVERAGE",),
    )


def print_summary(result: Ut3Result) -> None:
    """The suites table again, with what each suite's tests actually did.

    Same first column as `UNIT TESTS SUITES:` so that under `-verbose` the two
    read as before and after of one list, then a column per verdict. **A zero
    renders blank**: a column of `0`s competes for the eye with the counts that
    matter, and the thing a reader scans this table for is the row that is not
    all-passed.

    **There is no `TESTS` column.** Every test lands in exactly one verdict, so
    the total is a fourth number derivable from the other three. The one thing it
    would carry alone is the `%disabled` test, which appears in no verdict
    column; its own result row still reads `SKIP`.

    **`TIMER` carries that suite's own seconds**, which is what turns the table
    from a tally into something that explains a slow run. It is one of the two
    columns a zero does not blank out of, see `seconds_cell`.

    **`COVERAGE` closes the row with the figure for the package that suite
    tests**, paired through `ut_match`. It is a property of the package, not of
    the suite, so a suite the expression cannot pair, or one whose target Oracle
    never instrumented, reads blank rather than `0.0`; see `coverage_cell`. Two
    suites that test the same package therefore print the same figure, because
    block coverage records which blocks ran and never which test ran them.

    **The header names what the table groups, never what the run covered.**
    `SUMMARY PER SUITE:` is one row per suite, and `SUMMARY PER MODULE:` below it
    is the same run grouped the other way, so the two headings read as one pair
    and a reader who scrolled past the tables knows which is which. The `-name`
    filter is stated once, by the `RUNNING TESTS FOR <PATTERNS>:` heading the run
    happened under (`progress.py`); restating it on a table that shows only what
    ran would be the second and worse telling of something already told.

    **The header and the rows are separable**, and this is the pair `#379` was
    filed on: the `COVERAGE` column is what the run reads Oracle's profiler for,
    a wait Jan measured at 9.9 seconds of a 19.3 second run, and it used to
    happen with the whole table still unprinted and the cursor parked on a
    progress row reading `100%  0:00:00`. The header goes above the read now and
    the rows fill in behind it.
    """
    print_summary_header()
    print_summary_rows(result)


def print_summary_header() -> None:
    """`SUMMARY PER SUITE:` alone, so it can lead the coverage read."""
    print_adt_header(SUMMARY_TITLE)


def print_summary_rows(result: Ut3Result) -> None:
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "PASS"          : count_cell(count(outcomes, RESULT_PASSED)),
                "FAIL"          : count_cell(count(outcomes, RESULT_FAILED)),
                "ERROR"         : count_cell(count(outcomes, RESULT_ERRORED)),
                "TIMER"         : seconds_cell(result.seconds_for(package.name)),
                # `paired` is the suite's own resolved target, which is what
                # separates "this suite measures nothing knowable" from "the
                # package it measures collected nothing" (`#436`).
                "COVERAGE"      : coverage_cell(
                    result.coverage.for_package(package.target),
                    paired = bool(package.target),
                ),
            }
            for package, outcomes in outcomes_by_package(result)
        ],
        columns = list(_SUMMARY_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


__all__ = [
    "DETAIL_INDENT",
    "FixedWidthProgressPrinter",
    "HEADING_INDENT",
    "PackageCoverage",
    "RESULT_ERRORED",
    "RESULT_FAILED",
    "RESULT_PASSED",
    "SUITES_TITLE",
    "SUMMARY_NUMERIC",
    "SUMMARY_TITLE",
    "SuitePackage",
    "SuiteTest",
    "TestOutcome",
    "Ut3Result",
    "_SUITE_COLUMNS",
    "_SUMMARY_COLUMNS",
    "annotations",
    "count",
    "count_cell",
    "coverage_cell",
    "mark_announced",
    "outcomes_by_package",
    "percent_cell",
    "print_adt_header",
    "print_adt_table",
    "print_coverage_gate",
    "print_package_heading",
    "print_package_results",
    "print_results",
    "print_results_header",
    "print_suites",
    "print_suites_header",
    "print_suites_rows",
    "print_summary",
    "print_summary_header",
    "print_summary_rows",
    "print_test_rows",
    "seconds_cell",
    "test_status_cell",
]
