"""Console rendering for the ut3 module.

Four sections, in the order a run produces them: the suites that matched, what
the run is doing while it does it, the detail for anything that did not pass,
and the per-suite tally. The second of those has two shapes and a mode chooses
between them, one dotted progress bar by default (`progress.py`), or the
per-test verdicts under `-verbose`.

Three shapes carry all four. The suites roll-up and the tally are tables because
every value in them is bounded, a package name and counts. The verdicts are
fixed-width status rows grouped under their package, because a run prints one
block per suite as that suite completes and a table cannot be emitted in
pieces. The third shape, everything free-text a run produces, as a status-led
per-record stanza, is `problems.py`, split out at the 20 KB context budget:
tables and rows here, stanzas there.

A test package that cannot run (INVALID, or holding no parsed `%test`) is in
none of the four sections. It is not a suite, and `ut3` reports suites.

How a single cell looks is `cells.py`, so the two tables cannot disagree about
what a zero or an absent measurement renders as; the indents both content
sections lay text on are `layout.py`, so they cannot drift apart.
"""

from __future__ import annotations

from adt_ai.export_db.runner import print_adt_header, print_adt_table
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.ut3.cells import (
    SUMMARY_NUMERIC,
    count_cell,
    coverage_cell,
    module_cell,
    percent_cell,
    seconds_cell,
    test_status_cell,
)
from adt_ai.ut3.grouping import (
    by_module,
    count,
    flatten,
    outcomes_by_package,
    target_packages,
    total_seconds,
)
from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    PackageCoverage,
    SuitePackage,
    TestOutcome,
    coverage_percent,
)
from adt_ai.ut3.layout import DETAIL_INDENT, HEADING_INDENT
from adt_ai.ut3.progress import SuiteProgressBar
from adt_ai.ut3.runner import Ut3Reporter, Ut3Result

_SUITE_COLUMNS = ("SUITE_PACKAGE", "TESTS")

# **`COVERAGE` closes the row, after `TIMER`.** Jan's 2026-08-11 shape: the
# verdicts say whether the suite is green, `TIMER` what it cost, and `COVERAGE`
# how much of the code it actually reached, the three questions in the order a
# reader asks them.
_SUMMARY_COLUMNS = ("SUITE_PACKAGE", "PASS", "FAIL", "ERROR", "TIMER", "COVERAGE")

# `SUMMARY:` again, one row per `ut_module` group instead of per suite package.
# `PACKAGES` and `LINES` are the two columns the per-suite table does not have,
# and they are the pair that makes the rest honest: a group of one and a group of
# nine are the same row without the count, and forty lines and four thousand are
# the same row without the size. They sit together, ahead of the verdicts, because
# both describe the group rather than what this run did to it.
_MODULE_COLUMNS = (
    "MODULE_NAME",
    "PACKAGES",
    "LINES",
    "PASS",
    "FAIL",
    "ERROR",
    "TIMER",
    "COVERAGE",
)


def print_suites(packages: tuple[SuitePackage, ...]) -> None:
    """The matched suites, one row each, printed before anything runs.

    **The header and the column row always print, even with nothing to list.**
    An empty table is a complete answer, it says the command looked and found
    no suite, and it says it in the same shape as a run that found ten. The
    section this replaced printed a four-line troubleshooting lecture instead,
    which buried the one fact the reader wanted behind advice they had not
    asked for. The failure is still carried, by the exit code and by `SUMMARY:`.

    **Only runnable suites are listed.** A package that is INVALID, or that
    utPLSQL parsed no test for, contributes no row and no stanza anywhere else
    either: Jan's instruction is that `ut3` ignores it. It has no test count, so
    a table of suites and their counts has nothing to say about it, and naming it
    below under a heading that reads `ERRORS & FAILURES:` claims a failure the
    command has decided not to have.

    Packages print in the dictionary's A-Z order, which the discovery query
    already imposes.
    """
    print_adt_header("UNIT TESTS SUITES:")
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


