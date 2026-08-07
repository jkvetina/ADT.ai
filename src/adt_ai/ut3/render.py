"""Console rendering for the ut3 module.

Four sections, in the order a run produces them: the suites that matched, the
per-test verdicts as each suite finishes, the detail for anything that did not
pass, and the per-suite tally.

Two shapes carry that. The suites roll-up and the tally are tables because
every value in them is bounded — a package name and counts. The verdicts are
fixed-width status rows grouped under their package, because a run prints one
block per suite as that suite completes and a table cannot be emitted in
pieces. Everything free-text a run produces — a failure message, an ORA stack —
is a per-record stanza: status-led heading first, wrapped text beneath it, never
a table column that would be sized to its widest sentence.

A `_UT` package that cannot run — INVALID, or holding no parsed `%test` — is in
none of the four sections. It is not a suite, and `ut3` reports suites.
"""

from __future__ import annotations

import textwrap

from adt_ai.export_db.runner import print_adt_header, print_adt_table
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    CoverageReport,
    SuitePackage,
    TestOutcome,
)
from adt_ai.ut3.runner import Ut3Reporter, Ut3Result

_SUITE_COLUMNS = ("SUITE_PACKAGE", "TESTS")
_SUMMARY_COLUMNS = ("SUITE_PACKAGE", "PASSED", "FAILED", "ERRORED", "TIMER")
_COVERAGE_COLUMNS = ("PACKAGE", "LINES", "PASSED", "FAILED", "ERRORED", "COVERAGE")

# The roll-up `-coverage` closes with: **one row, for the whole schema**, because
# it answers one question. It shipped as three — one per table above it plus a
# total — which made the reader re-read a breakdown they had just scrolled past
# to get at the single line they came for.
_COVERAGE_SUMMARY_COLUMNS = ("PACKAGES", "LINES", "COVERED", "COVERAGE")

# **The two tables do not carry the same columns, and that is the point.** The
# split is on "did anything execute this", so in the second half every column the
# split determines is constant: `COVERAGE` could only ever read `-`, and the
# verdicts could only ever describe tests that covered nothing. A constant column
# is not a column — it is a wide empty stripe between the two facts that do vary,
# which are which package and how much code is in it. Making the halves
# symmetrical was symmetry for its own sake.
_NO_COVERAGE_COLUMNS = ("PACKAGE", "LINES")

# COVERAGE is a number and lines up on its units digit like one. Detection reads
# the cells with `isnumeric()`, which rejects the decimal point, so `75.0` is no
# more numeric to the sniffer than `75%` was — the column stays declared. `TIMER`
# is declared for exactly the same reason: `3.0` sniffs as text.
_COVERAGE_NUMERIC = ("COVERAGE",)
_SUMMARY_NUMERIC = ("TIMER",)

# One grid for both content sections. `TEST RESULTS:` and `ERRORS & FAILURES:`
# have the same shape — a heading naming what follows, then its detail — so a
# package heading sits where a stanza heading sits and a test row sits where a
# wrapped message line sits. Four spaces against two: the detail reads as
# nested under its heading without starting a third of a tab-stop in.
_HEADING_INDENT = "  "
_DETAIL_INDENT = "    "

# The message body wraps inside the terminal; only an unbreakable token (an
# ORA stack line, a path) is allowed to overhang.
_MESSAGE_WIDTH = 78


def print_suites(packages: tuple[SuitePackage, ...]) -> None:
    """The matched suites, one row each, printed before anything runs.

    **The header and the column row always print, even with nothing to list.**
    An empty table is a complete answer — it says the command looked and found
    no suite — and it says it in the same shape as a run that found ten. The
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

    Only the heading can be streamed ahead of the work — utPLSQL runs a whole
    suite in one `ut.run` call, so no individual test result exists until the
    call returns. Printing the heading first is what puts the visible pause on
    the suite being run instead of on the section header above it.
    """
    print(f"{_HEADING_INDENT}{package.name}", flush=True)