def print_package_results(package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
    """One block per suite: the package heading, then a row per test."""
    print_package_heading(package)
    print_test_rows(outcomes)


def print_test_rows(outcomes: tuple[TestOutcome, ...]) -> None:
    """The one row builder both the streamed and the batch render go through.

    A passing row's right-hand text is its elapsed seconds, not the word
    `PASS`; `FAIL`/`ERROR`/`SKIP` still print their status word. See
    `cells.test_status_cell`.
    """
    printer = FixedWidthProgressPrinter(indent=DETAIL_INDENT)
    for outcome in outcomes:
        # begin() lays the label down and status() completes that same row; the
        # pair is the only way to get a labelled fixed-width line.
        printer.begin(outcome.test)
        printer.status(outcome.test, test_status_cell(outcome))
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
    and two in `SUMMARY:`, and the package is what the reader has to go and
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


def print_summary(result: Ut3Result, names: tuple[str, ...] = ()) -> None:
    """The suites table again, with what each suite's tests actually did.

    Same first column as `UNIT TESTS SUITES:` so the two read as before and
    after of one list, then a column per verdict. **A zero renders blank**: a
    column of `0`s competes for the eye with the counts that matter, and the
    thing a reader scans this table for is the row that is not all-passed.

    **There is no `TESTS` column.** Every test lands in exactly one verdict, so
    the total was a fourth number derivable from the other three, and the count
    the reader wanted was already on the `UNIT TESTS SUITES:` row above. The one
    thing it carried alone was the `%disabled` test, which appears in no verdict
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

    **The header names the filter when there is one.** With `-name` passed the
    section reads `SUMMARY FOR <PATTERNS>:`, upper-cased, several patterns joined
    with commas, a roll-up over part of a schema should say so in its own
    heading rather than leaving the reader to remember the command they typed.
    `MODULES:` below keeps its own heading: it is one section down from this one
    and the filter has already been stated.
    """
    print_adt_header(_summary_header(names))
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "PASS"          : count_cell(count(outcomes, RESULT_PASSED)),
                "FAIL"          : count_cell(count(outcomes, RESULT_FAILED)),
                "ERROR"         : count_cell(count(outcomes, RESULT_ERRORED)),
                "TIMER"         : seconds_cell(result.seconds_for(package.name)),
                "COVERAGE"      : coverage_cell(result.coverage.for_package(package.target)),
            }
            for package, outcomes in outcomes_by_package(result)
        ],
        columns = list(_SUMMARY_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def _summary_header(names: tuple[str, ...]) -> str:
    """`SUMMARY:`, or `SUMMARY FOR <PATTERNS>:` when `-name` narrowed the run.

    Upper-cased because every other word in an ADT section header is, and the
    patterns are Oracle identifiers-with-wildcards where case carries no meaning:
    `-name ict_sec%` and `-name ICT_SEC%` select the same suites, so they must
    not print two different headings.

    `-name` is repeatable and multi-value, so the heading joins what was passed
    rather than naming the first pattern and silently dropping the rest.
    """
    if not names:
        return "SUMMARY:"
    return f"SUMMARY FOR {', '.join(name.upper() for name in names)}:"


def print_module_summary(result: Ut3Result) -> None:
    """`SUMMARY:` again, grouped by `ut_module`, the second table of a run.

    **It answers a different question from the table above it.** `SUMMARY:` says
    which suite is red; this says which *area* is. On a schema with ninety suites
    the per-suite table is a list you scroll and this is the one you read, which
    is why it is a table of its own rather than a sort order on the first.

    `PACKAGES` is the group size and `LINES` is how much code that adds up to,
    and together they are what make the rest honest: four failures spread over
    nine suites and four in one are not the same news, and neither are ninety
    percent of forty lines and ninety percent of four thousand.

    **`LINES` counts the packages the group's suites test, not the suites.** It
    is the same deduplicated set `COVERAGE` beside it is computed over, two
    suites testing one package contribute one body once, so the two columns can
    never describe two different sets. A group whose suites pair to nothing has
    no lines to count and blanks, exactly where its `COVERAGE` blanks too.

    **The last row is the whole run, with a blank module name.** A `TOTAL` label
    would be a value in a column of module names, it would sort among them and
    read as one, so the total is placed rather than labelled. A suite whose name
    `ut_module` cannot parse groups at the top, and reads `?` rather than blank:
    two unnamed rows in one table say nothing about which is which, which is the
    defect card `#248` fixed.

    **`COVERAGE` is the group's own figure, over the packages its suites test.**
    Every column on the row describes one set of suites, `COVERAGE` included, so
    a group is never a mix of this run's verdicts and some wider schema's
    coverage. The one shared `coverage_percent` helper computes the groups and
    the total alike, so the total can never disagree with the rows above it, and
    a target Oracle measured nothing for still counts its body lines, which pulls
    the group down in proportion to how much of it went unreached.

    Prints only when `ut_module` is configured. A project without one sees the
    output it saw before this existed, not a table of empty groups.
    """
    grouped = by_module(outcomes_by_package(result), result)
    print_adt_header("MODULES:")
    print_adt_table(
        [_module_row(module_cell(module), rows) for module, rows in grouped]
        + [_module_row("", flatten(grouped))],
        columns = list(_MODULE_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def _module_row(name: str, rows: list[dict[str, object]]) -> dict[str, object]:
    """One `MODULES:` row, a named group, or the unnamed total over every row.

    The two go through one builder so the total can never be a different
    calculation from the rows it sits under. That was already true of `COVERAGE`,
    which has always shared `coverage_percent`; writing the group and the total
    out twice is what let `LINES` be the next column with two ways to be right.
    """
    packages = target_packages(rows)
    return {
        "MODULE_NAME" : name,
        "PACKAGES"    : len(rows),
        "LINES"       : count_cell(sum(package.lines for package in packages)),
        "PASS"        : count_cell(sum(row["passed"] for row in rows)),
        "FAIL"        : count_cell(sum(row["failed"] for row in rows)),
        "ERROR"       : count_cell(sum(row["errored"] for row in rows)),
        "TIMER"       : seconds_cell(total_seconds(rows)),
        "COVERAGE"    : percent_cell(coverage_percent(packages)),
    }


class ConsoleUt3Reporter(Ut3Reporter):
    """Prints the run as it proceeds, never as one dump at the end.

    The suites roll-up lands the moment discovery returns. What follows it is the
    mode's own section, and there are three modes with one section each:

    * default   , `RUNNING TESTS:`, one dotted bar bumped by finished suites.
    * ``verbose``, `TEST RESULTS:`, a package heading before each suite blocks
      and a row per test once its verdict is known.
    * ``silent`` , neither listing, and no bar.

    `-silent` outranks `-verbose` for the reason it outranked `-dense`: two flags
    about one region of the screen, and the one that removes it wins, or the
    quiet flag would print a section it exists to suppress.
    """

    def __init__(
        self,
        silent: bool = False,
        verbose: bool = False,
        *,
        previous_seconds: float = 0.0,
        started_at: float | None = None,
    ) -> None:
        self._silent = silent
        self._verbose = verbose
        self._previous = previous_seconds
        self._started_at = started_at
        self._bar: SuiteProgressBar | None = None
        self.streamed = False

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        if self._silent:
            return
        print_suites(packages)
        if self._verbose:
            return
        # **A run that matched nothing gets no bar.** The empty roll-up above is
        # already the complete answer; a bar over zero units would have to print
        # some percentage beside no work, and every value it could print is a
        # claim rather than a report. `UNIT TESTS SUITES:` still prints its
        # header and column row, because an empty *table* says the same thing in
        # the same shape as a full one, a bar has no such empty form.
        runnable = [package for package in packages if package.runnable]
        if not runnable:
            return
        # The bar moves in suites and is labelled in tests, and both totals are
        # read off the discovery the table above just printed, so `-name`
        # narrows the label and the axis together, and the label is the sum of
        # the `TESTS` column the reader can see two lines up.
        self._bar = SuiteProgressBar(
            len(runnable),
            tests            = sum(len(package.tests) for package in runnable),
            previous_seconds = self._previous,
            started_at       = self._started_at,
        )
        self._bar.begin()
        self.streamed = True

    def suite_begin(self, package: SuitePackage) -> None:
        # Nothing to stream ahead of the work in bar mode: the bar is already on
        # screen at 0%, and it moves on completion, not on start.
        if self._silent or not self._verbose:
            return
        if not self.streamed:
            print_results_header()
            self.streamed = True
        print_package_heading(package)

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        if self._silent:
            return
        if self._bar is not None:
            self._bar.advance()
            return
        if self._verbose:
            print_test_rows(outcomes)

    def close(self) -> None:
        """End the bar's row, once, after the last suite.

        Only the bar owes this: a per-test block closes itself with its own blank
        line, while the bar's row is rewritten with a carriage return and carries
        no newline at all until something ends it.
        """
        if self._bar is None:
            return
        self._bar.close()


__all__ = [name for name in globals() if not name.startswith("__")]