def print_package_results(package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
    """One block per suite: the package heading, then a row per test."""
    print_package_heading(package)
    print_test_rows(outcomes)


def print_test_rows(outcomes: tuple[TestOutcome, ...]) -> None:
    """The one row builder both the streamed and the batch render go through."""
    printer = FixedWidthProgressPrinter(indent=_DETAIL_INDENT)
    for outcome in outcomes:
        # begin() lays the label down and status() completes that same row; the
        # pair is the only way to get a labelled fixed-width line.
        printer.begin(outcome.test)
        printer.status(outcome.test, outcome.result)
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


def print_problems(result: Ut3Result) -> None:
    """One stanza per non-passing test: what happened, then why.

    **The status leads the heading** — `ERROR > A_UT.TEST_LABELS#LOOKUP_RAISES`,
    not the name with a trailing verdict. The reader is scanning for the ones
    that errored, and a status word parked at the end of a package-qualified
    identifier is behind the longest, most variable part of the line.

    **Every stanza gets a blank line above it**, the first one included, so the
    section reads as a list of records rather than one wall of text under a
    header.

    A package that could not run is not here and is not anywhere: an INVALID or
    unparsed `_UT` package is ignored outright.
    """
    problems = [
        outcome
        for outcome in result.outcomes
        if outcome.result in {RESULT_FAILED, RESULT_ERRORED}
    ]
    if not problems:
        return
    print_adt_header("ERRORS & FAILURES:")
    for outcome in problems:
        print()
        print(f"{_HEADING_INDENT}{outcome.result} > {outcome.package}.{outcome.test}")
        for line in _wrapped_lines(outcome.message):
            print(line)
    print()


def print_summary(result: Ut3Result) -> None:
    """The suites table again, with what each suite's tests actually did.

    Same first column as `UNIT TESTS SUITES:` so the two read as before and
    after of one list, then a column per verdict. **A zero renders blank**: a
    column of `0`s competes for the eye with the counts that matter, and the
    thing a reader scans this table for is the row that is not all-passed.

    **There is no `TESTS` column.** Every test lands in exactly one verdict, so
    the total was a fourth number derivable from the other three — and the count
    the reader wanted was already on the `UNIT TESTS SUITES:` row above. The one
    thing it carried alone was the `%disabled` test, which appears in no verdict
    column; its own result row still reads `SKIPPED`.

    **`TIMER` closes the row** with that suite's own seconds, which is what turns
    the table from a tally into something that explains a slow run. It is the one
    column a zero does not blank out of — see `_seconds_cell`.
    """
    print_adt_header("SUMMARY:")
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "PASSED"        : _count_cell(_count(outcomes, RESULT_PASSED)),
                "FAILED"        : _count_cell(_count(outcomes, RESULT_FAILED)),
                "ERRORED"       : _count_cell(_count(outcomes, RESULT_ERRORED)),
                "TIMER"         : _seconds_cell(result.seconds_for(package.name)),
            }
            for package, outcomes in _outcomes_by_package(result)
        ],
        columns = list(_SUMMARY_COLUMNS),
        numeric = _SUMMARY_NUMERIC,
    )


def _outcomes_by_package(
    result: Ut3Result,
) -> list[tuple[SuitePackage, tuple[TestOutcome, ...]]]:
    grouped = []
    for package in result.packages:
        if not package.runnable:
            continue
        outcomes = tuple(
            outcome for outcome in result.outcomes if outcome.package == package.name
        )
        grouped.append((package, outcomes))
    return grouped


def _count(outcomes: tuple[TestOutcome, ...], result: str) -> int:
    return sum(1 for outcome in outcomes if outcome.result == result)


def _count_cell(count: int) -> object:
    return count or ""


def _seconds_cell(seconds: float | None) -> str:
    """One decimal, always present — and `0.0` is a figure, not a blank.

    The verdict columns blank their zeros because a `0` there competes for the
    eye with the counts that matter. A timing zero is the opposite: a suite that
    finished inside a tenth of a second *was* measured, and an empty cell would
    claim it was not. Only a suite that never ran has nothing to print.

    Fixed precision is what makes the column scannable, the same argument
    `COVERAGE` settled: with trailing zeros stripped, `3`, `0.3` and `12.5` end
    at three different offsets and the digits do not stack even flush right.
    """
    return "" if seconds is None else f"{seconds:.1f}"


def print_coverage(report: CoverageReport) -> None:
    """Two tables: what the run covered, then what it did not.

    **The second table is the whole reason the section exists.** A report that
    only listed measured packages would omit exactly the package the reader
    opened it to find, and mixing the two into one list buries the gap among the
    rows that are fine. Splitting them makes `NO CODE COVERAGE:` a work list.

    A package lands in the second table when nothing executed it — whether
    Oracle measured it at `0%`, measured nothing at all, or could not instrument
    it at all.

    **The two tables do not carry the same columns.** Everything the split
    determines is constant in the second half, so what survives there is
    `PACKAGE` and `LINES`: which package, and how much code is sitting untested
    in it. The first half adds the verdicts and the figure, both of which need a
    run behind them to mean anything.

    `PASSED`, `FAILED` and `ERRORED` come from the package's `_UT` partner by
    name, so a package covered by another suite shows a real percentage and no
    verdicts — see `runner._verdicts_by_target`.

    Packages print in the dictionary's A-Z order, which the query already
    imposes.

    **The `CODE COVERAGE:` header is not printed here.** It goes out the moment
    discovery returns — see `print_coverage_header` — because everything between
    that point and this call is a silent suite run.
    """
    print_adt_table(
        [
            {
                "PACKAGE"  : package.name,
                "LINES"    : package.lines or "",
                "PASSED"   : package.passed or "",
                "FAILED"   : package.failed or "",
                "ERRORED"  : package.errored or "",
                "COVERAGE" : _coverage_cell(package),
            }
            for package in report.covered
        ],
        columns = list(_COVERAGE_COLUMNS),
        numeric = _COVERAGE_NUMERIC,
    )
    print_adt_header("NO CODE COVERAGE:")
    print_adt_table(
        [
            {
                "PACKAGE" : package.name,
                "LINES"   : package.lines or "",
            }
            for package in report.uncovered
        ],
        columns = list(_NO_COVERAGE_COLUMNS),
    )


def print_coverage_header() -> None:
    """The section header, printed before the suites run rather than after.

    Under `-coverage` the run is silent by design — the report is the answer, and
    the per-suite chatter is what the flag was passed to remove. That left the
    connection block as the last thing on screen for the tens of seconds a real
    schema takes, which reads as a hang on connecting rather than as work in
    progress. Naming the section first puts the pause under the work it is being
    spent on.

    `print_adt_header` writes through the shared stdout tracker, which flushes
    the visible body of every write immediately, so nothing extra is needed to
    get this onto the terminal ahead of the blocking call.
    """
    print_adt_header("CODE COVERAGE:")


def print_coverage_summary(report: CoverageReport) -> None:
    """The roll-up that closes `-coverage`: the whole schema on one line.

    `PACKAGES` and `LINES` are the entire listing — both tables — so the row is
    the size of the codebase, not of its tested part; a reader who wants the
    split has the two tables immediately above. `COVERED` is the source lines
    that actually executed, and `COVERAGE` the same block figure the per-package
    column carries.

    **`COVERED` over `LINES` is deliberately not `COVERAGE`, and the three are
    labelled so nobody has to work that out by dividing.** They measure
    different things: `LINES` counts every row of every package body — comments,
    blanks and declarations, none of which Oracle instruments — `COVERED` counts
    instrumented lines that ran, and `COVERAGE` is covered blocks over measured
    blocks. Forcing them to agree would mean either dropping the line counts or
    computing a percentage against a denominator that includes code no coverage
    tool ever looks at.

    **`COVERAGE` blanks when nothing was measured**, exactly as a package row's
    does: the package and line counts are true whether or not a test ever ran,
    but a percentage is a claim about collected data. `COVERED` still reads `0`
    there — nothing executed is a measured zero, not an absent measurement.
    """
    packages = report.packages
    print_adt_header("SUMMARY:")
    print_adt_table(
        [
            {
                "PACKAGES" : _count_cell(len(packages)),
                "LINES"    : _count_cell(sum(package.lines for package in packages)),
                "COVERED"  : sum(package.lines_covered for package in packages),
                "COVERAGE" : _percent_cell(report.percent),
            }
        ],
        columns = list(_COVERAGE_SUMMARY_COLUMNS),
        numeric = _COVERAGE_NUMERIC,
    )


def _percent_cell(percent: float | None) -> str:
    # Same shape as a package row's own figure, so the roll-up stacks under the
    # column it summarises rather than reading as a different kind of number.
    return "" if percent is None else f"{percent:.1f}"


def _coverage_cell(package) -> str:
    """Only ever called for a package with a real figure.

    The `-` and `NATIVE` cases this used to carry are gone with the column they
    lived in: a package with no measurement is in the other table now, where the
    absence *is* the row rather than something a cell has to spell.

    **One decimal place, always present, and no `%`.** The header names the
    unit once, so repeating it on every row bought nothing and cost a character
    of width on each. Variable precision cost more than that: stripping the
    trailing zeros ended `100`, `75` and `53.1` at three different offsets, so
    even flush right the figures did not stack under each other — and a column
    read by scanning down it for the low numbers is exactly the column that has
    to stack.
    """
    return f"{package.percent:.1f}"


class ConsoleUt3Reporter(Ut3Reporter):
    """Prints the results as the run proceeds, never as one dump at the end.

    The suites roll-up lands the moment discovery returns; then each package's
    heading lands before that suite blocks and its test rows once the verdict is
    known. A reader watching a slow run sees the suite currently being executed,
    not a silent terminal followed by everything at once.

    `-coverage` keeps the same principle with nothing left to stream: the run is
    deliberately quiet there, so what lands at discovery is the section header
    for the report that run is producing.
    """

    def __init__(self, silent: bool = False, coverage: bool = False) -> None:
        self._silent = silent
        self._coverage = coverage
        self.streamed = False

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        if self._coverage:
            print_coverage_header()
            return
        if self._silent:
            return
        print_suites(packages)

    def suite_begin(self, package: SuitePackage) -> None:
        if self._silent:
            return
        if not self.streamed:
            print_results_header()
            self.streamed = True
        print_package_heading(package)

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        if self._silent:
            return
        print_test_rows(outcomes)


def _wrapped_lines(message: str) -> list[str]:
    if not message:
        return []
    lines: list[str] = []
    for paragraph in message.splitlines():
        if not paragraph.strip():
            continue
        lines.extend(
            textwrap.wrap(
                paragraph.strip(),
                width              = _MESSAGE_WIDTH,
                initial_indent     = _DETAIL_INDENT,
                subsequent_indent  = _DETAIL_INDENT,
                break_long_words   = False,
                break_on_hyphens   = False,
            )
            or [f"{_DETAIL_INDENT}{paragraph.strip()}"]
        )
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
